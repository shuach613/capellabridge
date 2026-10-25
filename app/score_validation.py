"""Validate MuseScore 3 native scores before offering them to MuseScore Studio 4.

An MSCZ is a ZIP containing MSCX XML, but a well-formed XML file can still
contain incomplete measures. Such measure corruption triggers MuseScore Studio's
"file is corrupted" dialog even though MuseScore 3 rendered the score as PDF.

This module deliberately repairs only a tiny missing duration in an existing,
invisible trailing rest. Other discrepancies fail closed with a precise
staff/measure diagnostic rather than distributing an invalid score.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from fractions import Fraction
from pathlib import Path

from lxml import etree as ET


DURATION_TYPES = {
    "longa": Fraction(4), "breve": Fraction(2),
    "whole": Fraction(1), "half": Fraction(1, 2),
    "quarter": Fraction(1, 4), "eighth": Fraction(1, 8),
    "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128),
    "256th": Fraction(1, 256), "512th": Fraction(1, 512),
    "1024th": Fraction(1, 1024),
}
# Native MuseScore 3 uses <grace8/> for common eighth-note acciaccaturas,
# not just <grace16/> and <grace32/>. These are *not timed* events.
# Match any explicitly supported native grace duration, with optional
# "after" placement, rather than miscounting it as a normal note.
GRACE_TAGS = tuple(
    f"grace{denom}{suffix}"
    for denom in (4, 8, 16, 32, 64, 128)
    for suffix in ("", "after")
) + ("grace", "appoggiatura", "acciaccatura")


class ScoreIntegrityError(ValueError):
    pass


def _duration(obj, meter, tuplet_scale):
    kind = obj.findtext("durationType")
    if obj.tag == "Chord" and any(obj.find(tag) is not None for tag in GRACE_TAGS):
        return Fraction(0)
    if kind == "measure":
        return meter
    explicit = obj.findtext("duration")
    if explicit:
        try:
            return Fraction(explicit)
        except (ZeroDivisionError, ValueError) as exc:
            raise ScoreIntegrityError(f"Invalid duration {explicit!r}") from exc
    if kind not in DURATION_TYPES:
        raise ScoreIntegrityError(f"Unsupported native note duration: {kind!r}")
    nominal = DURATION_TYPES[kind]
    dots = int(obj.findtext("dots", "0"))
    if not 0 <= dots <= 4:
        raise ScoreIntegrityError(f"Unsupported dot count: {dots}")
    dotted = nominal * sum((Fraction(1, 2**k) for k in range(dots + 1)), Fraction(0))
    return dotted * tuplet_scale


def _tuplet_ratio(item):
    """Return the duration multiplier represented by a native tuplet marker."""
    try:
        return Fraction(int(item.findtext("normalNotes", "2")),
                        int(item.findtext("actualNotes", "3")))
    except (ValueError, ZeroDivisionError) as exc:
        raise ScoreIntegrityError("Invalid native tuplet ratio") from exc


def _measure_duration(voice, meter, tuplet_stack=None):
    """Measure one voice while optionally carrying tuplets across barlines.

    MuseScore stores tuplets as a time-ordered ``Tuplet`` marker followed by
    notes and an ``endTuplet`` marker.  The markers are allowed to straddle a
    measure boundary.  A fresh stack for every measure therefore creates
    false "unclosed" errors and, worse, can make a valid score look unsafe.
    Callers that inspect a complete staff pass a mutable stack per voice and
    check it only after the last measure.  The default remains strict for the
    small public helper and existing tests.
    """
    total = Fraction(0)
    ratio_stack = tuplet_stack if tuplet_stack is not None else []
    timed = []
    for item in voice:
        if item.tag == "Tuplet":
            ratio_stack.append(_tuplet_ratio(item))
        elif item.tag == "endTuplet":
            if not ratio_stack:
                raise ScoreIntegrityError("Unexpected tuplet closing marker")
            ratio_stack.pop()
        elif item.tag in ("Chord", "Rest"):
            scale = Fraction(1)
            for ratio in ratio_stack:
                scale *= ratio
            duration = _duration(item, meter, scale)
            total += duration
            timed.append((item, duration))
    if tuplet_stack is None and ratio_stack:
        raise ScoreIntegrityError("Unclosed native tuplet")
    return total, timed


def audit_mscz(path: Path, repair: bool = False,
               allow_repairs: bool = True) -> list[str]:
    """Verify all staffed voices against the measure's actual duration.

    Returns a list of safe, precisely identified automatic repairs. Raises
    ScoreIntegrityError for any unresolved underfilled or overfilled measure.
    Production conversion can set ``allow_repairs=False`` so that even a
    tiny silent gap must be explained by the source semantic audit instead of
    being silently padded.
    """
    path = Path(path)
    repairs = []
    temp = None
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ScoreIntegrityError("MSCZ has a corrupt ZIP member")
            name = next((n for n in archive.namelist() if n.endswith(".mscx")), None)
            if not name:
                raise ScoreIntegrityError("MSCZ contains no native MSCX score")
            parser = ET.XMLParser(resolve_entities=False, no_network=True)
            root = ET.fromstring(archive.read(name), parser=parser)
            score = root.find("Score")
            if score is None:
                raise ScoreIntegrityError("MSCX contains no Score element")
            discrepancies = []
            for staff_index, staff in enumerate(score.findall("Staff"), 1):
                meter = Fraction(4, 4)
                tuplet_stacks = {}
                for measure_index, measure in enumerate(staff.findall("Measure"), 1):
                    for signature in measure.findall("./voice/TimeSig"):
                        num = int(signature.findtext("sigN", "4"))
                        den = int(signature.findtext("sigD", "4"))
                        if num <= 0 or den <= 0:
                            raise ScoreIntegrityError("Invalid native time signature")
                        meter = Fraction(num, den)
                    intended = Fraction(measure.get("len")) if measure.get("len") else meter
                    for voice_index, voice in enumerate(measure.findall("voice"), 1):
                        stack = tuplet_stacks.setdefault(voice_index, [])
                        actual, timed = _measure_duration(voice, intended, stack)
                        if not timed or actual == intended:
                            continue
                        gap = intended - actual
                        location = (f"staff {staff_index}, measure {measure_index}, "
                                    f"voice {voice_index}")
                        if (repair and allow_repairs and 0 < gap <= Fraction(1, 64)
                                and timed[-1][0].tag == "Rest"
                                and timed[-1][0].findtext("visible") == "0"):
                            rest, old_duration = timed[-1]
                            native = rest.find("duration")
                            if native is None:
                                native = ET.SubElement(rest, "duration")
                            native.text = str(old_duration + gap)
                            new_actual, _ = _measure_duration(voice, intended)
                            if new_actual != intended:
                                discrepancies.append(
                                    f"Automatic repair failed in {location}: "
                                    f"{new_actual} instead of {intended}")
                                continue
                            repairs.append(f"Repaired silent rounding gap {gap} in {location}")
                            continue
                        direction = "short" if gap > 0 else "overfull"
                        discrepancies.append(
                            f"{location} is {direction}: {actual} of {intended} "
                            f"whole notes (difference {gap})")
                for voice_index, stack in tuplet_stacks.items():
                    if stack:
                        raise ScoreIntegrityError(
                            f"Unclosed native tuplet in staff {staff_index}, "
                            f"voice {voice_index}")
            if discrepancies:
                detail = "; ".join(discrepancies[:12])
                if len(discrepancies) > 12:
                    detail += f"; and {len(discrepancies) - 12} more"
                raise ScoreIntegrityError(
                    f"{len(discrepancies)} structural measure issue(s): {detail}")
            if repairs:
                with tempfile.NamedTemporaryFile(
                    prefix=".validated-", suffix=".mscz",
                    dir=path.parent, delete=False
                ) as handle:
                    temp = Path(handle.name)
                modified = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                with zipfile.ZipFile(temp, "w") as target:
                    for member in archive.infolist():
                        target.writestr(member, modified if member.filename == name
                                        else archive.read(member.filename))
        if temp is not None:
            os.replace(temp, path)
            temp = None
        return repairs
    except (ET.XMLSyntaxError, zipfile.BadZipFile) as exc:
        raise ScoreIntegrityError("Malformed native MuseScore archive") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
