"""Notation fidelity tests for CapXML beyond the original four-part examples."""
from fractions import Fraction
from pathlib import Path
import io
import zipfile

import pytest
from lxml import etree as ET

from app.converter import (
    duration_of, read_capx, serialize_musicxml, tuplet_normal_notes,
)
from app.engraving import prepare_print_ready
from app.score_validation import ScoreIntegrityError, audit_mscz


CAP_NS = "http://www.capella.de/CapXML/2.0"


def capx(document):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("score.xml", document.encode("utf-8"))
    return buf.getvalue()


def native_zip(path, score_xml):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("score.mscx", score_xml)


def test_capxml_duplets_triplets_and_extended_tuplets():
    for attrs, expected in (
        ('count="2"', Fraction(1, 2)),
        ('count="2" prolong="true" tripartite="true"', Fraction(3, 2)),
        ('count="3"', Fraction(2, 3)),
        ('count="5"', Fraction(4, 5)),
        ('count="11" tripartite="true" prolong="true"', Fraction(12, 11)),
    ):
        chord = ET.fromstring((
            f'<chord xmlns="{CAP_NS}"><duration base="1/8">'
            f'<tuplet {attrs}/></duration><heads><head pitch="C5"/>'
            '</heads></chord>'
        ).encode())
        assert duration_of(chord) == Fraction(1, 8) * expected


def test_multiple_voices_slurs_lyrics_and_sparse_timing():
    voice1 = (
        '<chord><duration base="1/2"/><heads><head pitch="C5"/></heads>'
        '</chord>'
        '<chord><duration base="1/2"/><heads><head pitch="D5"/></heads>'
        '</chord>'
    )
    voice2 = (
        '<chord><duration base="1/4"/>'
        '<drawObjects><drawObj><slur/><basic noteRange="2"/></drawObj>'
        '</drawObjects>'
        '<lyric><verse i="0" hyphen="true">Hel</verse>'
        '<verse i="1">Hallo</verse></lyric>'
        '<heads><head pitch="G5"/></heads></chord>'
        '<chord><duration base="1/4"/>'
        '<drawObjects><drawObj><slur/><basic noteRange="1"/></drawObj>'
        '</drawObjects><heads><head pitch="A5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="B5"/></heads></chord>'
    )
    document = (
        f'<score xmlns="{CAP_NS}">'
        '<layout><staves><staffLayout description="Piano"/></staves></layout>'
        '<systems><system><staves><staff layout="Piano"><voices>'
        f'<voice><noteObjects><timeSign time="4/4"/>{voice1}</noteObjects></voice>'
        f'<voice><noteObjects>{voice2}</noteObjects></voice>'
        '</voices></staff></staves></system></systems></score>'
    )
    xml, report = serialize_musicxml(capx(document), "balanced")
    score = ET.fromstring(xml)
    part = score.find("part")
    measure = part.find("measure")
    assert report["measures"] == 1
    assert len(measure.findall("backup")) == 1
    assert measure.findtext("backup/duration") == "40320"
    assert measure.findtext("forward/duration") == "10080"
    voices = [n.findtext("voice") for n in measure.findall("note")]
    assert voices == ["1", "1", "2", "2", "2"]
    # The slurs only belong to voice 2 and must have distinct numbers.
    starts = measure.findall('.//slur[@type="start"]')
    stops = measure.findall('.//slur[@type="stop"]')
    assert sorted(n.get("number") for n in starts) == ["1", "2"]
    assert sorted(n.get("number") for n in stops) == ["1", "2"]
    lyrics = measure.findall(".//lyric")
    assert [n.findtext("text") for n in lyrics] == ["Hel", "Hallo"]


def test_integrity_audit_preserves_exact_native_fraction(tmp_path):
    path = tmp_path / "fine.mscz"
    template = (
        '<museScore version="3.01"><Score><Staff id="1">'
        '<Measure len="15/32"><voice>'
        '<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>'
        + ('<Chord><durationType>eighth</durationType></Chord>' * 3) +
        '<endTuplet/><Chord><durationType>eighth</durationType></Chord>'
        '<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>'
        '<Rest><durationType>eighth</durationType></Rest><endTuplet/>'
        '<Rest><visible>0</visible><durationType>128th</durationType>'
        '<duration>{duration}</duration></Rest>'
        '</voice></Measure></Staff></Score></museScore>'
    )
    native_zip(path, template.format(duration="1/96"))
    assert audit_mscz(path, repair=False) == []
    with zipfile.ZipFile(path) as archive:
        assert b"<duration>1/96</duration>" in archive.read("score.mscx")


def test_integrity_audit_repairs_small_hidden_rest_gap(tmp_path):
    path = tmp_path / "short.mscz"
    xml = (
        '<museScore version="3.01"><Score><Staff id="1">'
        '<Measure len="15/32"><voice>'
        '<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>'
        + ('<Chord><durationType>eighth</durationType></Chord>' * 3) +
        '<endTuplet/><Chord><durationType>eighth</durationType></Chord>'
        '<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>'
        '<Rest><durationType>eighth</durationType></Rest><endTuplet/>'
        '<Rest><visible>0</visible><durationType>128th</durationType>'
        '<duration>1/128</duration></Rest>'
        '</voice></Measure></Staff></Score></museScore>'
    )
    native_zip(path, xml)
    with pytest.raises(ScoreIntegrityError, match="measure 1"):
        audit_mscz(path)
    repairs = audit_mscz(path, repair=True)
    assert len(repairs) == 1 and "1/384" in repairs[0]
    assert audit_mscz(path, repair=False) == []
    with zipfile.ZipFile(path) as archive:
        assert b"<duration>1/96</duration>" in archive.read("score.mscx")


def test_integrity_audit_rejects_real_missing_voice_beats(tmp_path):
    path = tmp_path / "bad.mscz"
    native_zip(path,
        '<museScore version="3.01"><Score><Staff id="1">'
        '<Measure><voice><Chord><durationType>quarter</durationType></Chord>'
        '</voice></Measure></Staff></Score></museScore>'
    )
    with pytest.raises(ScoreIntegrityError, match="staff 1, measure 1, voice 1"):
        audit_mscz(path, repair=True)


def test_score_title_frame_multiline_is_single_subtitle(tmp_path):
    path = tmp_path / "title.mscz"
    native_zip(path,
        '<museScore version="3.01"><Score><Staff id="1"><VBox><height>10</height>'
        '<Text><style>Title</style><text>Old Title</text></Text>'
        '<Text><style>Subtitle</style><text>Old Subtitle</text></Text>'
        '</VBox></Staff></Score></museScore>'
    )
    prepare_print_ready(path, {
        "title": "Die alte Schwytzer",
        "subtitle": "Prepared by Karl Schell\nArranged by René Brielmann",
        "composer": "René Brielmann",
    })
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read("score.mscx"))
    texts = native.findall("./Score/Staff/VBox/Text")
    assert [item.findtext("style") for item in texts] == ["Title", "Subtitle", "Composer"]
    rich = texts[1].find("text")
    assert rich.text == "Prepared by Karl Schell"
    assert len(rich.findall("br")) == 1
    assert rich.find("br").tail == "Arranged by René Brielmann"
    assert b"&lt;br/&gt;" not in ET.tostring(rich)
