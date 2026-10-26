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
                actual, timed = _measure_duration(voice, intended, stack)
                ledger[(staff_index, measure_index, voice_index)] = {
                    "actual": actual,
                    "intended": intended,
                    "timed": timed,
                    "measure": measure,
                    "voice": voice,
                    "explicit_len": measure.get("len") is not None,
                    "time_signature": _has_time_signature(measure),
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


def _normalize_consensus_lengths(score, ledger, repairs: list[str]) -> bool:
    """Preserve importer timing for shared irregular bars.

    When every staff has the same non-meter duration, the common source bar
    boundary is stronger evidence than the current inherited time signature.
    Setting ``Measure len`` records that boundary. A following short bar is
    changed only when it completes the exact amount of time displaced by the
    preceding overfull bar.
    """
    changed = False
    staves = score.findall("Staff")
    if not staves:
        return False
    max_measures = max((len(staff.findall("Measure")) for staff in staves), default=0)

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

    for measure_index in range(1, max_measures + 1):
        for voice_index in range(1, 5):
            entries = [ledger[key] for key in ledger
                       if key[1] == measure_index and key[2] == voice_index]
            if len(entries) != len(staves):
                continue
            actuals = {entry["actual"] for entry in entries}
            if len(actuals) != 1:
                continue
            actual = next(iter(actuals))
            if actual <= 0:
                continue
            # An explicit source length or time signature is a deliberate
            # boundary and must never be overwritten by this heuristic.
            if any(entry["measure"].get("len") is not None or entry["time_signature"]
                   for entry in entries):
                continue
            intended = {entry["intended"] for entry in entries}
            if len(intended) != 1:
                continue
            expected = next(iter(intended))
            should_set = actual > expected
            if actual < expected and measure_index > 1:
                previous = [ledger[key] for key in ledger
                            if key[1] == measure_index - 1
                            and key[2] == voice_index]
                if len(previous) == len(staves):
                    prev_actuals = {entry["actual"] for entry in previous}
                    prev_expected = {entry["intended"] for entry in previous}
                    previous_safe = (
                        len(prev_actuals) == 1 and len(prev_expected) == 1
                        and next(iter(prev_actuals)) > next(iter(prev_expected))
                        and not any(entry["explicit_len"] or entry["time_signature"]
                                    for entry in previous)
                    )
                    should_set = previous_safe and (
                        next(iter(prev_actuals)) + actual ==
                        next(iter(prev_expected)) + expected
                    )
            if not should_set:
                continue
            for entry in entries:
                entry["measure"].set("len", str(actual))
            changed = True
            repairs.append(
                f"Preserved shared irregular native CAP bar length {actual} "
                f"at measure {measure_index}, voice {voice_index} "
                f"across {len(staves)} staves"
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
        depth = 0
        for item in entry["voice"]:
            if item.tag == "Tuplet":
                depth += 1
            elif item.tag == "endTuplet":
                depth = max(0, depth - 1)
        if depth:
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
    archive, score_name, root = _native_root(path)
    try:
        score = root.find("Score")
        if score is None:
            raise ValueError("MSCX contains no Score element")
        changed = _normalize_tuplet_markers(score, repairs)
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
    return {"changed": bool(repairs), "repairs": repairs}
