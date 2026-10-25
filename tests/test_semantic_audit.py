"""Playback-level source/native equivalence tests."""
from pathlib import Path
import zipfile

import pytest

from app.semantic_audit import (
    SemanticIntegrityError,
    audit_musicxml_against_mscz,
)


def native_zip(path: Path, xml: str):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("score.mscx", xml.encode())


SOURCE = """<?xml version="1.0"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piccolo</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>480</divisions><time><beats>2</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>480</duration><voice>1</voice>
        <notations><articulations><accent/></articulations><slur type="start" number="1"/></notations>
      </note>
      <note print-object="no"><grace/><rest/><voice>1</voice></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>480</duration><voice>1</voice>
        <notations><articulations><strong-accent/></articulations><slur type="stop" number="1"/></notations>
      </note>
      <barline location="right"><repeat direction="backward"/></barline>
    </measure>
  </part>
</score-partwise>"""


def native(score_note="<pitch>84</pitch>"):
    return f"""<museScore version="3.02"><Score>
      <Part><Staff id="1"/><Instrument><transposeChromatic>12</transposeChromatic></Instrument></Part>
      <Staff id="1"><Measure len="1/4"><voice>
        <Chord><durationType>quarter</durationType><Articulation><subtype>articAccentAbove</subtype></Articulation>
          <Spanner type="Slur"><Slur/><next/></Spanner><Note>{score_note}</Note></Chord>
        <Chord><durationType>quarter</durationType><Articulation><subtype>articMarcatoAbove</subtype></Articulation>
          <Spanner type="Slur"><prev/></Spanner><Note><pitch>86</pitch></Note></Chord>
      </voice><endRepeat/></Measure></Staff>
    </Score></museScore>"""


def test_native_aliases_and_transposition_compare_as_music(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native())
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_events"] == result["native_events"] == 2
    assert result["ignored_grace_rests"] == 1
    assert result["source_repeats"] == result["native_repeats"] == 1


def test_semantic_audit_rejects_pitch_changes(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native("<pitch>73</pitch>"))
    with pytest.raises(SemanticIntegrityError, match="Musical event changed"):
        audit_musicxml_against_mscz(source, output)


def test_semantic_audit_rejects_unmatched_source_rest(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        "</measure>",
        '<note print-object="no"><rest/><duration>30</duration><voice>1</voice></note></measure>',
        1,
    ), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native())
    # The additional source rest has no native counterpart; the audit must
    # remain strict rather than treating arbitrary silence as harmless.
    with pytest.raises(SemanticIntegrityError):
        audit_musicxml_against_mscz(source, output)
