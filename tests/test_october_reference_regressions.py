"""Regression tests for score markings found in the four October references.

Synthetic fragments only; original user CAPX/PDF files are never committed.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml

NS="http://www.capella.de/CapXML/2.0"


def capx(music, page="", more=""):
    xml=(f'<score xmlns="{NS}">{page}'
         '<layout><staves><staffLayout description="Piccolo 1"/></staves></layout>'
         '<systems><system><staves><staff layout="Piccolo 1"><voices>'
         '<voice><noteObjects><timeSign time="4/4"/>'
         +music+'</noteObjects></voice></voices></staff></staves></system></systems>'
         '</score>')
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as archive:
        archive.writestr('score.xml',xml)
    return buf.getvalue()


def chord(pitch="C5", extra="", head_extra=""):
    return (f'<chord><duration base="1/4"/>{extra}<heads>'
            f'<head pitch="{pitch}">{head_extra}</head></heads></chord>')


def group_circle(number):
    return ('<drawObjects><drawObj><group>'
            '<drawObj><ellipse x1="-5" y1="-10" x2="-1" y2="-6"/></drawObj>'
            '<drawObj><text y="-8"><font face="capella3" height="18"/>'
            f'<content>{number}</content></text></drawObj>'
            '</group><basic scope="system"/></drawObj></drawObjects>')


def words(label, size=18):
    return ('<drawObjects><drawObj><text x="0" y="-8">'
            f'<font face="Arial" height="{size}"/>'
            f'<content>{label}</content></text></drawObj></drawObjects>')


def test_altfrangg_grouped_circled_sections():
    music=chord(extra=group_circle('1'))+chord()+chord()+chord()
    data,report=serialize_musicxml(capx(music), filename="Altfrangg.capx")
    root=ET.fromstring(data)
    assert report["variation_numbers"]==["①"]
    assert root.find('.//direction-type/words[.="①"]') is not None


def test_arabi_numbered_movement_title_on_same_note():
    graphic=('<drawObjects>'
             '<drawObj><text y="-8"><font face="Arial" height="18"/>'
             '<content>1</content></text></drawObj>'
             '<drawObj><text y="-8"><font face="Arial" height="9"/>'
             '<content>British Grenadiers</content></text></drawObj>'
             '</drawObjects>')
    music=chord(extra=graphic)+chord()+chord()+chord()
    _,report=serialize_musicxml(capx(music))
    assert report["variation_numbers"]==["1 British Grenadiers"]


def test_explicit_hidden_silent_placeholder_keeps_rhythm_but_not_pitch():
    hidden=('<display invisible="true"/>'
            '<heads><head pitch="G5" silent="true"/></heads>')
    music=('<chord><duration base="1/4"/>'+hidden+'</chord>'
           +chord()+chord()+chord())
    data,report=serialize_musicxml(capx(music))
    root=ET.fromstring(data)
    first=root.find('./part/measure/note')
    assert first.get("print-object")=="no"
    assert first.find("rest") is not None
    assert first.find("pitch") is None
    assert len(root.findall('./part/measure/note'))==4


def test_implicit_accidental_suppression_does_not_naturalize():
    music=('<keySign fifths="1"/>'
           +chord('F5',head_extra='<alter display="suppress"/>')
           +chord('F5')+chord()+chord())
    data,_=serialize_musicxml(capx(music))
    root=ET.fromstring(data)
    notes=root.findall('./part/measure/note')
    assert notes[0].findtext('./pitch/alter')=='1'
    assert notes[0].find('./accidental') is None
    assert notes[1].findtext('./pitch/alter')=='1'
