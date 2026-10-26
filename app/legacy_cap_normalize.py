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
from pathlib import Path

from lxml import etree as ET

from .score_validation import DURATION_TYPES, _measure_duration


def _native_root(path: Path):
    archive = zipfile.ZipFile(path)
    try:
        if archive.testzip() is not None:
            raise ValueError("MSCZ has a corrupt ZIP member")
        name = next((n for n in archive.namelist()
                     if n.lower().endswith(".mscx")), None)
        if not name:
            raise ValueError("MSCZ contains no native MSCX score")
        root = ET.fromstring(
            archive.read(name),
            parser=ET.XMLParser(resolve_entities=False, no_network=True),
        )
        return archive, name, root
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
    r"maestoso|rit(?:ardando)?\.?|a\s+tempo|tempo\s+primo|tempo)\b",
    re.IGNORECASE,
)
_NUMERIC_TEMPO_RE = re.compile(
    r"(?:[♩♪]|quarter(?:\s+note)?|bpm)\s*(?:=|:)\s*(\d{2,3})"
    r"|=\s*(\d{2,3})",
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


def _append_marker(measure, label: str, symbol: str) -> None:
    marker = ET.Element("Marker")
    ET.SubElement(marker, "text").text = f"<sym>{symbol}</sym>"
    ET.SubElement(marker, "label").text = label
    measure.append(marker)


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
    voice.insert(0, tempo)


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
                    raw_bpm = tempo_match.group(1) or tempo_match.group(2)
                    bpm = int(raw_bpm)
                    if 20 <= bpm <= 400:
                        tempo_requests.append((measure_index, bpm, text))
                elif tempo_word:
                    diagnostics.append(
                        f"Textual tempo direction at staff {staff_index}, "
                        f"measure {measure_index} (\"{text}\") has no numeric "
                        "tempo map; playback needs manual review."
                    )

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
            _append_marker(measure, label, label)
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

    for measure_index, bpm, text in tempo_requests:
        if measure_index > len(primary_staff.findall("Measure")):
            continue
        measure = primary_staff.findall("Measure")[measure_index - 1]
        voice = measure.find("voice")
        if voice is None or measure.find(".//Tempo") is not None:
            continue
        _append_numeric_tempo(voice, bpm)
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


def _append_safe_silent_gaps(score, ledger, repairs: list[str]) -> bool:
    """Pad only an isolated, silent trailing gap agreed by the other staves."""
    changed = False
    staves = score.findall("Staff")
    for key, entry in ledger.items():
        staff_index, measure_index, voice_index = key
        actual = entry["actual"]
        gap = entry["intended"] - actual
        if not (Fraction(0) < gap <= Fraction(1, 4)):
            continue
        if entry["explicit_len"] or entry["time_signature"]:
            continue
        if not entry["timed"] or entry["timed"][-1][0].tag == "Chord":
            continue
        # A carried tuplet cannot be safely closed by adding a rest here.
        if (entry["tuplet_depth_before"] or entry["tuplet_depth_after"]):
            continue
        peer_entries = [ledger.get((other_index, measure_index, voice_index))
                        for other_index in range(1, len(staves) + 1)
                        if other_index != staff_index]
        if any(peer is None or peer["actual"] != peer["intended"]
               for peer in peer_entries):
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


def normalize_legacy_cap_mscz(path: Path) -> dict:
    """Apply safe CAP-import repairs and return an auditable repair record."""
    path = Path(path)
    repairs: list[str] = []
    diagnostics: list[str] = []
    archive, score_name, root = _native_root(path)
    try:
        score = root.find("Score")
        if score is None:
            raise ValueError("MSCX contains no Score element")
        changed = _normalize_tuplet_markers(score, repairs)
        changed = _normalize_playback_directions(
            score, repairs, diagnostics
        ) or changed
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
