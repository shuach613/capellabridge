"""Synthetic regression cases based on the supplied Bajass, Bebbino,
Blabbermuul and Brandywine source/PDF pairs.

These tests contain no user-uploaded musical content.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml

NS="http://www.capella.de/CapXML/2.0"


def capx(notes, *, namespace=NS):
    xml=(
        f'<score xmlns="{namespace}">'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="4/4"/>'
        +notes+'</noteObjects></voice></voices></staff></staves></system></systems>'
        '</score>')
    file=io.BytesIO()
    with zipfile.ZipFile(file,'w') as archive:
        archive.writestr('score.xml',xml)
    return file.getvalue()


def text(label, *, font="Arial", size=11, y=-5):
    return (
        f'<drawObj><text x="0" y="{y}">'
        f'<font face="{font}" height="{size}"/>'
        f'<content>{label}</content></text></drawObj>'
    )


def chord(pitch="C5", marks=""):
    return (
        '<chord><duration base="1/4"/>'
        +(f'<drawObjects>{marks}</drawObjects>' if marks else '')
        +f'<heads><head pitch="{pitch}"/></heads></chord>'
    )


def rest(marks=""):
    return ('<rest><duration base="1/4"/>'
            +(f'<drawObjects>{marks}</drawObjects>' if marks else '')
            +'</rest>')


def test_bajass_small_first_system_composer_not_confused_with_trio():
    annotations=text("Bajass",size=16,y=-13)+text("W. E. Saladin",size=9,y=-9)
    first=chord(marks=annotations)+chord()+chord()+chord()
    xml,report=serialize_musicxml(capx(first))
    root=ET.fromstring(xml)
    assert report["title"]=="Bajass"
    assert report["composer"]=="W. E. Saladin"
    assert root.findtext('./identification/creator')=="W. E. Saladin"


def test_bebbino_capella_font_section_numbers_and_ornaments():
    score=(
        chord(marks=text("1",font="capella3",size=22,y=-5))
        +chord("D5",text("l",font="capella3",size=18))
        +chord("E5",text("¿",font="capella3",size=18))
        +chord("F5")
    )
    xml,report=serialize_musicxml(capx(score,
        namespace="http://www.capella.de/CapXML/3.0"))
    root=ET.fromstring(xml)
    assert report["variation_numbers"]==["1"]
    assert root.find('.//direction-type/words[.="1"]') is not None
    assert root.find('./part/measure/note[2]/notations/ornaments/mordent') is not None
    assert root.find('./part/measure/note[3]/notations/ornaments/trill-mark') is not None


def test_blabbermuul_cape_dynamics_accent_and_da_capo_al_coda():
    notes=(
        chord(marks=text("Ê",font="capella3",size=18)
                    +text("{",font="capella3",size=18,y=4.5))
        +chord()+chord()
        +rest(text("da capo &#10;al coda",size=12,y=-10))
    )
    xml,report=serialize_musicxml(capx(notes))
    root=ET.fromstring(xml)
    assert 'da capo al coda' in report["printed_directions"]
    assert root.find('.//direction-type/dynamics/fz') is not None
    assert root.find('./part/measure/note[1]/notations/articulations/accent') is not None
    assert root.find('.//direction/sound[@dacapo="yes"][@tocoda="coda"]') is not None


def test_bajass_trio_stays_visible_when_font_is_small():
    score=chord(marks=text('Trio',font='Arial',size=7,y=-7))+chord()+chord()+chord()
    xml,report=serialize_musicxml(capx(score))
    assert 'Trio' in report['printed_directions']
    root=ET.fromstring(xml)
    assert root.find('.//direction-type/words[.="Trio"]') is not None
