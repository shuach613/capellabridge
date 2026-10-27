"""Compare MusicXML source music with the native MuseScore score.

The structural validator in :mod:`score_validation` proves that an MSCZ is
readable and rhythmically complete.  This module checks the more important
question: whether the music survived the MuseScore round trip.  It keeps the
comparison deliberately small and canonical, normalizing MuseScore's native
names without creating a second notation-conversion pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from collections import Counter
import zipfile
from pathlib import Path

from lxml import etree as ET


class SemanticIntegrityError(ValueError):
    """Raised when the exported score changes playback-relevant music."""


@dataclass
class SemanticEvent:
    part: int
    measure: int
    voice: int
    onset: Fraction
    kind: str
    duration: Fraction
    pitches: tuple[int, ...] = ()
    hidden: bool = False
    grace: bool = False
    marks: set[str] = field(default_factory=set)
    ties: set[tuple[int, str]] = field(default_factory=set)
    slurs: set[str] = field(default_factory=set)

    def fingerprint(self):
        return (
            self.part, self.measure, self.voice, self.onset, self.kind,
            self.duration, self.pitches, self.hidden, self.grace,
            tuple(sorted(self.marks)), tuple(sorted(self.ties)),
            tuple(sorted(self.slurs)),
        )

    def location(self) -> str:
        return (f"part {self.part}, measure {self.measure}, voice {self.voice}, "
                f"onset {self.onset}")


@dataclass
class SemanticSnapshot:
    events: list[SemanticEvent]
    breaths: set[tuple[int, int, int, Fraction]]
    repeats: set[tuple[int, str]]
    ignored_grace_rests: int = 0
    volta_starts: set[tuple[int, str]] = field(default_factory=set)
    hairpins: set[tuple[int, int, int, Fraction, str]] = field(default_factory=set)
    tuplets: set[tuple[int, int, str]] = field(default_factory=set)
    tempos: set[tuple[int, int, int, Fraction, Fraction]] = field(default_factory=set)
    dynamics: set[tuple[int, int, int, Fraction, str]] = field(default_factory=set)
    time_signatures: set[tuple[int, int, int, int]] = field(default_factory=set)
    key_signatures: set[tuple[int, int, int]] = field(default_factory=set)
    navigation: set[tuple[int, int, int, Fraction, str, str]] = field(default_factory=set)
    volta_spans: set[tuple[int, int, str]] = field(default_factory=set)
    open_tuplets: set[tuple[int, int, int]] = field(default_factory=set)


SOURCE_MARKS = {
    "accent": "accent",
    "strong-accent": "strong-accent",
    "staccato": "staccato",
    "staccatissimo": "staccatissimo",
    "tenuto": "tenuto",
    "detached-legato": "detached-legato",
    "fermata": "fermata",
    "trill-mark": "trill",
    "turn": "turn",
    "inverted-turn": "inverted-turn",
    "mordent": "mordent",
    "inverted-mordent": "inverted-mordent",
}

NATIVE_MARKS = {
    "articAccentAbove": "accent",
    "articAccentBelow": "accent",
    "articMarcatoAbove": "strong-accent",
    "articMarcatoBelow": "strong-accent",
    "articStaccatoAbove": "staccato",
    "articStaccatoBelow": "staccato",
    "articStaccatissimoAbove": "staccatissimo",
    "articStaccatissimoBelow": "staccatissimo",
    "articTenutoAbove": "tenuto",
    "articTenutoBelow": "tenuto",
    "articTenutoStaccatoAbove": "detached-legato",
    "articTenutoStaccatoBelow": "detached-legato",
    "articFermataAbove": "fermata",
    "articFermataBelow": "fermata",
    "fermataAbove": "fermata",
    "fermataBelow": "fermata",
    "fermata": "fermata",
    "ornamentTrill": "trill",
    "ornamentTurn": "turn",
    "ornamentTurnInverted": "inverted-turn",
    "ornamentMordent": "inverted-mordent",
    # MuseScore 3 uses this counter-intuitive native name for MusicXML's
    # ordinary <mordent>. Treating it as inverted rejected correct imports.
    "ornamentMordentInverted": "mordent",
    "ornamentShortMordent": "mordent",
    "ornamentPrall": "inverted-mordent",
}

NATIVE_DURATIONS = {
    "longa": Fraction(4), "breve": Fraction(2),
    "whole": Fraction(1), "half": Fraction(1, 2),
    "quarter": Fraction(1, 4), "eighth": Fraction(1, 8),
    "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128),
    "256th": Fraction(1, 256), "512th": Fraction(1, 512),
    "1024th": Fraction(1, 1024),
}
NATIVE_GRACE_TAGS = {
    f"grace{denom}{suffix}"
    for denom in (4, 8, 16, 32, 64, 128)
    for suffix in ("", "after")
}
NATIVE_GRACE_TAGS.update({"grace", "appoggiatura", "acciaccatura"})


def _fraction(value, label="value") -> Fraction:
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise SemanticIntegrityError(f"Invalid {label}: {value!r}") from exc


def _pitch(step: str, octave: str, alter: str | None = None) -> int:
    base = {"C": 0, "D": 2, "E": 4, "F": 5,
            "G": 7, "A": 9, "B": 11}
    if step not in base:
        raise SemanticIntegrityError(f"Invalid MusicXML pitch step: {step!r}")
    return base[step] + (int(octave) + 1) * 12 + int(alter or 0)


def _source_marks(note) -> tuple[set[str], set[tuple[int, str]], set[str], bool]:
    marks: set[str] = set()
    ties: set[tuple[int, str]] = set()
    slurs: set[str] = set()
    breath = False
    pitch = note.find("pitch")
    midi = None
    if pitch is not None:
        midi = _pitch(pitch.findtext("step", "C"),
                      pitch.findtext("octave", "4"),
                      pitch.findtext("alter"))
    for node in note.iter():
        name = ET.QName(node).localname
        if name == "breath-mark":
            breath = True
        if name in SOURCE_MARKS:
            marks.add(SOURCE_MARKS[name])
        if name in ("tie", "tied") and node.get("type") in ("start", "stop"):
            if midi is not None:
                ties.add((midi, node.get("type")))
        if name == "slur" and node.get("type") in ("start", "stop"):
            slurs.add(node.get("type"))
    return marks, ties, slurs, breath


def _musicxml_staff_count(part) -> int:
    """Return the number of physical staves represented by a MusicXML part."""
    count = 1
    for staves in part.findall(".//attributes/staves"):
        try:
            count = max(count, int(staves.text or "1"))
        except ValueError as exc:
            raise SemanticIntegrityError("Invalid MusicXML staves count") from exc
    for staff in part.findall(".//note/staff"):
        try:
            count = max(count, int(staff.text or "1"))
        except ValueError as exc:
            raise SemanticIntegrityError("Invalid MusicXML staff number") from exc
    return count


def _source_snapshot(root) -> SemanticSnapshot:
    events: dict[tuple[int, int, int, Fraction], SemanticEvent] = {}
    breaths: set[tuple[int, int, int, Fraction]] = set()
    repeats: set[tuple[int, str]] = set()
    volta_starts: set[tuple[int, str]] = set()
    hairpins: set[tuple[int, int, int, Fraction, str]] = set()
    tuplets: set[tuple[int, int, str]] = set()
    tempos: set[tuple[int, int, int, Fraction, Fraction]] = set()
    dynamics: set[tuple[int, int, int, Fraction, str]] = set()
    time_signatures: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    key_signatures: dict[tuple[int, int], tuple[int, int, int]] = {}
    navigation: set[tuple[int, int, int, Fraction, str, str]] = set()
    volta_spans: set[tuple[int, int, str]] = set()
    ignored_grace_rests = 0
    staff_offset = 0
    for source_part_index, part in enumerate(root.findall("part"), 1):
        # MusicXML can represent a four-staff ensemble either as four parts
        # or as one part with four <staff> lanes. MuseScore's native score
        # always uses physical Staff objects. Use the same physical-staff
        # indexing in both snapshots so CAP exports such as Pizzicato Polka
        # are compared as music rather than as a serialization accident.
        staff_count = _musicxml_staff_count(part)
        default_part_index = staff_offset + 1
        divisions = 1
        active_endings: dict[str, int] = {}
        for measure_index, measure in enumerate(part.findall("measure"), 1):
            cursor = Fraction(0)
            last_onset = Fraction(0)
            grace_ordinals: dict[tuple[int, Fraction], int] = {}
            for item in measure:
                name = ET.QName(item).localname
                if name == "attributes":
                    value = item.findtext("divisions")
                    if value:
                        divisions = int(value)
                    key = item.findtext("key/fifths")
                    if key is not None:
                        key_signatures[(default_part_index, measure_index)] = (
                            default_part_index, measure_index, int(key))
                    beats = item.findtext("time/beats")
                    beat_type = item.findtext("time/beat-type")
                    if beats and beat_type:
                        time_signatures[(default_part_index, measure_index)] = (
                            default_part_index, measure_index,
                            int(beats), int(beat_type))
                    continue
                if name in ("backup", "forward"):
                    duration = _fraction(item.findtext("duration", "0"),
                                         "MusicXML duration") / (divisions * 4)
                    cursor += duration if name == "forward" else -duration
                    if cursor < 0:
                        raise SemanticIntegrityError(
                            f"Negative MusicXML cursor in part {part_index}, "
                            f"measure {measure_index}")
                    continue
                if name == "barline":
                    for repeat in item.findall("repeat"):
                        direction = repeat.get("direction")
                        if direction:
                            repeats.add((measure_index, direction))
                    for ending in item.findall("ending"):
                        number = ending.get("number", "")
                        if ending.get("type") == "start":
                            volta_starts.add((measure_index, number))
                            active_endings[number] = measure_index
                        elif ending.get("type") in ("stop", "discontinue"):
                            start_measure = active_endings.pop(number, None)
                            if start_measure is not None:
                                volta_spans.add((start_measure, measure_index,
                                                 number))
                    continue
                if name == "direction":
                    voice = int(item.findtext("voice", "1"))
                    offset = (_fraction(item.findtext("offset", "0"),
                                        "MusicXML direction offset") /
                              (divisions * 4))
                    direction_onset = cursor + offset
                    for sound in item.findall("sound"):
                        if sound.get("tempo"):
                            tempos.add((default_part_index, measure_index, voice,
                                        direction_onset,
                                        _fraction(sound.get("tempo"), "tempo")))
                        for attribute in ("dacapo", "dalsegno", "tocoda",
                                          "coda", "segno", "fine"):
                            if sound.get(attribute):
                                navigation.add((default_part_index, measure_index, voice,
                                                direction_onset, attribute,
                                                sound.get(attribute)))
                    for dynamic in item.findall("direction-type/dynamics/*"):
                        dynamics.add((default_part_index, measure_index, voice,
                                      direction_onset,
                                      ET.QName(dynamic).localname))
                    for wedge in item.findall("direction-type/wedge"):
                        # Native MuseScore stores one HairPin spanner per
                        # wedge, with the endpoint represented by next/prev;
                        # count only the MusicXML start marker here.
                        if wedge.get("type") != "stop":
                            hairpins.add((default_part_index, measure_index, voice,
                                          direction_onset,
                                          wedge.get("type", "")))
                    continue
                if name == "sound":
                    if item.get("tempo"):
                        tempos.add((default_part_index, measure_index, 1, cursor,
                                    _fraction(item.get("tempo"), "tempo")))
                    for attribute in ("dacapo", "dalsegno", "tocoda",
                                      "coda", "segno", "fine"):
                        if item.get(attribute):
                            navigation.add((default_part_index, measure_index, 1,
                                            cursor, attribute,
                                            item.get(attribute)))
                    continue
                if name != "note":
                    continue
                voice = int(item.findtext("voice", "1"))
                try:
                    staff_number = int(item.findtext("staff", "1"))
                except ValueError as exc:
                    raise SemanticIntegrityError(
                        "Invalid MusicXML staff number") from exc
                if not 1 <= staff_number <= staff_count:
                    raise SemanticIntegrityError(
                        f"MusicXML staff {staff_number} is outside the declared "
                        f"range 1-{staff_count}")
                part_index = staff_offset + staff_number
                grace = item.find("grace") is not None
                duration = Fraction(0) if grace else (
                    _fraction(item.findtext("duration", "0"),
                              "MusicXML duration") / (divisions * 4)
                )
                rest = item.find("rest") is not None
                if rest and grace:
                    ignored_grace_rests += 1
                # Grace notes/rests share the following performed onset but
                # are separate notation objects. Keep them separate so a
                # missing grace placeholder cannot hide a changed sounding
                # note at the same onset.
                onset = last_onset if item.find("chord") is not None else cursor
                ordinal = 0
                if grace and item.find("chord") is None:
                    ordinal = grace_ordinals.get((voice, onset), 0)
                    grace_ordinals[(voice, onset)] = ordinal + 1
                key = (part_index, measure_index, voice, onset, grace, ordinal)
                event = events.get(key)
                if event is None:
                    event = SemanticEvent(
                        part_index, measure_index, voice, cursor,
                        "rest" if rest else "chord", duration,
                        hidden=item.get("print-object") == "no",
                        grace=grace,
                    )
                    events[key] = event
                elif not rest:
                    event.kind = "chord"
                    event.duration = max(event.duration, duration)
                    event.hidden = event.hidden and item.get("print-object") == "no"
                if not rest:
                    pitch = item.find("pitch")
                    if pitch is not None:
                        value = _pitch(pitch.findtext("step", "C"),
                                       pitch.findtext("octave", "4"),
                                       pitch.findtext("alter"))
                        event.pitches = tuple(sorted(set(event.pitches + (value,))))
                marks, ties, slurs, has_breath = _source_marks(item)
                event.marks.update(marks)
                event.ties.update(ties)
                event.slurs.update(slurs)
                for tuplet in item.findall("notations/tuplet"):
                    tuplets.add((measure_index, voice, tuplet.get("type", "")))
                if has_breath:
                    breaths.add((part_index, measure_index, voice,
                                 onset + duration))
                if item.find("chord") is None:
                    last_onset = cursor
                    cursor += duration
        staff_offset += staff_count
    return SemanticSnapshot(
        sorted(events.values(), key=lambda e: (e.part, e.measure, e.voice, e.onset)),
        breaths, repeats, ignored_grace_rests, volta_starts, hairpins,
        tuplets, tempos, dynamics, set(time_signatures.values()),
        set(key_signatures.values()), navigation, volta_spans,
    )


def _native_duration(obj, meter: Fraction, tuplet_scale: Fraction) -> Fraction:
    if obj.tag == "Chord" and any(obj.find(tag) is not None for tag in NATIVE_GRACE_TAGS):
        return Fraction(0)
    if obj.findtext("durationType") == "measure":
        return meter
    explicit = obj.findtext("duration")
    if explicit:
        return _fraction(explicit, "native duration") * tuplet_scale
    kind = obj.findtext("durationType")
    if kind not in NATIVE_DURATIONS:
        raise SemanticIntegrityError(f"Unsupported native duration: {kind!r}")
    dots = int(obj.findtext("dots", "0"))
    if not 0 <= dots <= 4:
        raise SemanticIntegrityError(f"Unsupported native dot count: {dots}")
    nominal = NATIVE_DURATIONS[kind]
    duration = nominal * sum((Fraction(1, 2 ** power)
                              for power in range(dots + 1)), Fraction(0))
    return duration * tuplet_scale


def _native_marks(chord) -> set[str]:
    marks = set()
    for articulation in chord.findall("Articulation"):
        subtype = articulation.findtext("subtype")
        if subtype in NATIVE_MARKS:
            marks.add(NATIVE_MARKS[subtype])
    for spanner in chord.findall('Spanner[@type="Trill"]'):
        if _native_spanner_mark(spanner):
            marks.add("trill")
    return marks


def _native_spanner_mark(item) -> str | None:
    """Map native ornament spanners to the event mark they affect."""
    if item.tag != "Spanner" or item.get("type") != "Trill":
        return None
    trill = item.find("Trill")
    if trill is None:
        return None
    subtype = (trill.findtext("subtype") or "trill").strip().lower()
    return "trill" if "trill" in subtype else None


def _native_volta_spans(staff):
    """Read complete native volta lines, including connector measures."""
    measures = staff.findall("Measure")
    spans = set()
    for start_measure, measure in enumerate(measures, 1):
        for voice in measure.findall("voice"):
            for spanner in voice.findall('Spanner[@type="Volta"]'):
                volta = spanner.find("Volta")
                if volta is None or not volta.findtext("endings"):
                    continue
                try:
                    step = int(spanner.findtext(
                        "next/location/measures", "1"))
                except ValueError as exc:
                    raise SemanticIntegrityError(
                        "Invalid native volta span") from exc
                end_measure = start_measure + max(1, step) - 1
                if end_measure > len(measures):
                    # Keep the impossible span in the semantic set so the
                    # caller receives the same precise "Volta spans changed"
                    # diagnostic as any other navigation mismatch. Native
                    # structural validation reports the out-of-range path.
                    spans.add((start_measure, end_measure,
                               volta.findtext("endings", "")))
                    continue
                while end_measure < len(measures):
                    # A connector and the next numbered ending can share a
                    # measure boundary. The new Volta object starts a new
                    # span; do not include that measure in the prior span.
                    # The prev offset is authoritative: a connector belongs
                    # only to the start object it points back to. Looking only
                    # for any prev connector incorrectly joined adjacent first
                    # and second endings in imported CAP scores.
                    connector_measure = end_measure + 1
                    connector = any(
                        candidate.find("Volta") is None
                        # A terminal connector has only <prev>; it marks the
                        # exclusive barline endpoint and does not add the next
                        # measure to the ending. Only a connector which also
                        # continues with <next> extends a multi-system volta.
                        and candidate.find("next/location") is not None
                        and _volta_connector_targets(
                            candidate, connector_measure, start_measure)
                        for next_voice in measures[end_measure].findall("voice")
                        for candidate in next_voice.findall(
                            'Spanner[@type="Volta"]')
                    ) and not any(
                        candidate.find("Volta") is not None
                        for next_voice in measures[end_measure].findall("voice")
                        for candidate in next_voice.findall(
                            'Spanner[@type="Volta"]')
                    )
                    if not connector:
                        break
                    end_measure += 1
                spans.add((start_measure, end_measure,
                           volta.findtext("endings", "")))
    return spans


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


def _native_snapshot(root) -> SemanticSnapshot:
    score = root.find("Score")
    if score is None:
        raise SemanticIntegrityError("MSCX contains no Score")
    transpose = {}
    for part in score.findall("Part"):
        instrument = part.find("Instrument")
        value = 0
        if instrument is not None:
            value = int(instrument.findtext("transposeChromatic", "0"))
        for staff in part.findall("Staff"):
            transpose[staff.get("id")] = value

    events: list[SemanticEvent] = []
    breaths: set[tuple[int, int, int, Fraction]] = set()
    repeats: set[tuple[int, str]] = set()
    volta_starts: set[tuple[int, str]] = set()
    hairpins: set[tuple[int, int, int, Fraction, str]] = set()
    tuplets: set[tuple[int, int, str]] = set()
    tempos: set[tuple[int, int, int, Fraction, Fraction]] = set()
    dynamics: set[tuple[int, int, int, Fraction, str]] = set()
    time_signatures: set[tuple[int, int, int, int]] = set()
    key_signatures: set[tuple[int, int, int]] = set()
    navigation: set[tuple[int, int, int, Fraction, str, str]] = set()
    volta_spans: set[tuple[int, int, str]] = set()
    open_tuplets: set[tuple[int, int, int]] = set()
    for part_index, staff in enumerate(score.findall("Staff"), 1):
        staff_id = staff.get("id", str(part_index))
        transposition = transpose.get(staff_id, 0)
        meter = Fraction(4, 4)
        tuplet_stacks: dict[int, list[Fraction]] = {}
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            # A TimeSig belongs to the current measure. Read it before
            # calculating measure rests, then carry it forward.
            for voice in measure.findall("voice"):
                for signature in voice.findall("TimeSig"):
                    try:
                        numerator = int(signature.findtext("sigN", "4"))
                        denominator = int(signature.findtext("sigD", "4"))
                        meter = Fraction(numerator, denominator)
                        time_signatures.add((part_index, measure_index,
                                             numerator, denominator))
                    except (ValueError, ZeroDivisionError) as exc:
                        raise SemanticIntegrityError("Invalid native time signature") from exc
                for signature in voice.findall("KeySig"):
                    value = (signature.findtext("concertKey") or
                             signature.findtext("accidental"))
                    if value is not None:
                        key_signatures.add((part_index, measure_index, int(value)))
            if measure.get("len"):
                measure_meter = _fraction(measure.get("len"), "native measure length")
            else:
                measure_meter = meter
            for barline in measure.findall("BarLine"):
                subtype = barline.findtext("subtype")
                if subtype == "start-repeat":
                    repeats.add((measure_index, "forward"))
                elif subtype == "end-repeat":
                    repeats.add((measure_index, "backward"))
            if measure.find("startRepeat") is not None:
                repeats.add((measure_index, "forward"))
            if measure.find("endRepeat") is not None:
                repeats.add((measure_index, "backward"))
            # MuseScore stores imported Markers and Jumps as direct Measure
            # children, not necessarily inside a voice. They still apply to
            # the leading voice at this measure boundary.
            for item in list(measure):
                if item.tag == "Marker":
                    label = item.findtext("label") or item.findtext("text", "")
                    marker_type = item.findtext("subtype", "marker")
                    navigation.add((part_index, measure_index, 1,
                                    Fraction(0), marker_type, label))
                elif item.tag == "Jump":
                    value = "|".join((item.findtext("jumpTo", ""),
                                      item.findtext("playUntil", ""),
                                      item.findtext("continueAt", "")))
                    navigation.add((part_index, measure_index, 1,
                                    Fraction(0), "jump", value))
            for voice_index, voice in enumerate(measure.findall("voice"), 1):
                for spanner in voice.findall('Spanner[@type="Volta"]'):
                    volta = spanner.find("Volta")
                    if volta is not None and volta.findtext("endings"):
                        number = volta.findtext("endings", "")
                        volta_starts.add((measure_index, number))
                cursor = Fraction(0)
                # Native MuseScore permits a tuplet to cross a barline. Keep
                # the stack per voice for the complete staff instead of
                # restarting it at every measure; otherwise the audit can
                # assign the wrong durations or reject a valid closing marker.
                tuplet_stack = tuplet_stacks.setdefault(voice_index, [])
                pending_marks: set[str] = set()
                for item in voice:
                    if item.tag == "Tuplet":
                        tuplets.add((measure_index, voice_index, "start"))
                        try:
                            tuplet_stack.append(Fraction(
                                int(item.findtext("normalNotes", "2")),
                                int(item.findtext("actualNotes", "3"))))
                        except (ValueError, ZeroDivisionError) as exc:
                            raise SemanticIntegrityError("Invalid native tuplet") from exc
                        continue
                    if item.tag == "endTuplet":
                        tuplets.add((measure_index, voice_index, "stop"))
                        if not tuplet_stack:
                            raise SemanticIntegrityError("Unexpected native endTuplet")
                        tuplet_stack.pop()
                        continue
                    if item.tag == "Breath":
                        breaths.add((part_index, measure_index, voice_index, cursor))
                        continue
                    if item.tag == "Fermata":
                        subtype = item.findtext("subtype", "fermata")
                        pending_marks.add(NATIVE_MARKS.get(subtype, "fermata"))
                        continue
                    if item.tag == "Dynamic":
                        subtype = item.findtext("subtype")
                        if subtype:
                            dynamics.add((part_index, measure_index, voice_index,
                                          cursor, subtype))
                        continue
                    if item.tag == "Tempo":
                        value = item.findtext("tempo") or item.get("tempo")
                        if value:
                            # MSCX stores quarter notes per second.
                            tempos.add((part_index, measure_index, voice_index,
                                        cursor, _fraction(value, "native tempo") * 60))
                        continue
                    spanner_mark = _native_spanner_mark(item)
                    if spanner_mark:
                        pending_marks.add(spanner_mark)
                        continue
                    if item.tag == "Spanner" and item.get("type") == "HairPin":
                        hairpin = item.find("HairPin")
                        if hairpin is not None:
                            subtype = hairpin.findtext("subtype", "")
                            kind = {"0": "crescendo", "1": "diminuendo"}.get(
                                subtype, subtype)
                            hairpins.add((part_index, measure_index, voice_index,
                                          cursor, kind))
                        continue
                    if item.tag == "Marker":
                        label = item.findtext("label") or item.findtext("text", "")
                        marker_type = item.findtext("subtype", "marker")
                        navigation.add((part_index, measure_index, voice_index,
                                        cursor, marker_type, label))
                        continue
                    if item.tag == "Jump":
                        value = "|".join((item.findtext("jumpTo", ""),
                                          item.findtext("playUntil", ""),
                                          item.findtext("continueAt", "")))
                        navigation.add((part_index, measure_index, voice_index,
                                        cursor, "jump", value))
                        continue
                    if item.tag not in ("Chord", "Rest"):
                        continue
                    scale = Fraction(1)
                    for ratio in tuplet_stack:
                        scale *= ratio
                    grace = item.tag == "Chord" and any(
                        item.find(tag) is not None for tag in NATIVE_GRACE_TAGS)
                    duration = _native_duration(item, measure_meter, scale)
                    hidden_chord = item.findtext("visible") == "0"
                    pitches = []
                    ties: set[tuple[int, str]] = set()
                    if item.tag == "Chord":
                        notes = item.findall("Note")
                        note_hidden = []
                        for note in notes:
                            hidden = note.findtext("visible") == "0"
                            note_hidden.append(hidden)
                            raw_pitch = note.findtext("pitch")
                            if raw_pitch is None:
                                continue
                            value = int(raw_pitch) - transposition
                            pitches.append(value)
                            for spanner in note.findall("Spanner"):
                                if spanner.get("type") != "Tie":
                                    continue
                                if spanner.find("next") is not None:
                                    ties.add((value, "start"))
                                if spanner.find("prev") is not None:
                                    ties.add((value, "stop"))
                        if notes:
                            hidden_chord = hidden_chord or all(note_hidden)
                    slurs = set()
                    for spanner in item.findall("Spanner"):
                        if spanner.get("type") != "Slur":
                            continue
                        if spanner.find("next") is not None:
                            slurs.add("start")
                        if spanner.find("prev") is not None:
                            slurs.add("stop")
                    marks = _native_marks(item)
                    marks.update(pending_marks)
                    pending_marks.clear()
                    events.append(SemanticEvent(
                        part_index, measure_index, voice_index, cursor,
                        "chord" if item.tag == "Chord" else "rest", duration,
                        tuple(sorted(pitches)), hidden_chord, grace,
                        marks, ties, slurs,
                    ))
                    cursor += duration
        volta_spans.update(_native_volta_spans(staff))
        for voice_index, stack in tuplet_stacks.items():
            if stack:
                open_tuplets.add((part_index, voice_index, len(stack)))
    return SemanticSnapshot(
        sorted(events, key=lambda e: (e.part, e.measure, e.voice, e.onset)),
        breaths, repeats, 0, volta_starts, hairpins, tuplets, tempos,
        dynamics, time_signatures, key_signatures, navigation, volta_spans,
        open_tuplets,
    )


def _load_native(path: Path):
    try:
        with zipfile.ZipFile(path) as archive:
            name = next((name for name in archive.namelist()
                         if name.endswith(".mscx")), None)
            if not name:
                raise SemanticIntegrityError("MSCZ contains no native MSCX score")
            return ET.fromstring(archive.read(name))
    except (zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
        raise SemanticIntegrityError("Could not read native MSCZ for semantic audit") from exc


def _filtered_events(snapshot: SemanticSnapshot, source: SemanticSnapshot):
    source_timed = {
        (event.part, event.measure)
        for event in source.events if event.duration > 0
    }
    filtered = []
    for event in snapshot.events:
        if event.duration == 0 and event.kind == "rest":
            continue
        # MuseScore fills measures for a staff that does not exist in the
        # corresponding Capella system with hidden measure rests. They are
        # not musical events and should not be mistaken for missing rests.
        if (event.kind == "rest" and event.hidden and
                (event.part, event.measure) not in source_timed):
            continue
        filtered.append(event)
    return filtered


def _complete_tie_chains(events: list[SemanticEvent]) -> None:
    """Add implicit stop endpoints MuseScore stores on a continued tie.

    MusicXML commonly writes a repeated tied pitch as ``start`` on each
    source note and only writes ``stop`` on the final note. MuseScore stores
    both sides of each native Tie spanner. Completing the chain makes the two
    representations comparable without changing the score's meaning.
    """
    by_voice = {}
    for event in events:
        by_voice.setdefault((event.part, event.voice), []).append(event)
    for voice_events in by_voice.values():
        active: set[int] = set()
        for event in sorted(voice_events, key=lambda item: (item.measure, item.onset)):
            for pitch in event.pitches:
                if pitch in active:
                    event.ties.add((pitch, "stop"))
            for pitch, endpoint in tuple(event.ties):
                if endpoint == "stop":
                    active.discard(pitch)
            for pitch, endpoint in tuple(event.ties):
                if endpoint == "start":
                    active.add(pitch)


def _allowed_tail_padding(source_events, native_events, key, left, right):
    """Allow only MuseScore's known silent fill after an explicit source rest."""
    if left or len(right) != 1:
        return False
    extra = right[0]
    if (extra.kind != "rest" or not extra.hidden or
            extra.duration <= 0 or extra.duration > Fraction(1, 64)):
        return False
    same_voice = [event for event in source_events
                  if (event.part, event.measure, event.voice) == key[:3]]
    if not same_voice:
        return False
    last = max(same_voice, key=lambda event: event.onset + event.duration)
    return (last.kind == "rest" and last.hidden and
            last.onset + last.duration == extra.onset)


def _compare_events(source: SemanticSnapshot, native: SemanticSnapshot):
    _complete_tie_chains(source.events)
    _complete_tie_chains(native.events)
    source_events = _filtered_events(source, source)
    native_events = _filtered_events(native, source)
    source_by_key = {}
    native_by_key = {}
    for event in source_events:
        source_by_key.setdefault((event.part, event.measure, event.voice, event.onset), []).append(event)
    for event in native_events:
        native_by_key.setdefault((event.part, event.measure, event.voice, event.onset), []).append(event)
    keys = sorted(set(source_by_key) | set(native_by_key))
    allowed_tail_padding = 0
    for key in keys:
        left = source_by_key.get(key, [])
        right = native_by_key.get(key, [])
        if len(left) != len(right):
            if _allowed_tail_padding(source_events, native_events, key, left, right):
                allowed_tail_padding += right[0].duration
                continue
            location = (f"part {key[0]}, measure {key[1]}, voice {key[2]}, "
                        f"onset {key[3]}")
            raise SemanticIntegrityError(
                f"Musical event count changed at {location}: "
                f"source {len(left)}, native {len(right)}")
        for source_event, native_event in zip(left, right):
            if source_event.fingerprint() != native_event.fingerprint():
                raise SemanticIntegrityError(
                    f"Musical event changed at {source_event.location()}: "
                    f"source={source_event.fingerprint()} native="
                    f"{native_event.fingerprint()}")
    return allowed_tail_padding


def audit_musicxml_against_mscz(source_xml: Path, mscz: Path) -> dict:
    """Fail if playback-relevant MusicXML and native MSCX music differ."""
    try:
        source_root = ET.parse(str(source_xml)).getroot()
    except (OSError, ET.XMLSyntaxError) as exc:
        raise SemanticIntegrityError("Could not read MusicXML for semantic audit") from exc
    source = _source_snapshot(source_root)
    native = _native_snapshot(_load_native(Path(mscz)))
    tail_padding = _compare_events(source, native)
    if source.repeats != native.repeats:
        raise SemanticIntegrityError(
            f"Repeat markers changed: source={sorted(source.repeats)} "
            f"native={sorted(native.repeats)}")
    if source.breaths != native.breaths:
        raise SemanticIntegrityError(
            f"Breath marks changed: source={sorted(source.breaths)} "
            f"native={sorted(native.breaths)}")
    if source.volta_starts != native.volta_starts:
        raise SemanticIntegrityError(
            f"Volta starts changed: source={sorted(source.volta_starts)} "
            f"native={sorted(native.volta_starts)}")
    if source.volta_spans != native.volta_spans:
        raise SemanticIntegrityError(
            f"Volta spans changed: source={sorted(source.volta_spans)} "
            f"native={sorted(native.volta_spans)}")
    # Wedges are staff-level directions. MuseScore may move one between
    # voices while preserving the exact staff and onset.
    source_hairpins = {(part, measure, onset, kind)
                       for part, measure, _voice, onset, kind
                       in source.hairpins}
    native_hairpins = {(part, measure, onset, kind)
                       for part, measure, _voice, onset, kind
                       in native.hairpins}
    if source_hairpins != native_hairpins:
        raise SemanticIntegrityError(
            f"Hairpins changed: source={sorted(source_hairpins)} "
            f"native={sorted(native_hairpins)}")
    for label, left, right in (
        ("Dynamics", source.dynamics, native.dynamics),
        ("Time signatures", source.time_signatures, native.time_signatures),
        # MuseScore omits an explicit initial C-major KeySig because zero is
        # its native default. Later changes back to zero remain significant.
        ("Key signatures",
         {item for item in source.key_signatures
          if not (item[1] == 1 and item[2] == 0)},
         native.key_signatures),
    ):
        if left != right:
            raise SemanticIntegrityError(
                f"{label} changed: source={sorted(left)} native={sorted(right)}")
    # Tempo is score-global. Importers legitimately move duplicate tempo
    # objects from secondary parts to the leading staff. Compare the canonical
    # score timeline and tolerate sub-centi-BPM serialization rounding.
    source_tempos = {
        (measure, onset): value
        for _part, measure, _voice, onset, value in source.tempos
    }
    native_tempos = {
        (measure, onset): value
        for _part, measure, _voice, onset, value in native.tempos
    }
    if (source_tempos.keys() != native_tempos.keys() or any(
            abs(source_tempos[key] - native_tempos[key]) > Fraction(1, 50)
            for key in source_tempos)):
        raise SemanticIntegrityError(
            f"Tempos changed: source={sorted(source.tempos)} "
            f"native={sorted(native.tempos)}")
    source_navigation = {
        (part, measure,
         "marker" if kind in ("segno", "coda", "fine") else "jump")
        for part, measure, _voice, _onset, kind, _ in source.navigation
    }
    native_navigation = {
        (part, measure,
         "jump" if kind == "jump" else "marker")
        for part, measure, _voice, _onset, kind, _ in native.navigation
    }
    if source_navigation != native_navigation:
        raise SemanticIntegrityError(
            "Navigation markers changed: "
            f"source={sorted(source_navigation)} native={sorted(native_navigation)}")
    # MuseScore is allowed to coalesce adjacent tuplets into one native
    # container. Event durations and the source MusicXML tuplet markers still
    # protect playback; boundary counts are reported for review but are not a
    # rejection criterion by themselves.
    return {
        "source_events": len(_filtered_events(source, source)),
        "native_events": len(_filtered_events(native, source)),
        "source_breaths": len(source.breaths),
        "native_breaths": len(native.breaths),
        "source_repeats": len(source.repeats),
        "native_repeats": len(native.repeats),
        "source_volta_starts": len(source.volta_starts),
        "native_volta_starts": len(native.volta_starts),
        "source_volta_spans": len(source.volta_spans),
        "native_volta_spans": len(native.volta_spans),
        "source_hairpins": len(source.hairpins),
        "native_hairpins": len(native.hairpins),
        "source_tuplet_boundaries": len(source.tuplets),
        "native_tuplet_boundaries": len(native.tuplets),
        "source_tempos": len(source.tempos),
        "native_tempos": len(native.tempos),
        "source_dynamics": len(source.dynamics),
        "native_dynamics": len(native.dynamics),
        "source_time_signatures": len(source.time_signatures),
        "native_time_signatures": len(native.time_signatures),
        "source_key_signatures": len(source.key_signatures),
        "native_key_signatures": len(native.key_signatures),
        "source_navigation": len(source.navigation),
        "native_navigation": len(native.navigation),
        "source_open_tuplets": len(source.open_tuplets),
        "native_open_tuplets": len(native.open_tuplets),
        "ignored_grace_rests": source.ignored_grace_rests,
        "source_tail_padding": str(tail_padding),
    }


def audit_cap_musicxml_export(source_xml: Path, mscz: Path) -> dict:
    """Audit a CAP MusicXML export without assuming stable voice numbering.

    MusicXML exported from a native CAP import can renumber voices, merge
    rest-only staves, quantize cursor positions, and encode a multi-measure
    volta with a different span convention. Those are representation changes,
    not necessarily playback changes. Compare the sounding chord/pitch ledger
    and playback feature totals instead of applying the strict CAPX source-ledger
    comparison to this secondary file.
    """
    try:
        source_root = ET.parse(str(source_xml)).getroot()
    except (OSError, ET.XMLSyntaxError) as exc:
        raise SemanticIntegrityError(
            "Could not read exported CAP MusicXML for semantic audit") from exc
    source = _source_snapshot(source_root)
    native = _native_snapshot(_load_native(Path(mscz)))
    source_events = _filtered_events(source, source)
    native_events = _filtered_events(native, source)

    source_counts = Counter(event.kind for event in source_events)
    native_counts = Counter(event.kind for event in native_events)

    # The legacy CAP importer and MuseScore's MusicXML exporter use different
    # silent-rest representations. Several adjacent rests can become one
    # whole-measure rest, or an implicit rest can disappear from the exported
    # event list, while the sounding events and total silence stay unchanged.
    # Compare the sounding pitch ledger by part and measure. MuseScore's
    # secondary MusicXML export can quantize a duration or place a chord at a
    # different cursor position when it normalizes a rest-only voice. The
    # strict CAPX audit remains responsible for exact event timing; this CAP
    # boundary must still reject a lost chord or changed pitch.
    def chord_fingerprint(event):
        return (event.part, event.measure, event.pitches,
                event.hidden, event.grace)

    source_chords = Counter(
        chord_fingerprint(event) for event in source_events
        if event.kind == "chord"
    )
    native_chords = Counter(
        chord_fingerprint(event) for event in native_events
        if event.kind == "chord"
    )
    if source_chords != native_chords:
        raise SemanticIntegrityError(
            "CAP MusicXML sounding events changed: "
            f"source_chords={len(source_chords)} "
            f"native_chords={len(native_chords)}")

    rest_representation_normalized = source_counts["rest"] != native_counts["rest"]

    # These features affect playback or the audible/printed interpretation.
    # A native import may contain an additional copy on a hidden rest-only
    # staff, so only a loss is rejected here; exact identity is already used
    # by the strict CAPX audit where a source ledger exists.
    feature_counts = (
        ("breaths", len(source.breaths), len(native.breaths)),
        ("repeats", len(source.repeats), len(native.repeats)),
        ("volta_starts", len(source.volta_starts), len(native.volta_starts)),
        ("hairpins", len(source.hairpins), len(native.hairpins)),
        ("tempos", len(source.tempos), len(native.tempos)),
        ("dynamics", len(source.dynamics), len(native.dynamics)),
    )
    for label, source_count, native_count in feature_counts:
        if native_count < source_count:
            raise SemanticIntegrityError(
                f"CAP MusicXML export lost {label}: "
                f"source={source_count} native={native_count}")

    source_marks = Counter(mark for event in source_events for mark in event.marks)
    native_marks = Counter(mark for event in native_events for mark in event.marks)
    lost_marks = {
        mark: count - native_marks.get(mark, 0)
        for mark, count in source_marks.items()
        if count > native_marks.get(mark, 0)
    }
    if lost_marks:
        raise SemanticIntegrityError(
            f"CAP MusicXML export lost musical marks: {dict(sorted(lost_marks.items()))}")

    # Exported MusicXML may use a different transposition spelling, so tie
    # endpoints must be compared by start/stop role rather than raw pitch.
    source_ties = Counter(endpoint for event in source_events
                          for _pitch, endpoint in event.ties)
    native_ties = Counter(endpoint for event in native_events
                          for _pitch, endpoint in event.ties)
    if any(native_ties[endpoint] < count
           for endpoint, count in source_ties.items()):
        raise SemanticIntegrityError(
            f"CAP MusicXML export lost tie endpoints: source={dict(source_ties)} "
            f"native={dict(native_ties)}")
    source_slurs = Counter(mark for event in source_events for mark in event.slurs)
    native_slurs = Counter(mark for event in native_events for mark in event.slurs)
    if any(native_slurs[mark] < count for mark, count in source_slurs.items()):
        raise SemanticIntegrityError(
            f"CAP MusicXML export lost slur endpoints: source={dict(source_slurs)} "
            f"native={dict(native_slurs)}")

    result = {
        "source_events": len(source_events),
        "native_events": len(native_events),
        "source_chords": source_counts["chord"],
        "native_chords": native_counts["chord"],
        "source_rests": source_counts["rest"],
        "native_rests": native_counts["rest"],
        "rest_representation_normalized": rest_representation_normalized,
        "source_repeats": len(source.repeats),
        "native_repeats": len(native.repeats),
        "source_volta_starts": len(source.volta_starts),
        "native_volta_starts": len(native.volta_starts),
        "source_hairpins": len(source.hairpins),
        "native_hairpins": len(native.hairpins),
        "source_tempos": len(source.tempos),
        "native_tempos": len(native.tempos),
        "source_dynamics": len(source.dynamics),
        "native_dynamics": len(native.dynamics),
        "source_open_tuplets": len(source.open_tuplets),
        "native_open_tuplets": len(native.open_tuplets),
    }
    return result
