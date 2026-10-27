"""Music-first regression for native MuseScore grace notes and overfull guards.

MuseScore 3 commonly serializes an eighth grace as <grace8/>, not <grace16/>.
A grace note has ZERO performed time even though its note type is eighth.
"""
import zipfile
from fractions import Fraction
from lxml import etree as ET
import pytest
from app.score_validation import _measure_duration, audit_mscz, ScoreIntegrityError


def chord(value="quarter", grace=None):
    n=ET.Element("Chord")
    if grace:
        ET.SubElement(n,grace)
    ET.SubElement(n,"durationType").text=value
    return n


@pytest.mark.parametrize("grace_tag",[
    "grace4","grace8","grace16","grace32",
    "grace4after","grace8after","grace16after",
    "acciaccatura","appoggiatura",
])
def test_native_grace_chord_has_zero_sounding_duration(grace_tag):
    voice=ET.Element("voice")
    voice.extend([chord(),chord("eighth",grace_tag),chord()])
    total,timed=_measure_duration(voice,Fraction(1,2))
    assert total==Fraction(1,2)
    assert [value for _,value in timed]==[
        Fraction(1,4),Fraction(0),Fraction(1,4)]


def native_score(path,with_grace=True):
    root=ET.Element("museScore")
    score=ET.SubElement(root,"Score")
    staff=ET.SubElement(score,"Staff",id="1")
    m=ET.SubElement(staff,"Measure",len="1/2")
    v=ET.SubElement(m,"voice")
    v.extend([chord(),chord("eighth","grace8" if with_grace else None),chord()])
    with zipfile.ZipFile(path,"w") as z:
        z.writestr("test.mscx",ET.tostring(root,encoding="utf-8"))


def test_tango_measures_with_eighth_grace_are_not_reported_overfull(tmp_path):
    path=tmp_path/"tango-grace.mscz"
    native_score(path)
    assert audit_mscz(path,repair=False)==[]


def test_a_real_overfull_eighth_is_still_rejected(tmp_path):
    path=tmp_path/"actually-overfull.mscz"
    native_score(path,with_grace=False)
    with pytest.raises(ScoreIntegrityError,match=r"overfull: 5/8 of 1/2"):
        audit_mscz(path,repair=False)


def test_trailing_negative_location_for_direction_does_not_shorten_music():
    voice=ET.Element("voice")
    voice.append(chord("whole"))
    location=ET.SubElement(voice,"location")
    ET.SubElement(location,"fractions").text="-1/2"
    ET.SubElement(voice,"Dynamic")

    total,timed=_measure_duration(voice,Fraction(1,1))

    assert total==Fraction(1,1)
    assert [duration for _,duration in timed]==[Fraction(1,1)]


def test_positive_location_before_later_note_still_extends_timeline():
    voice=ET.Element("voice")
    voice.append(chord("quarter"))
    location=ET.SubElement(voice,"location")
    ET.SubElement(location,"fractions").text="1/4"
    voice.append(chord("quarter"))

    total,_=_measure_duration(voice,Fraction(1,1))

    assert total==Fraction(3,4)
