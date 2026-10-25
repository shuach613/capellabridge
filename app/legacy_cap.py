"""Safety checks and native-score inventory for legacy binary Capella files.

Legacy ``.cap`` files are decoded by the configured MuseScore Capella importer.
This module deliberately does not attempt to infer music from the reference
PDFs.  It validates the binary container before import and records what the
native MuseScore score actually contains after import.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import zipfile
from pathlib import Path

from lxml import etree as ET


CAP_SIGNATURE = b"cap3-v:a"


class LegacyCapError(ValueError):
    """Raised when a legacy CAP file cannot be safely identified."""


@dataclass(frozen=True)
class CapSource:
    format: str
    signature: str
    size_bytes: int
    sha256: str


def inspect_cap(path: Path) -> dict:
    """Validate the known legacy CAP container and return provenance data.

    The current reference corpus is the Capella 3 binary format identified by
    ``cap3-v:a``.  Rejecting an unknown signature is safer than silently
    sending a different binary format through the importer and certifying a
    misleading result.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            header = source.read(len(CAP_SIGNATURE))
            digest = hashlib.sha256()
            digest.update(header)
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise LegacyCapError(f"Cannot read legacy CAP file: {exc}") from exc
    if size < len(CAP_SIGNATURE):
        raise LegacyCapError("Legacy CAP file is truncated before its signature")
    if header != CAP_SIGNATURE:
        actual = header.decode("ascii", errors="replace")
        raise LegacyCapError(
            f"Unsupported legacy CAP signature {actual!r}; expected "
            f"{CAP_SIGNATURE.decode('ascii')!r}"
        )
    source = CapSource("cap", header.decode("ascii"), size, digest.hexdigest())
    return asdict(source)


def _local_name(element) -> str:
    return ET.QName(element).localname


def _count(root, names: tuple[str, ...]) -> int:
    wanted = set(names)
    return sum(1 for element in root.iter() if _local_name(element) in wanted)


def _has_hidden_pitch(note) -> bool:
    if note.find("pitch") is None:
        return False
    visible = note.findtext("visible")
    return visible == "0" or note.get("visible") == "0"


def inspect_native_mscz(path: Path) -> dict:
    """Extract a conservative musical inventory from a native MSCZ.

    This is intentionally an inventory rather than a second converter.  It is
    used to make CAP imports observable and to ensure the delivered archive is
    a real, inspectable MuseScore score even when certification remains
    ``manual-review``.
    """
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise LegacyCapError("MuseScore archive contains a corrupt ZIP member")
            score_name = next(
                (name for name in archive.namelist()
                 if name.lower().endswith(".mscx")), None
            )
            if score_name is None:
                raise LegacyCapError("MuseScore archive contains no native MSCX score")
            root = ET.fromstring(
                archive.read(score_name),
                parser=ET.XMLParser(resolve_entities=False, no_network=True),
            )
    except (OSError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
        raise LegacyCapError("Malformed native MuseScore archive") from exc

    score = root.find("Score")
    if score is None:
        raise LegacyCapError("Native MSCX contains no Score element")
    staves = score.findall("Staff")
    measures = [measure for staff in staves for measure in staff.findall("Measure")]
    voices = [voice for measure in measures for voice in measure.findall("voice")]
    chords = [item for voice in voices for item in voice if _local_name(item) == "Chord"]
    rests = [item for voice in voices for item in voice if _local_name(item) == "Rest"]
    notes = [note for chord in chords for note in chord.iter()
             if _local_name(note) == "Note"]
    hidden_pitched = sum(1 for note in notes if _has_hidden_pitch(note))
    measures_per_staff = [len(staff.findall("Measure")) for staff in staves]
    rest_only_staves = [index for index, staff in enumerate(staves, 1)
                        if staff.find('.//Chord') is None
                        and staff.find('.//Note') is None
                        and staff.find('.//Rest') is not None]
    visible_staves = [index for index, staff in enumerate(staves, 1)
                      if staff.findtext('visible') != '0']

    count_map = {
        "staves": len(staves),
        "measures": len(measures),
        "measures_total": len(measures),
        "measures_per_staff": measures_per_staff,
        "timeline_measures": max(measures_per_staff, default=0),
        "visible_staves": len(visible_staves),
        "rest_only_staves": rest_only_staves,
        "voices": len(voices),
        "chords": len(chords),
        "notes": len(notes),
        "rests": len(rests),
        "hidden_pitched_events": hidden_pitched,
        "tuplets": _count(root, ("Tuplet",)),
        "ties": _count(root, ("tieBack", "tieFor", "Tie")),
        "slurs": _count(root, ("Slur",)),
        "hairpins": _count(root, ("HairPin",)),
        "dynamics": _count(root, ("Dynamic",)),
        "tempos": _count(root, ("Tempo",)),
        "time_signatures": _count(root, ("TimeSig",)),
        "key_signatures": _count(root, ("KeySig",)),
        "repeats": _count(root, ("RepeatMeasure", "repeat")),
        "start_repeats": _count(root, ("startRepeat",)),
        "end_repeats": _count(root, ("endRepeat",)),
        "voltas": _count(root, ("Volta",)),
        "jumps": _count(root, ("Jump",)),
        "markers": _count(root, ("Marker",)),
        "articulations": _count(root, ("Articulation",)),
        "ornaments": _count(root, (
            "Ornament", "Trill", "trill", "Symbol",
        )),
        "lyrics": _count(root, ("Lyrics", "Verse",)),
        "harmonies": _count(root, ("Harmony",)),
        "text_objects": _count(root, (
            "StaffText", "SystemText", "Text", "Dynamic", "Tempo",
        )),
    }
    warnings = []
    if not staves:
        warnings.append("Native score contains no staves")
    if not measures:
        warnings.append("Native score contains no measures")
    if not notes and not rests:
        warnings.append("Native score contains no timed musical events")
    if hidden_pitched:
        warnings.append(
            f"Native score contains {hidden_pitched} hidden pitched event(s); "
            "verify playback and voice routing."
        )
    navigation = {
        "start_repeats": count_map["start_repeats"],
        "end_repeats": count_map["end_repeats"],
        "voltas": count_map["voltas"],
        "jumps": count_map["jumps"],
        "markers": count_map["markers"],
        "explicit_navigation_objects": sum(
            count_map[key] for key in ("start_repeats", "end_repeats",
                                       "voltas", "jumps", "markers")
        ),
    }
    return {
        "counts": count_map,
        "navigation": navigation,
        "warnings": warnings,
        "score_member": score_name,
    }


def is_valid_mscz(path: Path) -> bool:
    """Return whether *path* contains a readable native MuseScore score."""
    try:
        inspect_native_mscz(path)
    except LegacyCapError:
        return False
    return True
