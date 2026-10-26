# Legacy CAP corpus audit

## Scope and inventory

The reference set contains 140 pairs selected conservatively from the local
folders:

- `Cap/`: legacy binary Capella files with an exact `.cap` extension.
- `Pdf/`: PDFs whose embedded PDF `Title` ends in `.cap` and matches one
  unique CAP basename, case-insensitively.

The repository contains only the manifest, not the user-owned music files.
`legacy-cap-pdf-inventory.csv` records relative paths, PDF metadata, byte sizes
and SHA-256 hashes. The roots are intentionally represented as `Cap/` and
`Pdf/` so the manifest is portable and the hashes identify the exact corpus
members.

## Findings from all 140 pairs

The corpus is internally consistent:

- Every CAP begins with the binary signature `cap3-v:a`.
- CAP sizes range from 2,636 to 73,172 bytes; the median is 17,057 bytes.
- PDF page counts are: 17 one-page, 35 two-page, 37 three-page, 45 four-page,
  5 five-page and 1 fifteen-page score.
- PDF creation years are concentrated in 2013 (107 files), 2014 (8), 2015
  (15), with a small tail from 2017-2021.
- The PDFs were produced by several PDFCreator versions, so their metadata
  must not be used as a rendering baseline. The score image and page content
  are the useful reference.

Printable-string inspection of the binary corpus also shows a strongly shared
Capella 3 environment: all files contain Times New Roman references, 126
contain the `capella3` music-font name, 116 contain Arial, 99 contain MS Sans
Serif, and 100 contain embedded RTF payloads. These counts are diagnostic
signals, not a complete notation parser; binary fields and music-font glyphs
cannot safely be inferred from strings alone.

Visual inspection of representative pages confirms that the corpus exercises
the important legacy cases: multi-part scores, titles and bylines, section
headings, changing meters, multiple voices, beams and tuplets, slurs, ties,
accents, ornaments, dynamics, hairpins, repeat signs, numbered endings,
multi-measure rests and editorial text. The PDFs are valuable engraving
references, but they do not contain a reliable machine-readable note ledger.

## Audit of the current CAP path

The legacy path now keeps MuseScore's native CAP import as the authoritative
editable score:

1. CapellaBridge validates the `cap3-v:a` signature and records the source
   size and SHA-256 hash.
2. MuseScore imports CAP directly to MSCZ.
3. A CAP-specific normalizer preserves shared irregular bar boundaries,
   repairs isolated silent gaps, propagates a meter already present on a peer
   staff, and infers a missing meter only when closed measures agree across
   the score. Active staves are used for shared-boundary consensus, so a
   silent whole-measure staff cannot suppress a real source-shaped bar. A
   repeated run of identical partial bars on one active staff is also
   preserved; an isolated partial bar is left for manual review. It removes
   only provably orphaned tuplet closes and leaves ambiguous tuplet scopes
   untouched.
4. Native CAP title/credit text is moved into a title frame, chord labels are
   converted from importer Lyrics into native Harmony objects, rest-only
   staves are hidden without deleting their events, and text autoplace is
   restored.
5. Unambiguous textual navigation such as "D.S. al Fine", "D.C. al Fine",
   "alla Coda", "Fine", "Coda" and "Segno" is promoted to native MuseScore
   Marker/Jump objects. Numeric tempo text is promoted only when a BPM is
   explicit. Directions such as "rit." or "a tempo" remain visible and are
   reported for manual review because assigning a guessed BPM would change
   playback.
6. The same final MSCZ is structurally audited and exported to MusicXML and
   PDF. Native tuplet starts, stops, open scopes and orphan closes are recorded
   explicitly, while repeat/ending connectors are checked on the primary staff
   and unusual playback paths are reported as review flags rather than being
   rewritten automatically.

The CAP report now separates `native-valid` from `verified`: native-valid
means that the MSCZ is readable and its native timing/structure passed. CAP is
still marked source-unverified because the binary importer has no lossless
source event ledger. Unclosed or otherwise structurally ambiguous tuplets
remain manual-review, but the editable MSCZ is retained. When a missing meter
is repaired, the report records the exact staff and measure where the shared
or inferred time signature was inserted. This changes the measure boundary
only; it does not add, remove or retime a pitched event.

The final CAP path also exports MusicXML only as a secondary artifact and
checks that export against the native MSCZ. The check compares the sounding
chord/pitch ledger and playback-feature totals without assuming that MuseScore
will preserve internal voice numbers, staff numbering, cursor quantization,
or the exact volta-span spelling. Rest segmentation differences are recorded
as `rest_representation_normalized` rather than treated as lost music; a lost
chord, changed pitch, tie, slur, dynamic, tempo, repeat or other playback
feature still downgrades the result to manual review while retaining the MSCZ.
Volta diagnostics follow native connector objects across measures and report a
first ending that reaches the score end without a matching second ending at
the exact local measure range. Cross-barline tuplets are carried through the
normalizer and semantic audit; silent-gap and measure-length heuristics are
disabled while a tuplet scope is open.

## Recommended implementation

This should be a deterministic regression and normalization pipeline, not a
machine-learning model. The legacy binary format is proprietary and the
reference PDFs are visual output, not training labels for note-level recovery.

### 1. Use one pinned legacy importer

Use the MuseScore Capella importer already shipped in the container as the
binary decoder rather than reverse-engineering a second CAP parser. The
upstream importer has explicit models for notes, rests, clefs, keys, meters,
barlines, voices and Capella draw objects including text, slurs, wedges,
voltas, brackets and trills.

For CAP input, import directly to a native MSCZ first. Do not make the
production score by importing CAP to MusicXML and then importing that XML back
to MSCZ. Export MusicXML from the final native score only as a secondary
deliverable. This removes one lossy conversion boundary and keeps the
MuseScore legacy importer as the source decoder.

### 2. CAP-specific pipeline and report

The implementation records importer provenance, source and native inventories,
timeline and total measure counts, navigation object counts, safe native
repairs, the verification scope, and every unresolved structural diagnostic.
It always retains a genuine MSCZ after a late export or audit failure. The
browser distinguishes `native-valid` from `manual-review` so a readable CAP
score is never presented as source-equivalent merely because MuseScore opened
it.

The existing CAPX converter and its source audit remain unchanged. CAP and CAPX
should share only the native-score validation and output packaging layers.

### 3. Add corpus regression checks

The manifest should drive an opt-in reference job, not a public CI dependency
on private music files. When the two local roots are mounted, the job should:

- convert all 140 CAP files in isolation and in controlled batches;
- record import failures, timeouts, corrupt MSCZ files and missing outputs;
- compare output page count and staff/system structure to the paired PDF;
- render output pages at a normalized size and produce visual difference images
  for human review;
- inspect the native MSCX for hidden pitched events, missing voices, malformed
  durations, repeat/ending objects, ties, slurs, articulations and navigation;
- preserve a per-pair JSON result so a later importer or MuseScore upgrade can
  be compared against the same baseline.

The PDF comparison must be treated as engraving evidence, not as proof that
playback is correct. Any visual discrepancy affecting notes, rests, accidentals,
ties, slurs, articulations, dynamics or repeat routing is a blocking review
item. Spacing and page breaks are lower-priority diagnostics.

### 4. Pin and test the engine

The Dockerfile currently installs an unpinned `musescore3` package. Pin the
engine version or image digest used for CAP imports. A MuseScore upgrade must
rerun the 140-pair corpus because legacy import behavior is engine-dependent.
The CI suite should continue using synthetic fixtures for normal pull requests,
with the private corpus audit run locally or in a protected workflow.

### 5. Define certification honestly

For CAP, distinguish these states:

- `native-valid`: MuseScore created a readable MSCZ and all native structural
  checks pass; source equivalence remains unverified;
- `reference-reviewed`: the paired PDF review and corpus checks pass;
- `manual-review`: the MSCZ exists but a source comparison, importer warning or
  structural check is unresolved.

This prevents a successful CAP-to-MusicXML-to-MSCZ round trip from being
mistaken for proof that the original legacy score survived intact.

## Main risks to address

- Music-font glyphs can encode visible rests, accents, ornaments and editorial
  marks without appearing as ordinary text.
- Binary CAP draw objects may be semantically attached to a note or span a
  range of notes; flattening them into text loses playback or notation meaning.
- Repeat endings and D.C./D.S./Coda navigation require native playback checks,
  not just page-image comparison.
- A valid PDF can coexist with a malformed or semantically incomplete MSCZ.
- The 140 PDFs establish what was printed, but they cannot prove invisible
  playback events. The native score and explicit audit diagnostics must decide
  whether an output is certified.

## Implementation order

1. Keep this manifest and report as the fixed reference contract.
2. Add the direct-CAP-to-MSCZ adapter and CAP-specific report fields.
3. Add native-score audits and honest certification states.
4. Add the local 140-pair corpus runner and normalized PDF comparison tools.
5. Run the corpus against the pinned engine, inspect every blocking diff, and
   only then merge the converter changes into `main`.

