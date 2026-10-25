"""Verify that CAPX semantics survive the CAPX -> MusicXML boundary.

The native-score audit can only prove that MuseScore preserved the MusicXML it
was given.  This module builds a small, source-owned ledger from the parsed
CapXML events and compares it with the MusicXML ledger.  It intentionally
reuses the converter's parsing and normalization rules, but does not inspect
the generated MusicXML to decide what the source meant.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from lxml import etree as ET

from . import converter
from .semantic_audit import (
    SemanticEvent,
    SemanticIntegrityError,
    _complete_tie_chains,
    _filtered_events,
    _source_snapshot,
)


_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5,
                "G": 7, "A": 9, "B": 11}
_ARTICULATIONS = {
    "staccato": "staccato",
    "tenuto": "tenuto",
    "staccatissimo": "staccatissimo",
    "normalAccent": "accent",
    "strongAccent": "strong-accent",
    "accent": "accent",
    "marcato": "strong-accent",
    "weakBeat": "accent",
    "strongBeat": "strong-accent",
}


def _midi(head, alter: int) -> int:
    pitch = head.get("pitch", "C5")
    if len(pitch) != 2 or pitch[0] not in _PITCH_CLASS or not pitch[1].isdigit():
        raise SemanticIntegrityError(f"Invalid Capella pitch: {pitch!r}")
    return _PITCH_CLASS[pitch[0]] + int(pitch[1]) * 12 + alter


def _direct_marks(node, glyphs: set[str]) -> set[str]:
    marks = set()
    mark_nodes = ([node] if converter.tag(node) == "articulation"
                  else converter.children(node, "articulation"))
    for mark in mark_nodes:
        for token in mark.get("type", "").split():
            mapped = _ARTICULATIONS.get(token)
            if mapped:
                marks.add(mapped)
    for glyph, kind in {
        "È": "tenuto", "É": "staccatissimo", "Î": "staccatissimo",
        "Ê": "accent", "Ë": "strong-accent", "Ï": "strong-accent",
        "Ì": "accent", "Í": "strong-accent",
    }.items():
        if glyph in glyphs:
            marks.add(kind)
    return marks


def _direct_pitches(event, state, override, invisible):
    if event.kind != "chord" or override.get("print_as_rest"):
        return ()
    source_event = override.get("pitch_source", event)
    heads = converter.children(converter.child(source_event.node, "heads"), "head")
    values = []
    for head in heads:
        if invisible and head.get("silent") == "true":
            continue
        raw = head.get("pitch", "C5")
        match = raw[0] if raw else ""
        if match not in _PITCH_CLASS or len(raw) != 2 or not raw[1].isdigit():
            raise SemanticIntegrityError(f"Invalid Capella pitch: {raw!r}")
        cap_octave = int(raw[1])
        xml_octave = cap_octave - 1
        key_alter = converter.key_fifths_map(state["key"])[match]
        alteration = converter.child(head, "alter")
        if alteration is not None and alteration.get("step") is not None:
            alter = int(alteration.get("step"))
            state["accidentals"][(match, xml_octave)] = alter
        else:
            alter = state["accidentals"].get((match, xml_octave), key_alter)
        values.append(_midi(head, alter))
    return tuple(sorted(values))


def _direct_snapshot(root):
    parts, end, _ = converter.parse_score(root)
    boundaries, _ = converter.compute_boundaries(parts, end)
    rest_overrides = converter.shifted_tied_rest_pairs(parts)
    events = []

    for part_index, part in enumerate(parts, 1):
        ordered = sorted(part.events, key=lambda item: (item.time, item.voice))
        slur_starts, slur_ends = converter.make_slur_links(ordered)
        state = {"key": 0, "accidentals": {}}
        current_measure = None
        for event in ordered:
            if event.kind == "keySign":
                state["key"] = int(event.node.get("fifths", "0"))
                continue
            if event.kind not in ("chord", "rest"):
                continue
            duration_node = converter.child(event.node, "duration")
            is_grace = duration_node is not None and duration_node.get("noDuration") == "true"
            if not event.dur and not is_grace:
                continue
            override = rest_overrides.get(id(event), {})
            if event.time < 0 or event.time >= end:
                continue
            start_index = max(0, min(len(boundaries) - 2,
                                     next((i for i in range(len(boundaries) - 1)
                                           if boundaries[i] <= event.time < boundaries[i + 1]),
                                          len(boundaries) - 2)))
            stop_time = event.time + event.dur
            first = start_index
            last = max(first, min(len(boundaries) - 2,
                                  next((i for i in range(first, len(boundaries) - 1)
                                        if boundaries[i] < stop_time <= boundaries[i + 1]),
                                       len(boundaries) - 2)))
            measure_ranges = [(first, event.time, event.time)] if is_grace else [
                (measure_index,
                 max(event.time, boundaries[measure_index]),
                 min(stop_time, boundaries[measure_index + 1]))
                for measure_index in range(first, last + 1)
            ]
            for measure_index, segment_start, segment_stop in measure_ranges:
                measure_start, measure_stop = boundaries[measure_index:measure_index + 2]
                if not is_grace and segment_start >= segment_stop:
                    continue
                if current_measure != measure_index:
                    state["accidentals"] = {}
                    current_measure = measure_index
                invisible = converter.child(event.node, "display") is not None and \
                    converter.child(event.node, "display").get("invisible") == "true"
                if "visible" in override:
                    invisible = not override["visible"]
                pitches = _direct_pitches(event, state, override, invisible)
                source_event = override.get("pitch_source", event)
                heads = converter.children(converter.child(source_event.node, "heads"), "head")
                is_rest = event.kind == "rest" or bool(override.get("print_as_rest"))
                if event.kind == "chord" and heads and invisible and not pitches:
                    is_rest = True
                if is_grace and is_rest:
                    # Grace rests are an engraving placeholder. The native
                    # audit deliberately ignores them because they have no
                    # playback duration.
                    continue
                glyphs = converter.capella_glyphs(event)
                marks = set() if is_rest else _direct_marks(event.node, glyphs)
                if not is_rest:
                    for extra in override.get("extra_articulations", ()):
                        marks.update(_direct_marks(extra, set()))
                if not is_rest:
                    if "t" in glyphs or "¿" in glyphs:
                        marks.add("trill")
                    if any(converter.child(draw, "trill") is not None
                           for draw in converter.children(
                               converter.child(event.node, "drawObjects"), "drawObj")):
                        marks.add("trill")
                    if "l" in glyphs:
                        marks.add("mordent")
                    if "u" in glyphs or "k" in glyphs:
                        marks.add("fermata")
                ties = set()
                if not is_rest and not override.get("replace_source_tie"):
                    pitch_iter = iter(pitches)
                    for head in heads:
                        if head.get("silent") == "true" and invisible:
                            continue
                        pitch = next(pitch_iter, None)
                        if pitch is None:
                            continue
                        split_before = segment_start > event.time
                        split_after = segment_stop < stop_time
                        tie = converter.child(head, "tie")
                        if tie is not None:
                            if tie.get("end") == "true" and not split_before:
                                ties.add((pitch, "stop"))
                            if tie.get("begin") == "true" and not split_after:
                                ties.add((pitch, "start"))
                        if split_before:
                            ties.add((pitch, "stop"))
                        if split_after:
                            ties.add((pitch, "start"))
                slurs = set()
                linked = id(event)
                if segment_start == event.time:
                    slurs.update("start" for _ in slur_starts.get(linked, ()))
                if segment_stop == stop_time:
                    slurs.update("stop" for _ in slur_ends.get(linked, ()))
                durations = [segment_stop - segment_start]
                base = duration_node.get("base", "") if duration_node is not None else ""
                if is_rest and not is_grace and base.isdigit() and int(base) > 1:
                    durations = converter.rest_duration_parts(segment_stop - segment_start)
                onset = segment_start - measure_start
                for duration in durations:
                    events.append(SemanticEvent(
                        part_index, measure_index + 1, event.voice, onset,
                        "rest" if is_rest else "chord", duration,
                        pitches if not is_rest else (), invisible, is_grace,
                        marks if onset == segment_start - measure_start else set(),
                        ties if onset == segment_start - measure_start else set(),
                        slurs if onset == segment_start - measure_start else set(),
                    ))
                    onset += duration
    # The converter intentionally materializes every silent voice gap as a
    # hidden rest. Include those source-owned timing events in this ledger;
    # MusicXML <forward> elements are not reliable across MuseScore import.
    for part_index, part in enumerate(parts, 1):
        for measure_index, (measure_start, measure_stop) in enumerate(
                zip(boundaries, boundaries[1:]), 1):
            source_voices = {
                item.voice for item in part.events
                if (measure_start <= item.time < measure_stop or
                    (item.kind in ("chord", "rest") and
                     item.time < measure_start < item.time + item.dur))
            } or {1}
            measure_duration = measure_stop - measure_start
            for voice in source_voices:
                timed = sorted((item for item in events
                                if item.part == part_index and
                                item.measure == measure_index and
                                item.voice == voice and item.duration > 0),
                               key=lambda item: item.onset)
                cursor = Fraction(0)
                additions = []
                for item in timed:
                    if item.onset > cursor:
                        additions.append(SemanticEvent(
                            part_index, measure_index, voice, cursor,
                            "rest", item.onset - cursor, (), True))
                    cursor = max(cursor, item.onset + item.duration)
                if cursor < measure_duration:
                    additions.append(SemanticEvent(
                        part_index, measure_index, voice, cursor,
                        "rest", measure_duration - cursor, (), True))
                events.extend(additions)
    controls = {"dynamics": set(), "hairpins": set()}
    for part_index, part in enumerate(parts, 1):
        ordered = sorted(part.events, key=lambda item: (item.time, item.voice))
        wedge_starts, _ = converter.make_wedge_links(ordered)
        for event in ordered:
            if event.kind not in ("chord", "rest") or not (0 <= event.time < end):
                continue
            measure_index = next(
                (index for index in range(len(boundaries) - 1)
                 if boundaries[index] <= event.time < boundaries[index + 1]),
                len(boundaries) - 2)
            onset = event.time - boundaries[measure_index]
            for glyph in converter.capella_glyphs(event):
                dynamic = converter.CAPELLA_DYNAMICS.get(glyph)
                if dynamic:
                    controls["dynamics"].add((
                        part_index, measure_index + 1, event.voice,
                        onset, dynamic))
            for _, kind, _ in wedge_starts.get(id(event), ()):
                controls["hairpins"].add((
                    part_index, measure_index + 1, event.voice, onset, kind))
    return events, rest_overrides, controls


def audit_capx_against_musicxml(raw: bytes, musicxml: bytes | Path) -> dict:
    """Fail if the generated MusicXML changes CAPX events or playback marks."""
    root = converter.read_capx(raw)
    expected_events, overrides, controls = _direct_snapshot(root)
    try:
        xml_root = ET.fromstring(
            musicxml if isinstance(musicxml, bytes)
            else Path(musicxml).read_bytes()
        )
    except (OSError, ET.XMLSyntaxError) as exc:
        raise SemanticIntegrityError("Could not read generated MusicXML") from exc
    actual = _source_snapshot(xml_root)
    expected = type(actual)(
        sorted(expected_events,
               key=lambda e: (e.part, e.measure, e.voice, e.onset, e.kind)),
        set(), set(), 0,
    )
    _complete_tie_chains(expected.events)
    _complete_tie_chains(actual.events)
    actual_events = _filtered_events(actual, expected)
    expected_fingerprints = [event.fingerprint() for event in expected.events]
    actual_fingerprints = [event.fingerprint() for event in actual_events]
    if expected_fingerprints != actual_fingerprints:
        first = next((i for i, pair in enumerate(zip(expected_fingerprints, actual_fingerprints))
                      if pair[0] != pair[1]), min(len(expected_fingerprints), len(actual_fingerprints)))
        location = (expected.events[first].location() if first < len(expected.events)
                    else "end of source event stream")
        raise SemanticIntegrityError(
            f"CAPX-to-MusicXML event changed at {location}: "
            f"source={expected_fingerprints[first] if first < len(expected_fingerprints) else None} "
            f"musicxml={actual_fingerprints[first] if first < len(actual_fingerprints) else None}"
        )
    for label in ("dynamics", "hairpins"):
        expected_controls = controls[label]
        actual_controls = getattr(actual, label)
        if expected_controls != actual_controls:
            raise SemanticIntegrityError(
                f"CAPX-to-MusicXML {label} changed: "
                f"source={sorted(expected_controls)} "
                f"musicxml={sorted(actual_controls)}")
    return {
        "status": "strict",
        "source_events": len(expected.events),
        "musicxml_events": len(actual_events),
        "normalized_rest_engravings": len(overrides),
        "source_fermata_events": sum("fermata" in e.marks for e in expected.events),
        "musicxml_fermata_events": sum("fermata" in e.marks for e in actual_events),
        "source_hidden_pitched_events": sum(e.hidden and bool(e.pitches) for e in expected.events),
        "source_hidden_rest_events": sum(e.hidden and e.kind == "rest" for e in expected.events),
        "source_dynamics": len(controls["dynamics"]),
        "musicxml_dynamics": len(actual.dynamics),
        "source_hairpins": len(controls["hairpins"]),
        "musicxml_hairpins": len(actual.hairpins),
        "transformation_counts": dict(converter.collections.Counter(
            override.get("rule", "legacy-rest-normalization")
            for override in overrides.values()
        )),
    }
