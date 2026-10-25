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
    hairpins: set[tuple[int, int, str]] = field(default_factory=set)
    tuplets: set[tuple[int, int, str]] = field(default_factory=set)
    tempos: set[tuple[int, str]] = field(default_factory=set)


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
    "ornamentMordent": "mordent",
    "ornamentMordentInverted": "inverted-mordent",
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


def _source_snapshot(root) -> SemanticSnapshot:
    events: dict[tuple[int, int, int, Fraction], SemanticEvent] = {}
    breaths: set[tuple[int, int, int, Fraction]] = set()
    repeats: set[tuple[int, str]] = set()
    volta_starts: set[tuple[int, str]] = set()
    hairpins: set[tuple[int, int, str]] = set()
    tuplets: set[tuple[int, int, str]] = set()
    tempos: set[tuple[int, str]] = set()
    ignored_grace_rests = 0
    for part_index, part in enumerate(root.findall("part"), 1):
        divisions = 1
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
                        if ending.get("type") == "start":
                            volta_starts.add((measure_index, ending.get("number", "")))
                    continue
                if name == "direction":
                    voice = int(item.findtext("voice", "1"))
                    for sound in item.findall("sound"):
                        if sound.get("tempo"):
                            tempos.add((measure_index, sound.get("tempo")))
                    for wedge in item.findall("direction-type/wedge"):
                        # Native MuseScore stores one HairPin spanner per
                        # wedge, with the endpoint represented by next/prev;
                        # count only the MusicXML start marker here.
                        if wedge.get("type") != "stop":
                            hairpins.add((measure_index, voice,
                                          wedge.get("type", "")))
                    continue
                if name != "note":
                    continue
                voice = int(item.findtext("voice", "1"))
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
    return SemanticSnapshot(
        sorted(events.values(), key=lambda e: (e.part, e.measure, e.voice, e.onset)),
        breaths, repeats, ignored_grace_rests, volta_starts, hairpins,
        tuplets, tempos,
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
    return marks


def _native_snapshot(root) -> SemanticSnapshot:
    score = root.find("Score")
    if score is None:
        raise SemanticIntegrityError("MSCX contains no Score")
    transpose = {}
    for part in score.findall("Part"):
        staff = part.find("Staff")
        if staff is None:
            continue
        instrument = part.find("Instrument")
        value = 0
        if instrument is not None:
            value = int(instrument.findtext("transposeChromatic", "0"))
        transpose[staff.get("id")] = value

    events: list[SemanticEvent] = []
    breaths: set[tuple[int, int, int, Fraction]] = set()
    repeats: set[tuple[int, str]] = set()
    volta_starts: set[tuple[int, str]] = set()
    hairpins: set[tuple[int, int, str]] = set()
    tuplets: set[tuple[int, int, str]] = set()
    tempos: set[tuple[int, str]] = set()
    for part_index, staff in enumerate(score.findall("Staff"), 1):
        staff_id = staff.get("id", str(part_index))
        transposition = transpose.get(staff_id, 0)
        meter = Fraction(4, 4)
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            # A TimeSig belongs to the current measure. Read it before
            # calculating measure rests, then carry it forward.
            for voice in measure.findall("voice"):
                for signature in voice.findall("TimeSig"):
                    try:
                        meter = Fraction(int(signature.findtext("sigN", "4")),
                                         int(signature.findtext("sigD", "4")))
                    except (ValueError, ZeroDivisionError) as exc:
                        raise SemanticIntegrityError("Invalid native time signature") from exc
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
            for voice_index, voice in enumerate(measure.findall("voice"), 1):
                for spanner in voice.findall('Spanner[@type="Volta"]'):
                    volta = spanner.find("Volta")
                    if volta is not None and volta.findtext("endings"):
                        volta_starts.add((measure_index,
                                          volta.findtext("endings", "")))
                for spanner in voice.findall('Spanner[@type="HairPin"]'):
                    hairpin = spanner.find("HairPin")
                    if hairpin is not None:
                        subtype = hairpin.findtext("subtype", "")
                        hairpins.add((measure_index, voice_index, subtype))
                for tempo in voice.findall("Tempo"):
                    value = tempo.findtext("tempo") or tempo.get("tempo")
                    if value:
                        tempos.add((measure_index, value))
                cursor = Fraction(0)
                tuplet_stack: list[Fraction] = []
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
                    events.append(SemanticEvent(
                        part_index, measure_index, voice_index, cursor,
                        "chord" if item.tag == "Chord" else "rest", duration,
                        tuple(sorted(pitches)), hidden_chord, grace,
                        _native_marks(item), ties, slurs,
                    ))
                    cursor += duration
    return SemanticSnapshot(
        sorted(events, key=lambda e: (e.part, e.measure, e.voice, e.onset)),
        breaths, repeats, 0, volta_starts, hairpins, tuplets, tempos,
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
    if source.breaths and len(source.breaths) != len(native.breaths):
        raise SemanticIntegrityError(
            f"Breath-mark count changed: source {len(source.breaths)}, "
            f"native {len(native.breaths)}")
    if source.volta_starts != native.volta_starts:
        raise SemanticIntegrityError(
            f"Volta starts changed: source={sorted(source.volta_starts)} "
            f"native={sorted(native.volta_starts)}")
    if source.hairpins and len(source.hairpins) != len(native.hairpins):
        raise SemanticIntegrityError(
            f"Hairpin count changed: source {len(source.hairpins)}, "
            f"native {len(native.hairpins)}")
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
        "source_hairpins": len(source.hairpins),
        "native_hairpins": len(native.hairpins),
        "source_tuplet_boundaries": len(source.tuplets),
        "native_tuplet_boundaries": len(native.tuplets),
        "source_tempos": len(source.tempos),
        "native_tempos": len(native.tempos),
        "ignored_grace_rests": source.ignored_grace_rests,
        "source_tail_padding": str(tail_padding),
    }
