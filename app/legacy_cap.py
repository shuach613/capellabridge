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
from collections import Counter
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


def _native_symbol_inventory(root) -> dict:
    """Return typed native symbol counts without treating every Symbol as an ornament."""
    articulation_subtypes = Counter(
        element.findtext("subtype", "")
        for element in root.iter()
        if _local_name(element) == "Articulation"
        and element.findtext("subtype")
    )
    ornament_subtypes = Counter(
        subtype for subtype in articulation_subtypes
        if subtype.startswith("ornament")
    )
    tempo_values = [
        value for element in root.iter()
        if _local_name(element) == "Tempo"
        for value in [element.findtext("tempo") or element.get("tempo")]
        if value
    ]
    return {
        "articulation_subtypes": dict(sorted(articulation_subtypes.items())),
        "ornament_subtypes": dict(sorted(ornament_subtypes.items())),
        "tempo_values": tempo_values,
    }


def _has_hidden_pitch(note) -> bool:
    if note.find("pitch") is None:
        return False
    visible = note.findtext("visible")
    return visible == "0" or note.get("visible") == "0"


def _navigation_diagnostics(score) -> tuple[list[str], list[str]]:
    """Check playback structure on the score's primary staff.

    Repeat and volta objects are duplicated across imported parts in some
    legacy files, so navigation is evaluated on the first staff only. Counts
    alone are not enough: a first ending must terminate at a backward repeat,
    and a volta span must stay inside the score. We report only malformed
    structures; unusual but valid count patterns remain observable without
    being rewritten.
    """
    staves = score.findall("Staff")
    if not staves:
        return [], []
    staff = staves[0]
    measures = staff.findall("Measure")
    backward_repeats = set()
    for measure_index, measure in enumerate(measures, 1):
        if measure.find("endRepeat") is not None:
            backward_repeats.add(measure_index)
        for barline in measure.findall("BarLine"):
            if barline.findtext("subtype") == "end-repeat":
                backward_repeats.add(measure_index)

    issues = []
    review_flags = []
    ending_spans = _ending_spans(measures, issues)
    ending_numbers = {
        number
        for _start, _end, numbers in ending_spans
        for number in numbers
    }
    for start_measure, end_measure, endings in ending_spans:
        if ("1" in endings
                and not any(start_measure <= position <= end_measure
                            for position in backward_repeats)):
            review_flags.append(
                f"Volta at measure {start_measure} has no backward repeat "
                f"at or after its end (measure {end_measure})"
            )

        # A first ending at the end of the score is not a complete playback
        # path: on the repeated pass MuseScore has nowhere to continue. Do
        # not invent a second ending, because the binary CAP source is not
        # losslessly available here; report the exact local defect instead.
        if "1" in endings and end_measure == len(measures):
            matching_second = any(
                second_start > end_measure and "2" in second_numbers
                for second_start, _second_end, second_numbers in ending_spans
            )
            if not matching_second:
                review_flags.append(
                    f"First ending at measures {start_measure}-{end_measure} "
                    "has no matching second ending before the score ends; "
                    "playback may skip the final continuation on repeat"
                )

    if "1" in ending_numbers and "2" not in ending_numbers:
        review_flags.append("A first ending exists without a second ending")
    return issues, review_flags


def _volta_spanners(measure):
    for voice in measure.findall("voice"):
        yield from voice.findall('Spanner[@type="Volta"]')


def _volta_connector_targets(spanner, connector_measure: int,
                             start_measure: int) -> bool:
    """Return whether a native volta connector points to ``start_measure``."""
    raw = spanner.findtext("prev/location/measures")
    if raw is None:
        return False
    try:
        return connector_measure + int(raw) == start_measure
    except ValueError:
        return False


def _has_volta_connector(measure, connector_measure: int,
                         start_measure: int) -> bool:
    """Return whether a measure contains a continuation of a volta line."""
    # A second ending may share the boundary measure with the closing
    # connector for the first ending. In that case the new Volta object starts
    # a new span; it must not be swallowed into the preceding one.
    if any(spanner.find("Volta") is not None
           for spanner in _volta_spanners(measure)):
        return False
    for spanner in _volta_spanners(measure):
        if (spanner.find("Volta") is None
                and _volta_connector_targets(
                    spanner, connector_measure, start_measure)):
            return True
    return False


def _ending_spans(measures, issues: list[str] | None = None):
    """Return ``(start, end, numbers)`` for each native volta line.

    MuseScore represents a multi-measure ending as a start object followed by
    one or more connector objects. The old audit only used the start object's
    ``next`` value, which made a final first ending look like a one-measure
    object and missed the fact that its connector reached the end of the
    score. This parser follows those native connectors without changing the
    score.
    """
    issues = issues if issues is not None else []
    spans = []
    for start_measure, measure in enumerate(measures, 1):
        for spanner in _volta_spanners(measure):
            volta = spanner.find("Volta")
            if volta is None:
                if spanner.find("prev") is None:
                    issues.append(
                        f"Volta at measure {start_measure} has no native Volta object"
                    )
                continue
            raw_numbers = (volta.findtext("endings") or "").strip()
            numbers = tuple(
                part.strip() for part in raw_numbers.split(",") if part.strip()
            )
            if not numbers:
                issues.append(f"Volta at measure {start_measure} has no ending number")
            try:
                step = int(spanner.findtext("next/location/measures", "1"))
            except ValueError:
                issues.append(f"Volta at measure {start_measure} has an invalid span")
                continue
            end_measure = start_measure + max(1, step) - 1
            if end_measure > len(measures):
                issues.append(
                    f"Volta at measure {start_measure} extends beyond the score"
                )
                continue
            # The first connector is often the measure immediately after the
            # start object. Continue while a native <prev> connector exists.
            while end_measure < len(measures) and _has_volta_connector(
                    measures[end_measure], end_measure + 1, start_measure
            ):
                end_measure += 1
            candidate = (start_measure, end_measure, numbers)
            if candidate not in spans:
                spans.append(candidate)
    return spans


def _tuplet_scope_inventory(score) -> dict:
    """Inventory native tuplet boundaries without rewriting their meaning."""
    starts = 0
    stops = 0
    orphan_closes = []
    open_scopes = []
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        stacks: dict[int, list[int]] = {}
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            for voice_index, voice in enumerate(measure.findall("voice"), 1):
                stack = stacks.setdefault(voice_index, [])
                for item in voice:
                    if item.tag == "Tuplet":
                        starts += 1
                        stack.append(measure_index)
                    elif item.tag == "endTuplet":
                        stops += 1
                        if stack:
                            stack.pop()
                        else:
                            orphan_closes.append({
                                "staff": staff_index,
                                "measure": measure_index,
                                "voice": voice_index,
                            })
        for voice_index, stack in stacks.items():
            open_scopes.extend({
                "staff": staff_index,
                "voice": voice_index,
                "start_measure": measure_index,
            } for measure_index in stack)
    return {
        "starts": starts,
        "stops": stops,
        "orphan_closes": orphan_closes,
        "open_scopes": open_scopes,
    }


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
    tuplet_scopes = _tuplet_scope_inventory(score)
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
        "tuplet_starts": tuplet_scopes["starts"],
        "tuplet_stops": tuplet_scopes["stops"],
        "open_tuplet_scopes": len(tuplet_scopes["open_scopes"]),
        "orphan_tuplet_closes": len(tuplet_scopes["orphan_closes"]),
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
        # Generic Symbol nodes are visual glyphs, not proof of an ornament.
        # Typed ornament/articulation objects are counted separately so a CAP
        # import with a decorative symbol is not falsely certified as having
        # an ornament that may affect playback.
        "ornaments": _count(root, ("Ornament", "Trill", "trill")),
        "lyrics": _count(root, ("Lyrics", "Verse",)),
        "harmonies": _count(root, ("Harmony",)),
        "text_objects": _count(root, (
            "StaffText", "SystemText", "Text", "Dynamic", "Tempo",
        )),
    }
    symbols = _native_symbol_inventory(root)
    count_map.update({
        "typed_ornaments": sum(symbols["ornament_subtypes"].values()),
        "music_symbols": _count(root, ("Symbol",)),
        "articulation_subtypes": symbols["articulation_subtypes"],
        "ornament_subtypes": symbols["ornament_subtypes"],
        "tempo_values": symbols["tempo_values"],
    })
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
    if tuplet_scopes["open_scopes"]:
        warnings.append(
            f"Native score contains {len(tuplet_scopes['open_scopes'])} "
            "unclosed tuplet scope(s); playback duration needs manual review."
        )
    if tuplet_scopes["orphan_closes"]:
        warnings.append(
            f"Native score contains {len(tuplet_scopes['orphan_closes'])} "
            "orphan tuplet closing marker(s)."
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
    navigation_issues, navigation_review = _navigation_diagnostics(score)
    primary_staff = score.find("Staff")
    ending_spans = _ending_spans(
        primary_staff.findall("Measure") if primary_staff is not None else []
    )
    navigation["ending_spans"] = [
        {"start_measure": start, "end_measure": end,
         "endings": list(numbers)}
        for start, end, numbers in ending_spans
    ]
    navigation["unpaired_first_endings"] = [
        {"start_measure": start, "end_measure": end}
        for start, end, numbers in ending_spans
        if "1" in numbers and end == len(primary_staff.findall("Measure"))
        and not any(
            second_start > end and "2" in second_numbers
            for second_start, _second_end, second_numbers in ending_spans
        )
    ] if primary_staff is not None else []
    if navigation_issues:
        warnings.extend(
            f"Playback navigation review: {issue}" for issue in navigation_issues
        )
    if navigation_review:
        warnings.extend(
            f"Playback navigation review: {review}" for review in navigation_review
        )
    navigation["issues"] = navigation_issues
    navigation["review_flags"] = navigation_review
    return {
        "counts": count_map,
        "navigation": navigation,
        "tuplet_scopes": tuplet_scopes,
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
