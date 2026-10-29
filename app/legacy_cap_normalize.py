"""Conservative repairs for native MSCZ files imported from legacy CAP.

The MuseScore CAP importer is the authority for the pitches and notation.  A
small number of legacy files nevertheless arrive with importer bookkeeping
issues: a tuplet marker can cross a barline, an irregular source bar can be
forced into the current time signature, or one silent staff can be a fraction
short.  This module repairs only those structural cases without moving or
rewriting a sounding event.
"""
from __future__ import annotations

import os
import re
import tempfile
import zipfile
from collections import Counter
from fractions import Fraction
from html import entities as html_entities
from pathlib import Path

from lxml import etree as ET

from .score_validation import (
    DURATION_TYPES, _duration, _measure_duration, _tuplet_ratio,
)


_XML_NAMED_ENTITY_RE = re.compile(br"&([A-Za-z][A-Za-z0-9]+);")
_XML_PREDEFINED_ENTITIES = {b"amp", b"lt", b"gt", b"apos", b"quot"}


def _sanitize_native_xml_entities(data: bytes) -> tuple[bytes, tuple[str, ...]]:
    """Replace HTML-only entities which make an otherwise valid MSCX unreadable.

    MuseScore normally writes XML-safe text, but its legacy CAP import can
    occasionally leave HTML entities such as ``&nbsp;`` inside title or staff
    text. XML has no predefined ``nbsp`` entity, so retaining it makes the
    delivered MSCZ impossible to reopen. Keep the five XML entities intact,
    decode only known HTML entities, and leave unknown entities untouched so
    malformed source data still fails closed rather than being guessed.
    """
    repaired: set[str] = set()

    def replace(match):
        name = match.group(1)
        if name in _XML_PREDEFINED_ENTITIES:
            return match.group(0)
        value = html_entities.html5.get(name.decode("ascii") + ";")
        if value is None:
            return match.group(0)
        # A non-breaking space is formatting noise in native score text. A
        # regular space is portable in XML and does not alter music or timing.
        value = value.replace("\xa0", " ")
        repaired.add(name.decode("ascii"))
        return value.encode("utf-8")

    return _XML_NAMED_ENTITY_RE.sub(replace, data), tuple(sorted(repaired))


def _native_root(path: Path):
    archive = zipfile.ZipFile(path)
    try:
        if archive.testzip() is not None:
            raise ValueError("MSCZ has a corrupt ZIP member")
        name = next((n for n in archive.namelist()
                     if n.lower().endswith(".mscx")), None)
        if not name:
            raise ValueError("MSCZ contains no native MSCX score")
        source, repaired_entities = _sanitize_native_xml_entities(
            archive.read(name)
        )
        root = ET.fromstring(
            source,
            parser=ET.XMLParser(resolve_entities=False, no_network=True),
        )
        return archive, name, root, repaired_entities
    except Exception:
        archive.close()
        raise


def _write_native(path: Path, archive, score_name: str, root) -> None:
    modified = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".legacy-normalized-", suffix=".mscz",
            dir=path.parent, delete=False
        ) as handle:
            temp = Path(handle.name)
        with zipfile.ZipFile(temp, "w") as target:
            for member in archive.infolist():
                target.writestr(
                    member,
                    modified if member.filename == score_name
                    else archive.read(member.filename),
                )
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _meter_for_measure(measure, meter: Fraction) -> Fraction:
    for signature in measure.findall("./voice/TimeSig"):
        num = int(signature.findtext("sigN", "4"))
        den = int(signature.findtext("sigD", "4"))
        if num <= 0 or den <= 0:
            raise ValueError("Invalid native time signature")
        meter = Fraction(num, den)
    return meter


def _has_time_signature(measure) -> bool:
    return bool(measure.findall("./voice/TimeSig"))


def _duration_type(gap: Fraction) -> str:
    for kind, value in DURATION_TYPES.items():
        if value == gap:
            return kind
    return "128th"


def _normalize_tuplet_markers(score, repairs: list[str]) -> bool:
    """Remove only provably orphaned tuplet closing markers.

    Open scopes are deliberately left untouched: their exact intended
    boundary is not recoverable from a malformed legacy import without
    risking a playback change.
    """
    changed = False
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        voice_stacks: dict[int, list[object]] = {}
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            for voice_index, voice in enumerate(measure.findall("voice"), 1):
                stack = voice_stacks.setdefault(voice_index, [])
                for item in list(voice):
                    if item.tag == "Tuplet":
                        # Let the regular duration validator report malformed
                        # ratios; this pass only manages marker scope.
                        stack.append(item)
                    elif item.tag == "endTuplet":
                        if stack:
                            stack.pop()
                        else:
                            voice.remove(item)
                            changed = True
                            repairs.append(
                                "Removed orphan native tuplet closing marker "
                                f"at staff {staff_index}, measure {measure_index}, "
                                f"voice {voice_index}"
                            )
        # Do not synthesize closing markers for a remaining stack. CAP files
        # in the reference corpus contain importer-specific tuplet scopes
        # whose intended boundary cannot be recovered from MSCX alone. A
        # guessed close changes playback; retaining the native events and
        # reporting the unresolved scope is safer than silently changing the
        # rhythm. The structural audit will mark such an output manual-review.
    return changed


_TEXTUAL_TEMPO_RE = re.compile(
    r"\b(?:allegro|andante|adagio|largo|presto|lento|moderato|vivace|"
    r"maestoso|rit(?:ardando)?\.?|a\s+tempo|tempo\s+primo)\b"
    # German compound labels such as "Buehnentempo" are common in the CAP
    # corpus.  The surrounding word boundaries deliberately apply to the
    # whole compound, not to its internal "tempo" component.
    r"|\b[\wäöüÄÖÜß-]*tempo[\wäöüÄÖÜß-]*\b",
    re.IGNORECASE,
)
_NUMERIC_TEMPO_RE = re.compile(
    r"(?:[♩♪]|quarter(?:\s+note)?|bpm)\s*(?:=|:)\s*(\d{2,3})"
    r"|=\s*(\d{2,3})"
    # Legacy CAP tempo headings commonly use "Allegro (104)" rather than an
    # equals sign. Parentheses are accepted only in the presence of a tempo
    # word by the caller, so dates and catalogue numbers are not promoted.
    r"|\(\s*(\d{2,3})\s*\)",
    re.IGNORECASE,
)


def _staff_text_value(element) -> str:
    text = element.findtext("text", "")
    return " ".join(text.split())


def _native_navigation(score):
    markers = set()
    jumps = set()
    for staff in score.findall("Staff"):
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            for marker in measure.iter("Marker"):
                label = (marker.findtext("label") or "").strip().lower()
                if label:
                    markers.add((measure_index, label))
            for jump in measure.iter("Jump"):
                jumps.add((
                    measure_index,
                    jump.findtext("jumpTo", ""),
                    jump.findtext("playUntil", ""),
                    jump.findtext("continueAt", ""),
                ))
    return markers, jumps


def _append_marker(measure, label: str) -> None:
    """Add a non-printing playback anchor beside existing source text.

    Assigning ``<sym>fine</sym>`` as text causes XML escaping and literally
    prints the markup. The CAP already contains the visible Fine/Coda/Segno
    label, so the generated Marker must provide navigation only.
    """
    marker = ET.Element("Marker")
    ET.SubElement(marker, "text").text = label.capitalize()
    ET.SubElement(marker, "visible").text = "0"
    ET.SubElement(marker, "label").text = label
    measure.append(marker)


def _repair_escaped_navigation_markers(score, repairs: list[str]) -> bool:
    """Repair markers generated by older CapellaBridge releases.

    This is intentionally limited to the exact escaped ``sym`` payload that
    the previous implementation emitted. Native MuseScore markers with a real
    nested symbol remain untouched.
    """
    changed = False
    for marker in score.iter("Marker"):
        label = (marker.findtext("label") or "").strip().lower()
        text = marker.find("text")
        if label not in {"fine", "coda", "segno"} or text is None:
            continue
        value = (text.text or "").strip().lower()
        if value != f"<sym>{label}</sym>":
            continue
        text.text = label.capitalize()
        visible = marker.find("visible")
        if visible is None:
            visible = ET.SubElement(marker, "visible")
        visible.text = "0"
        changed = True
        repairs.append(
            f'Repaired escaped playback marker "{label}"'
        )
    return changed


_CAPELLA_NATIVE_MARKERS = {
    "y": ("segno", "segno"),
}


def _normalize_capella_navigation_glyphs(score, repairs: list[str]) -> bool:
    """Replace legacy Capella-font letters with native navigation symbols."""
    changed = False
    for measure_index, measure in enumerate(
            score.findall("./Staff[1]/Measure"), 1):
        existing = {
            (marker.findtext("label") or "").strip().lower(): marker
            for marker in measure.findall("Marker")
        }
        for staff_text in list(measure.iter("StaffText")):
            family = (staff_text.findtext("family") or "").strip().casefold()
            glyph = _staff_text_value(staff_text)
            if family not in {"capella", "capella3"}:
                continue
            mapped = _CAPELLA_NATIVE_MARKERS.get(glyph)
            if mapped is None:
                continue
            label, symbol = mapped
            parent = staff_text.getparent()
            if parent is None:
                continue
            index = parent.index(staff_text)
            marker = existing.get(label)
            if marker is not None and marker.getparent() is measure:
                measure.remove(marker)
            marker = ET.Element("Marker")
            ET.SubElement(marker, "style").text = "Repeat Left"
            text = ET.SubElement(marker, "text")
            ET.SubElement(text, "sym").text = symbol
            ET.SubElement(marker, "label").text = label
            offset = staff_text.find("offset")
            if offset is not None:
                marker.append(ET.fromstring(ET.tostring(offset)))
            parent.remove(staff_text)
            parent.insert(index, marker)
            existing[label] = marker
            changed = True
            repairs.append(
                f'Replaced Capella-font "{glyph}" with native {label} '
                f'marker at measure {measure_index}'
            )
    return changed


def _normalize_capella_ornament_glyphs(score, repairs: list[str]) -> bool:
    """Promote unambiguous repeated Capella mordent glyphs to notation.

    The legacy importer can leave a Capella3 ornament line as literal text
    such as ``llllllll``. Capella's ``l`` glyph is the upper-mordent symbol;
    retaining the font letters both prints incorrectly and provides no
    ornament playback. Only a repeated run immediately before a chord is
    promoted, so ordinary text and isolated proprietary glyphs remain
    untouched for review.
    """
    changed = False
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            for voice in measure.findall("voice"):
                children = list(voice)
                for index, element in enumerate(children):
                    if element.tag != "StaffText":
                        continue
                    family = (element.findtext("family") or "").strip().casefold()
                    glyph = _staff_text_value(element)
                    if (family not in {"capella", "capella3"}
                            or re.fullmatch(r"l{3,}", glyph) is None):
                        continue
                    target = next((candidate for candidate in children[index + 1:]
                                   if candidate.tag in {"Chord", "Rest"}), None)
                    if target is None or target.tag != "Chord":
                        continue
                    if not any(
                        articulation.findtext("subtype") == "ornamentMordent"
                        for articulation in target.findall("Articulation")
                    ):
                        articulation = ET.Element("Articulation")
                        ET.SubElement(articulation, "subtype").text = "ornamentMordent"
                        target.append(articulation)
                    voice.remove(element)
                    changed = True
                    repairs.append(
                        "Replaced repeated Capella mordent glyphs with a native "
                        f"ornament at staff {staff_index}, measure {measure_index}"
                    )
    return changed


def _volta_records(staff):
    records = []
    measures = staff.findall("Measure")
    for measure_index, measure in enumerate(measures):
        for voice in measure.findall("voice"):
            for spanner in voice.findall('Spanner[@type="Volta"]'):
                volta = spanner.find("Volta")
                if volta is None:
                    continue
                try:
                    step = max(1, int(spanner.findtext(
                        "next/location/measures", "1")))
                except ValueError:
                    continue
                records.append({
                    "measure": measure_index,
                    "measure_node": measure,
                    "voice": voice,
                    "spanner": spanner,
                    "volta": volta,
                    "step": step,
                    "numbers": (volta.findtext("endings") or "").strip(),
                })
    return records


def _connector_for(measures, start: int, endpoint: int):
    if not 0 <= endpoint < len(measures):
        return None
    for voice in measures[endpoint].findall("voice"):
        for spanner in voice.findall('Spanner[@type="Volta"]'):
            if spanner.find("Volta") is not None:
                continue
            try:
                target = endpoint + int(spanner.findtext(
                    "prev/location/measures", "0"))
            except ValueError:
                continue
            if target == start:
                return voice, spanner
    return None


def _set_volta_endpoint(measures, record, endpoint: int) -> None:
    start = record["measure"]
    old_endpoint = start + record["step"]
    old = _connector_for(measures, start, old_endpoint)
    if old is not None:
        old[0].remove(old[1])
    step = endpoint - start
    location = record["spanner"].find("next/location")
    if location is None:
        next_node = record["spanner"].find("next")
        if next_node is None:
            next_node = ET.SubElement(record["spanner"], "next")
        location = ET.SubElement(next_node, "location")
    measures_node = location.find("measures")
    if measures_node is None:
        measures_node = ET.SubElement(location, "measures")
    measures_node.text = str(step)
    record["step"] = step
    if 0 <= endpoint < len(measures):
        target_voice = measures[endpoint].find("voice")
        if target_voice is None:
            target_voice = ET.SubElement(measures[endpoint], "voice")
        connector = ET.Element("Spanner", type="Volta")
        prev = ET.SubElement(connector, "prev")
        loc = ET.SubElement(prev, "location")
        ET.SubElement(loc, "measures").text = str(-step)
        target_voice.insert(0, connector)


def _set_volta_number(record, number: str, *, show_text: bool = True) -> None:
    endings = record["volta"].find("endings")
    if endings is None:
        endings = ET.SubElement(record["volta"], "endings")
    endings.text = number
    begin = record["volta"].find("beginText")
    if begin is None:
        begin = ET.SubElement(record["volta"], "beginText")
    begin.text = f"{number}." if show_text else ""
    record["numbers"] = number


def _normalize_invalid_voltas(score, repairs: list[str],
                              diagnostics: list[str]) -> bool:
    """Repair importer-generated ending zero without inventing ambiguity.

    MuseScore 2 emits ``0.`` for an unlabeled continuation at a system break.
    It can also emit adjacent zero-numbered first/second endings. We repair
    only topologies whose neighbouring valid ending or backward repeat makes
    the intended numbering unambiguous.
    """
    changed = False
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        measures = staff.findall("Measure")
        records = _volta_records(staff)
        invalid = [r for r in records if not r["numbers"] or all(
            not token.isdigit() or int(token) <= 0
            for token in re.split(r"[ ,;]+", r["numbers"])
            if token
        )]
        handled = set()
        # Merge an unlabeled system continuation into the preceding first
        # ending when a connector and a later second ending prove the shape.
        for record in invalid:
            current = record["measure"]
            previous = next((candidate for candidate in reversed(records)
                             if candidate["measure"] < current
                             and "1" in candidate["numbers"].split(",")), None)
            later_second = next((candidate for candidate in records
                                 if candidate["measure"] > current
                                 and "2" in candidate["numbers"].split(",")), None)
            if previous is None or later_second is None:
                continue
            if previous["measure"] + previous["step"] != current:
                continue
            if _connector_for(measures, previous["measure"], current) is None:
                continue
            endpoint = current + record["step"]
            _set_volta_endpoint(measures, previous, endpoint)
            terminal = _connector_for(measures, current, endpoint)
            if terminal is not None:
                terminal[0].remove(terminal[1])
            record["voice"].remove(record["spanner"])
            handled.add(id(record))
            changed = True
            repairs.append(
                "Merged unlabeled volta continuation into first ending at "
                f"staff {staff_index}, measures {previous['measure'] + 1}-"
                f"{endpoint}"
            )

        remaining = [r for r in invalid if id(r) not in handled]
        index = 0
        while index + 1 < len(remaining):
            first, second = remaining[index:index + 2]
            if not (0 < second["measure"] - first["measure"] <= 2):
                index += 1
                continue
            repeat_measure = next((position for position in range(
                first["measure"], min(second["measure"] + 1, len(measures)))
                if measures[position].find(".//endRepeat") is not None), None)
            if repeat_measure is None:
                # Some legacy imports encode the boundary as a terminal
                # BarLine and begin a new repeated section immediately after
                # the second ending. The connected adjacent spanners plus
                # that new start-repeat make the pair unambiguous.
                second_endpoint = second["measure"] + second["step"]
                if (second_endpoint < len(measures)
                        and measures[second_endpoint].find(".//startRepeat")
                        is not None
                        and _connector_for(measures, first["measure"],
                                           second["measure"]) is not None):
                    repeat_measure = first["measure"]
            # Without a backward repeat, two nearby zero labels are not enough
            # evidence to infer first/second endings. Leave them diagnosed for
            # review instead of changing playback on a guess.
            if repeat_measure is None:
                index += 1
                continue
            endpoint = repeat_measure + 1
            if endpoint < first["measure"] + 1:
                index += 1
                continue
            _set_volta_number(first, "1")
            _set_volta_endpoint(measures, first, endpoint)
            _set_volta_number(second, "2")
            changed = True
            handled.update((id(first), id(second)))
            repairs.append(
                "Reconstructed adjacent first/second endings from legacy "
                f"zero-numbered voltas at staff {staff_index}, measures "
                f"{first['measure'] + 1} and {second['measure'] + 1}"
            )
            index += 2

        # A terminal zero ending immediately following a valid first ending
        # is a common importer spelling for a second ending. The preceding
        # numbered span and its repeat connector make this unambiguous even
        # when the backward-repeat element was attached to another staff.
        for record in remaining:
            if id(record) in handled:
                continue
            previous = next((candidate for candidate in reversed(records)
                             if candidate["measure"] < record["measure"]
                             and "1" in candidate["numbers"].split(",")), None)
            if previous is None:
                continue
            previous_endpoint = previous["measure"] + previous["step"]
            if previous_endpoint != record["measure"]:
                continue
            if any(candidate["measure"] > record["measure"]
                   for candidate in records):
                continue
            _set_volta_number(record, "2")
            handled.add(id(record))
            changed = True
            repairs.append(
                "Reconstructed terminal second ending after a proven first "
                f"ending at staff {staff_index}, measure "
                f"{record['measure'] + 1}"
            )

        for record in invalid:
            if id(record) not in handled:
                diagnostics.append(
                    "Unresolved zero-numbered volta at staff "
                    f"{staff_index}, measure {record['measure'] + 1}; playback "
                    "ending was not guessed."
                )
    return changed


def _restore_missing_terminal_second_endings(score, repairs: list[str]) -> bool:
    """Restore a lost final second ending only from repeated score topology."""
    changed = False
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        measures = staff.findall("Measure")
        if len(measures) < 2:
            continue
        records = _volta_records(staff)
        pairs = 0
        for first in records:
            if "1" not in first["numbers"].split(","):
                continue
            if any(second["measure"] == first["measure"] + first["step"]
                   and "2" in second["numbers"].split(",")
                   for second in records):
                pairs += 1
        terminal_first = next((record for record in reversed(records)
                               if "1" in record["numbers"].split(",")
                               and record["measure"] + record["step"]
                               == len(measures) - 1), None)
        if terminal_first is None or pairs < 2:
            continue
        final_index = len(measures) - 1
        if any(record["measure"] == final_index for record in records):
            continue
        voice = measures[final_index].find("voice")
        if voice is None:
            continue
        spanner = ET.Element("Spanner", type="Volta")
        volta = ET.SubElement(spanner, "Volta")
        ET.SubElement(volta, "endHookType").text = "1"
        ET.SubElement(volta, "beginText").text = "2."
        ET.SubElement(volta, "endings").text = "2"
        next_node = ET.SubElement(spanner, "next")
        location = ET.SubElement(next_node, "location")
        # A fractional endpoint closes the bracket at the score's final
        # barline without referring to a non-existent following measure.
        meter = Fraction(4, 4)
        for candidate in measures[:final_index + 1]:
            meter = _meter_for_measure(candidate, meter)
        ET.SubElement(location, "fractions").text = str(meter)
        voice.insert(0, spanner)
        changed = True
        repairs.append(
            "Restored missing terminal second ending from repeated first/"
            f"second-ending topology at staff {staff_index}, measure "
            f"{final_index + 1}"
        )
    return changed


def _has_backward_repeat(measure) -> bool:
    if measure.find("endRepeat") is not None:
        return True
    return any(barline.findtext("subtype") == "end-repeat"
               for barline in measure.findall("BarLine"))


def _has_forward_repeat(measure) -> bool:
    if measure.find("startRepeat") is not None:
        return True
    return any(barline.findtext("subtype") == "start-repeat"
               for barline in measure.findall("BarLine"))


def _normalize_numbered_volta_spans(score, repairs: list[str]) -> bool:
    """Fit numbered endings to proven repeat boundaries.

    MuseScore's CAP importer sometimes preserves the ending numbers but emits
    a one-measure ``next`` distance, or lets a first ending overlap the second.
    The backward repeat is the authoritative end of a first ending. A paired
    second ending is bounded by the next forward repeat, the next ending, or
    the score end. Extension to the score end is allowed only when the paired
    first ending was demonstrably truncated, preventing speculative rewrites.
    """
    changed = False
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        measures = staff.findall("Measure")
        records = _volta_records(staff)
        for first in records:
            if "1" not in first["numbers"].split(","):
                continue
            next_first = next((candidate for candidate in records
                               if candidate["measure"] > first["measure"]
                               and "1" in candidate["numbers"].split(",")), None)
            second = next((candidate for candidate in records
                           if candidate["measure"] > first["measure"]
                           and "2" in candidate["numbers"].split(",")
                           and (next_first is None
                                or candidate["measure"] < next_first["measure"])),
                          None)
            if second is None:
                continue
            repeat_measure = next((index for index in range(
                first["measure"], second["measure"])
                if _has_backward_repeat(measures[index])), None)
            if repeat_measure is None:
                continue

            first_endpoint = repeat_measure + 1
            old_first_endpoint = first["measure"] + first["step"]
            first_was_truncated = old_first_endpoint < first_endpoint
            if old_first_endpoint != first_endpoint:
                _set_volta_endpoint(measures, first, first_endpoint)
                changed = True
                repairs.append(
                    f"Aligned first ending at staff {staff_index}, measure "
                    f"{first['measure'] + 1} to backward repeat at measure "
                    f"{repeat_measure + 1}"
                )

            later_record = next((candidate for candidate in records
                                 if candidate["measure"] > second["measure"]),
                                None)
            forward_repeat = next((index for index in range(
                second["measure"] + 1, len(measures))
                if _has_forward_repeat(measures[index])), None)
            boundaries = [len(measures)]
            if later_record is not None:
                boundaries.append(later_record["measure"])
            if forward_repeat is not None:
                boundaries.append(forward_repeat)
            second_endpoint = min(boundaries)
            old_second_endpoint = second["measure"] + second["step"]
            should_shrink = old_second_endpoint > second_endpoint
            should_extend_terminal = (
                old_second_endpoint < second_endpoint
                and second_endpoint == len(measures)
                and first_was_truncated
            )
            if should_shrink or should_extend_terminal:
                _set_volta_endpoint(measures, second, second_endpoint)
                changed = True
                repairs.append(
                    f"Aligned second ending at staff {staff_index}, measure "
                    f"{second['measure'] + 1} to measure {second_endpoint}"
                )
    return changed


def _append_jump(measure, jump_to: str = "", play_until: str = "",
                 continue_at: str = "") -> None:
    jump = ET.Element("Jump")
    if jump_to:
        ET.SubElement(jump, "jumpTo").text = jump_to
    if play_until:
        ET.SubElement(jump, "playUntil").text = play_until
    if continue_at:
        ET.SubElement(jump, "continueAt").text = continue_at
    measure.append(jump)


def _append_numeric_tempo(voice, bpm: int) -> None:
    tempo = ET.Element("Tempo")
    ET.SubElement(tempo, "tempo").text = str(Fraction(bpm, 60))
    # The original CAP text remains visible. This generated object exists for
    # playback only and must not print a second numeric label.
    ET.SubElement(tempo, "visible").text = "0"
    voice.insert(0, tempo)


def _normalize_tempo_objects(score, repairs: list[str]) -> bool:
    """Hide playback-only tempo values and remove proven staff duplicates."""
    changed = False
    seen = set()
    removed = 0
    hidden = 0
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        meter = Fraction(4, 4)
        stacks: dict[int, list[Fraction]] = {}
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            meter = _meter_for_measure(measure, meter)
            for voice_index, voice in enumerate(measure.findall("voice"), 1):
                cursor = Fraction(0)
                stack = stacks.setdefault(voice_index, [])
                for item in list(voice):
                    if item.tag == "Tuplet":
                        stack.append(_tuplet_ratio(item))
                        continue
                    if item.tag == "endTuplet":
                        if stack:
                            stack.pop()
                        continue
                    if item.tag in {"Chord", "Rest"}:
                        scale = Fraction(1)
                        for ratio in stack:
                            scale *= ratio
                        cursor += _duration(item, meter, scale)
                        continue
                    if item.tag == "location":
                        cursor += Fraction(item.findtext("measures", "0")) * meter
                        cursor += Fraction(item.findtext("fractions", "0"))
                        continue
                    if item.tag != "Tempo":
                        continue
                    value = (item.findtext("tempo") or item.get("tempo") or "").strip()
                    text = " ".join((item.findtext("text") or "").split())
                    key = (measure_index, cursor, value)
                    if key in seen:
                        voice.remove(item)
                        removed += 1
                        changed = True
                        continue
                    seen.add(key)
                    # A Tempo without display text is a playback map entry.
                    # Keeping it visible prints hundreds of decimal BPM labels
                    # in scores with expressive tempo curves.
                    if not text:
                        visible = item.find("visible")
                        if visible is None:
                            visible = ET.SubElement(item, "visible")
                        if visible.text != "0":
                            visible.text = "0"
                            hidden += 1
                            changed = True
    if removed:
        repairs.append(
            f"Removed {removed} duplicate score-global CAP tempo object(s)"
        )
    if hidden:
        repairs.append(
            f"Hid {hidden} playback-only CAP tempo object(s) from print"
        )
    return changed


def _normalize_playback_directions(score, repairs: list[str],
                                   diagnostics: list[str]) -> bool:
    """Turn unambiguous CAP text navigation into native playback objects.

    Text such as "D.S. al Fine" has a precise MuseScore representation and is
    safe to promote. Words such as "rit." do not contain a tempo map, so they
    remain visible as text and are reported instead of being assigned a made-
    up BPM.
    """
    staves = score.findall("Staff")
    primary_staff = next((staff for staff in staves
                          if staff.findall("Measure")), None)
    if primary_staff is None:
        return False
    markers, jumps = _native_navigation(score)
    changed = False
    navigation_requests = []
    tempo_requests = []
    known_tempos = []
    reported_textual_tempos = set()
    for measure_index, measure in enumerate(
            primary_staff.findall("Measure"), 1):
        for tempo in measure.iter("Tempo"):
            raw = tempo.findtext("tempo")
            if raw is None:
                continue
            try:
                bpm = int(round(float(Fraction(raw) * 60)))
            except (ValueError, ZeroDivisionError):
                continue
            if 20 <= bpm <= 400:
                known_tempos.append((measure_index, bpm))
    for staff_index, staff in enumerate(staves, 1):
        measures = staff.findall("Measure")
        for measure_index, measure in enumerate(measures, 1):
            for text_element in measure.iter("StaffText"):
                text = _staff_text_value(text_element)
                if not text:
                    continue
                normalized = re.sub(r"\s+\d+$", "", text.lower()).strip()
                compact = " ".join(normalized.replace(".", ". ").split())
                compact = compact.replace("d. s.", "d.s.").replace("d. c.", "d.c.")
                if compact in {"fine", "coda", "segno"}:
                    navigation_requests.append((
                        measure_index, "marker", compact, "", "", "",
                    ))
                elif "d.s." in compact or "d.c." in compact:
                    jump_to = "segno" if "d.s." in compact else "start"
                    if "coda" in compact:
                        navigation_requests.append((
                            measure_index, "jump", "", jump_to, "", "coda",
                        ))
                    else:
                        navigation_requests.append((
                            measure_index, "jump", "", jump_to, "fine", "",
                        ))
                elif "alla coda" in compact:
                    navigation_requests.append((
                        measure_index, "jump", "", "", "", "coda",
                    ))

                tempo_match = _NUMERIC_TEMPO_RE.search(text)
                tempo_word = _TEXTUAL_TEMPO_RE.search(text)
                if tempo_match and (tempo_word or "bpm" in text.lower()):
                    raw_bpm = next(
                        value for value in tempo_match.groups() if value
                    )
                    bpm = int(raw_bpm)
                    if 20 <= bpm <= 400:
                        tempo_requests.append(
                            (staff_index, measure_index, bpm, text)
                        )
                elif tempo_word:
                    restore = re.search(
                        r"(?i)\b(?:a\s+tempo|tempo\s+primo)\b", text)
                    prior = [entry for entry in known_tempos
                             if entry[0] < measure_index]
                    if restore and prior:
                        tempo_requests.append((
                            staff_index, measure_index, prior[-1][1], text
                        ))
                    else:
                        diagnostic_key = (measure_index, normalized)
                        if diagnostic_key not in reported_textual_tempos:
                            diagnostics.append(
                                f"Textual tempo direction at measure "
                                f"{measure_index} (\"{text}\") has no numeric "
                                "tempo map; playback needs manual review."
                            )
                            reported_textual_tempos.add(diagnostic_key)

    for measure_index, kind, label, jump_to, play_until, continue_at in navigation_requests:
        if measure_index > len(primary_staff.findall("Measure")):
            diagnostics.append(
                f"Playback direction at measure {measure_index} could not be "
                "attached to the primary staff."
            )
            continue
        measure = primary_staff.findall("Measure")[measure_index - 1]
        if kind == "marker":
            if (measure_index, label) in markers:
                continue
            _append_marker(measure, label)
            markers.add((measure_index, label))
            changed = True
            repairs.append(
                f"Added native playback marker \"{label}\" at measure "
                f"{measure_index}"
            )
        else:
            key = (measure_index, jump_to, play_until, continue_at)
            if key in jumps:
                continue
            _append_jump(measure, jump_to, play_until, continue_at)
            jumps.add(key)
            changed = True
            description = "|".join(value for value in key[1:] if value) or "jump"
            repairs.append(
                f"Added native playback jump \"{description}\" at measure "
                f"{measure_index}"
            )

    # A tempo applies to the score timeline, even when the importer happened
    # to attach the source label to more than one staff.  Insert one native
    # Tempo at the originating staff/measure and do not duplicate it on the
    # other staves.
    seen_tempos = set()
    for staff_index, measure_index, bpm, text in tempo_requests:
        key = (measure_index, bpm)
        if key in seen_tempos:
            continue
        if staff_index > len(staves):
            continue
        staff = staves[staff_index - 1]
        measures = staff.findall("Measure")
        if measure_index > len(measures):
            continue
        measure = measures[measure_index - 1]
        voice = measure.find("voice")
        if voice is None:
            continue
        if any(
            len(candidate.findall("Measure")) >= measure_index and
            candidate.findall("Measure")[measure_index - 1].find(".//Tempo")
            is not None
            for candidate in staves
        ):
            seen_tempos.add(key)
            continue
        _append_numeric_tempo(voice, bpm)
        seen_tempos.add(key)
        changed = True
        repairs.append(
            f"Added native numeric tempo {bpm} BPM at measure {measure_index} "
            f"from \"{text}\""
        )
    return changed


def _measure_ledger(score):
    """Return per-staff/measure/voice durations with tuplets carried forward."""
    ledger = {}
    for staff_index, staff in enumerate(score.findall("Staff"), 1):
        meter = Fraction(4, 4)
        stacks: dict[int, list[Fraction]] = {}
        measures = staff.findall("Measure")
        for measure_index, measure in enumerate(measures, 1):
            meter = _meter_for_measure(measure, meter)
            intended = Fraction(measure.get("len")) if measure.get("len") else meter
            for voice_index, voice in enumerate(measure.findall("voice"), 1):
                stack = stacks.setdefault(voice_index, [])
                tuplet_depth_before = len(stack)
                actual, timed = _measure_duration(voice, intended, stack)
                ledger[(staff_index, measure_index, voice_index)] = {
                    "actual": actual,
                    "intended": intended,
                    "timed": timed,
                    "measure": measure,
                    "voice": voice,
                    "explicit_len": measure.get("len") is not None,
                    "time_signature": _has_time_signature(measure),
                    "tuplet_depth_before": tuplet_depth_before,
                    "tuplet_depth_after": len(stack),
                    # Keep the old key for callers that only need the final
                    # depth, while making cross-barline decisions explicit.
                    "tuplet_depth": len(stack),
                }
    return ledger


def _normalize_displaced_single_staff_measure_lengths(
        score, ledger, repairs: list[str]) -> bool:
    """Declare a narrowly proven irregular bar without rewriting its notes.

    MuseScore 2 represents overlapping CAP voices with a negative ``location``
    inside one native voice. In a single-staff score this can make the furthest
    timed endpoint slightly longer than the inherited meter even though all
    source notes are intentional. Deleting or shortening the tail would change
    the music. When the discrepancy is small, isolated, contains that explicit
    displacement, and has no repeat/time-signature boundary, preserve every
    event and record the actual native bar length explicitly.

    Multi-staff scores are excluded because changing only one staff's boundary
    could desynchronise parts. Ordinary overfull bars without a negative
    location remain manual-review.
    """
    staves = score.findall("Staff")
    if len(staves) != 1:
        return False
    measures = staves[0].findall("Measure")
    changed = False
    for (staff_index, measure_index, _voice_index), entry in ledger.items():
        if staff_index != 1 or entry["explicit_len"]:
            continue
        actual = entry["actual"]
        intended = entry["intended"]
        if not intended < actual <= intended + Fraction(1, 8):
            continue
        if entry["tuplet_depth_before"] or entry["tuplet_depth_after"]:
            continue
        voice = entry["voice"]
        if len(entry["measure"].findall("voice")) != 1:
            continue
        offsets = []
        for location in voice.findall("location"):
            try:
                offsets.append(
                    Fraction(location.findtext("measures", "0")) * intended
                    + Fraction(location.findtext("fractions", "0"))
                )
            except (ValueError, ZeroDivisionError):
                offsets = []
                break
        if not offsets or not any(offset < 0 for offset in offsets):
            continue
        measure = entry["measure"]
        if any(measure.find(f".//{tag}") is not None for tag in (
                "TimeSig", "startRepeat", "endRepeat", "Volta", "Jump",
                "Marker", "RepeatMeasure")):
            continue
        # Require normal surrounding bars so a run of malformed importer
        # output cannot be legitimised measure by measure.
        neighbours = []
        for neighbour_index in (measure_index - 1, measure_index + 1):
            if 1 <= neighbour_index <= len(measures):
                neighbour = ledger.get((1, neighbour_index, 1))
                if neighbour is not None:
                    neighbours.append(neighbour)
        if not neighbours or any(
                neighbour["actual"] != neighbour["intended"]
                for neighbour in neighbours):
            continue
        measure.set("len", str(actual))
        changed = True
        repairs.append(
            "Preserved displaced CAP polyphony by declaring irregular measure "
            f"{measure_index} length {actual} instead of inherited {intended}"
        )
    return changed


def _meter_signature(value: Fraction) -> tuple[int, int] | None:
    """Choose a conventional time-signature spelling for an inferred meter."""
    for denominator in (4, 8, 2, 16, 32, 64):
        numerator = value * denominator
        if numerator.denominator == 1 and 1 <= numerator <= 16:
            return int(numerator), denominator
    return None


def _new_time_signature(meter: Fraction):
    signature = _meter_signature(meter)
    if signature is None:
        return None
    numerator, denominator = signature
    node = ET.Element("TimeSig")
    ET.SubElement(node, "sigN").text = str(numerator)
    ET.SubElement(node, "sigD").text = str(denominator)
    return node


def _known_meter_states(score):
    """Return each staff's carried meter, keeping unknown staffs distinct."""
    states = []
    for staff in score.findall("Staff"):
        state = None
        carried = []
        for measure in staff.findall("Measure"):
            signatures = measure.findall("./voice/TimeSig")
            if signatures:
                numerator = int(signatures[0].findtext("sigN", "4"))
                denominator = int(signatures[0].findtext("sigD", "4"))
                if numerator <= 0 or denominator <= 0:
                    raise ValueError("Invalid native time signature")
                state = Fraction(numerator, denominator)
            carried.append(state)
        states.append(carried)
    return states


def _infer_meter(ledger, max_measures: int) -> Fraction | None:
    """Infer a score meter only from consistent, closed native measures."""
    values = [
        entry["actual"]
        for (staff_index, measure_index, _voice_index), entry in ledger.items()
        if 1 <= measure_index <= max_measures
        and entry["actual"] > 0
        and entry["tuplet_depth"] == 0
    ]
    if not values:
        return None
    value, count = Counter(values).most_common(1)[0]
    if count / len(values) < 0.8:
        return None
    return value


def _normalize_shared_meters(score, ledger, repairs: list[str]) -> bool:
    """Propagate or infer omitted score meter without moving any event."""
    staves = score.findall("Staff")
    if not staves:
        return False
    states = _known_meter_states(score)
    max_measures = max((len(staff.findall("Measure")) for staff in staves),
                       default=0)
    changed = False

    inferred = None
    if not any(state is not None for carried in states for state in carried):
        inferred = _infer_meter(ledger, max_measures)

    consensus: dict[int, Fraction] = {}
    for measure_index in range(1, max_measures + 1):
        known = [carried[measure_index - 1]
                 for carried in states
                 if len(carried) >= measure_index
                 and carried[measure_index - 1] is not None]
        if known:
            value, count = Counter(known).most_common(1)[0]
            if count / len(known) >= 0.5:
                consensus[measure_index] = value
        elif inferred is not None:
            consensus[measure_index] = inferred

    for staff_index, staff in enumerate(staves):
        carried = states[staff_index]
        established = any(state is not None for state in carried)
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            if measure.findall("./voice/TimeSig"):
                established = True
                continue
            meter = consensus.get(measure_index)
            if meter is None:
                continue
            # Only insert a signature before the staff has established its
            # own meter. Once a staff has a later explicit change, leave the
            # source's local notation untouched.
            if established:
                continue
            signature = _new_time_signature(meter)
            if signature is None:
                continue
            voice = measure.find("voice")
            if voice is None:
                continue
            voice.append(signature)
            changed = True
            source = "inferred" if inferred is not None and not any(
                state is not None for state in carried) else "shared"
            repairs.append(
                f"Added {source} time signature {_meter_signature(meter)[0]}/"
                f"{_meter_signature(meter)[1]} at staff {staff_index + 1}, "
                f"measure {measure_index}"
            )
            carried[measure_index - 1] = meter
            established = True
    return changed


def _entry_has_chord(entry) -> bool:
    return any(item.tag == "Chord" for item, _duration in entry["timed"])


def _staff_measure_entries(ledger, staff_index: int, measure_index: int):
    return [entry for (staff, measure, _voice), entry in ledger.items()
            if staff == staff_index and measure == measure_index]


def _length_safe_for_staff(entries, actual: Fraction) -> bool:
    """Check every timed voice before changing a shared Measure length."""
    for entry in entries:
        if (entry["measure"].get("len") is not None
                or entry["time_signature"]
                or entry["tuplet_depth_before"]
                or entry["tuplet_depth_after"]):
            return False
        if entry["timed"] and entry["actual"] != actual:
            return False
    return True


def _irregular_run_length(ledger, staff_index: int, measure_index: int,
                          voice_index: int, actual: Fraction,
                          expected: Fraction,
                          updated_measures: set[tuple[int, int]] | None = None) -> int:
    """Count a contiguous source-shaped run of the same irregular bar."""
    length = 1
    for direction in (-1, 1):
        current = measure_index + direction
        while current > 0:
            entry = ledger.get((staff_index, current, voice_index))
            if (entry is None or not _entry_has_chord(entry)
                    or entry["actual"] != actual
                    or entry["intended"] != expected
                    or (entry["measure"].get("len") is not None
                        and (updated_measures is None
                             or (staff_index, current) not in updated_measures))
                    or entry["time_signature"]
                    or entry["tuplet_depth_before"]
                    or entry["tuplet_depth_after"]):
                break
            length += 1
            current += direction
    return length


def _normalize_consensus_lengths(score, ledger, repairs: list[str]) -> bool:
    """Preserve importer timing for safe source-shaped irregular bars.

    A shared active-staff boundary is strong evidence even when another staff
    contains only whole-measure rests. A single active staff is repaired only
    for a repeated run of at least two identical irregular measures; isolated
    partial measures remain manual-review cases because padding them could
    change playback.
    """
    changed = False
    staves = score.findall("Staff")
    if not staves:
        return False
    max_measures = max((len(staff.findall("Measure")) for staff in staves),
                       default=0)

    # First repair a displaced boundary independently within one staff. CAP
    # can split the same source bar at different positions in different
    # instruments (usually because one part contains a tie or a rest). The
    # exact two-measure conservation check makes this safe without forcing
    # unrelated staves to share the same split.
    for staff_index in range(1, len(staves) + 1):
        for voice_index in range(1, 5):
            for measure_index in range(1, max_measures):
                current = ledger.get((staff_index, measure_index, voice_index))
                following = ledger.get((staff_index, measure_index + 1, voice_index))
                if not current or not following:
                    continue
                if any(current[key] or following[key]
                       for key in ("tuplet_depth_before", "tuplet_depth_after")):
                    continue
                if (current["explicit_len"] or current["time_signature"] or
                        following["explicit_len"] or following["time_signature"]):
                    continue
                if not (current["actual"] > current["intended"] and
                        following["actual"] < following["intended"]):
                    continue
                if current["actual"] + following["actual"] != (
                        current["intended"] + following["intended"]):
                    continue
                current["measure"].set("len", str(current["actual"]))
                following["measure"].set("len", str(following["actual"]))
                changed = True
                repairs.append(
                    f"Preserved displaced irregular CAP bar boundary at staff "
                    f"{staff_index}, measures {measure_index}-{measure_index + 1}, "
                    f"voice {voice_index} ({current['actual']} + "
                    f"{following['actual']})"
                )

    updated_measures: set[tuple[int, int]] = set()
    for measure_index in range(1, max_measures + 1):
        for voice_index in range(1, 5):
            active = [
                (key[0], entry) for key, entry in ledger.items()
                if key[1] == measure_index and key[2] == voice_index
                and _entry_has_chord(entry)
            ]
            if not active:
                continue
            groups = {}
            for staff_index, entry in active:
                groups.setdefault(
                    (entry["actual"], entry["intended"]), []
                ).append((staff_index, entry))
            for (actual, expected), active_group in groups.items():
                if actual <= 0 or actual == expected:
                    continue

                candidate_staffs = []
                for staff_index, _active_entry in active_group:
                    staff_entries = _staff_measure_entries(
                        ledger, staff_index, measure_index)
                    if _length_safe_for_staff(staff_entries, actual):
                        candidate_staffs.append(staff_index)
                if not candidate_staffs:
                    continue

                shared_active_boundary = len(candidate_staffs) >= 2
                for staff_index in candidate_staffs:
                    run_length = _irregular_run_length(
                        ledger, staff_index, measure_index, voice_index,
                        actual, expected, updated_measures)
                    safe_displaced_short = False
                    if actual < expected and measure_index > 1:
                        previous = ledger.get((staff_index, measure_index - 1,
                                               voice_index))
                        safe_displaced_short = bool(
                            previous
                            and previous["actual"] > previous["intended"]
                            and previous["actual"] + actual == (
                                previous["intended"] + expected)
                            and _length_safe_for_staff(
                                _staff_measure_entries(
                                    ledger, staff_index, measure_index - 1),
                                previous["actual"])
                        )
                    should_set = (
                        (actual > expected and (shared_active_boundary
                                                or run_length >= 2))
                        or (actual < expected and (run_length >= 2
                                                   or safe_displaced_short))
                    )
                    if (not should_set
                            or (staff_index, measure_index) in updated_measures):
                        continue
                    ledger_measure = _staff_measure_entries(
                        ledger, staff_index, measure_index)
                    for entry in ledger_measure:
                        entry["measure"].set("len", str(actual))
                    updated_measures.add((staff_index, measure_index))
                    changed = True
                    repairs.append(
                        f"Preserved source-shaped native CAP bar length {actual} "
                        f"at staff {staff_index}, measure {measure_index}, "
                        f"voice {voice_index}"
                    )
    return changed


def _has_complete_reference_voice(ledger, staff_index: int, measure_index: int,
                                  voice_index: int) -> bool:
    """Return whether another voice fixes this staff's measure boundary.

    A partial secondary voice is safe to complete with an invisible rest only
    when the same staff already has another complete timed voice. This avoids
    converting a genuine irregular single-voice CAP bar into a regular bar.
    """
    for (staff, measure, voice), candidate in ledger.items():
        if (staff == staff_index and measure == measure_index
                and voice != voice_index and candidate["timed"]
                and candidate["actual"] == candidate["intended"]
                and not candidate["tuplet_depth_before"]
                and not candidate["tuplet_depth_after"]):
            return True
    # Preserve the older safe case for a rest-only staff: its matching voice
    # is complete on every other staff, so the remaining duration is known to
    # be silent even though this staff has no second voice of its own.
    peer_entries = [candidate for (staff, measure, voice), candidate
                    in ledger.items()
                    if measure == measure_index and voice == voice_index
                    and staff != staff_index]
    return bool(peer_entries) and all(
        candidate["actual"] == candidate["intended"]
        and not candidate["tuplet_depth_before"]
        and not candidate["tuplet_depth_after"]
        for candidate in peer_entries
    )


def _append_safe_silent_gaps(score, ledger, repairs: list[str]) -> bool:
    """Make confirmed trailing silence explicit without changing a sound.

    CAP imports occasionally leave a secondary voice one small duration short
    even though a complete sibling voice proves the bar's real boundary. The
    missing interval is already silent in playback; an invisible rest records
    that silence explicitly so MuseScore has a complete rhythmic ledger.
    """
    changed = False
    for key, entry in ledger.items():
        staff_index, measure_index, voice_index = key
        actual = entry["actual"]
        gap = entry["intended"] - actual
        if not (Fraction(0) < gap <= entry["intended"]):
            continue
        if entry["explicit_len"] or entry["time_signature"]:
            continue
        if not entry["timed"]:
            continue
        # A carried tuplet cannot be safely closed by adding a rest here.
        if (entry["tuplet_depth_before"] or entry["tuplet_depth_after"]):
            continue
        if not _has_complete_reference_voice(
                ledger, staff_index, measure_index, voice_index):
            continue
        rest = ET.Element("Rest")
        ET.SubElement(rest, "visible").text = "0"
        ET.SubElement(rest, "durationType").text = _duration_type(gap)
        ET.SubElement(rest, "duration").text = str(gap)
        entry["voice"].append(rest)
        changed = True
        repairs.append(
            f"Added invisible silent gap {gap} at staff {staff_index}, "
            f"measure {measure_index}, voice {voice_index}"
        )
    return changed


def _remove_repeated_duplicate_measure_rests(ledger, repairs: list[str]) -> bool:
    """Remove only a proven importer-added whole-measure tail rest.

    Some CAP imports append a second full-measure rest after an already
    complete musical voice. A single overfull measure can be intentional, so
    this repair requires either agreement across at least two active staves in
    the same measure or a repeated run on one staff. Tuplet scopes and voices
    without pitched music are never changed.
    """
    candidates = []
    for key, entry in ledger.items():
        timed = entry["timed"]
        if (entry["tuplet_depth_before"] or entry["tuplet_depth_after"]
                or len(timed) < 2 or not _entry_has_chord(entry)):
            continue
        tail, tail_duration = timed[-1]
        if (tail.tag != "Rest" or tail_duration != entry["intended"]):
            continue
        prefix = sum((duration for _item, duration in timed[:-1]), Fraction(0))
        if prefix != entry["intended"]:
            continue
        candidates.append((key, entry, tail))
    by_measure = Counter(key[1] for key, _entry, _tail in candidates)
    by_staff = Counter(key[0] for key, _entry, _tail in candidates)
    changed = False
    for key, entry, tail in candidates:
        if by_measure[key[1]] < 2 and by_staff[key[0]] < 4:
            continue
        entry["voice"].remove(tail)
        changed = True
        repairs.append(
            "Removed duplicate importer-generated full-measure tail rest at "
            f"staff {key[0]}, measure {key[1]}, voice {key[2]}"
        )
    return changed


def normalize_legacy_cap_mscz(path: Path) -> dict:
    """Apply safe CAP-import repairs and return an auditable repair record."""
    path = Path(path)
    repairs: list[str] = []
    diagnostics: list[str] = []
    archive, score_name, root, repaired_entities = _native_root(path)
    try:
        score = root.find("Score")
        if score is None:
            raise ValueError("MSCX contains no Score element")
        changed = bool(repaired_entities)
        if repaired_entities:
            repairs.append(
                "Replaced XML-incompatible named entity/entities in native "
                f"score text: {', '.join(repaired_entities)}"
            )
        changed = _repair_escaped_navigation_markers(
            score, repairs
        ) or changed
        changed = _normalize_capella_navigation_glyphs(
            score, repairs
        ) or changed
        changed = _normalize_capella_ornament_glyphs(
            score, repairs
        ) or changed
        changed = _normalize_invalid_voltas(
            score, repairs, diagnostics
        ) or changed
        changed = _restore_missing_terminal_second_endings(
            score, repairs
        ) or changed
        changed = _normalize_numbered_volta_spans(
            score, repairs
        ) or changed
        changed = _normalize_tuplet_markers(score, repairs) or changed
        changed = _normalize_playback_directions(
            score, repairs, diagnostics
        ) or changed
        changed = _normalize_tempo_objects(score, repairs) or changed
        ledger = _measure_ledger(score)
        changed = _normalize_displaced_single_staff_measure_lengths(
            score, ledger, repairs
        ) or changed
        if changed:
            ledger = _measure_ledger(score)
        changed = _remove_repeated_duplicate_measure_rests(
            ledger, repairs
        ) or changed
        if changed:
            ledger = _measure_ledger(score)
        changed = _normalize_shared_meters(score, ledger, repairs) or changed
        if changed:
            ledger = _measure_ledger(score)
        changed = _normalize_consensus_lengths(score, ledger, repairs) or changed
        # Rebuild the ledger after changing Measure len so gap decisions use
        # the final timing boundary, not the inherited meter.
        if changed:
            ledger = _measure_ledger(score)
        changed = _append_safe_silent_gaps(score, ledger, repairs) or changed
        if changed:
            _write_native(path, archive, score_name, root)
    finally:
        archive.close()
    return {
        "changed": bool(repairs),
        "repairs": repairs,
        "diagnostics": diagnostics,
    }
