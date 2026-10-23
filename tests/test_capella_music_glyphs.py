"""Regression tests based on recurring glyph patterns in older Capella scores.

Synthetic fixtures only: do not commit user-owned musical scores or PDFs.
"""
import io
import zipfile
import pytest
from lxml import etree as ET
from app.converter import serialize_musicxml

NS='http://www.capella.de/CapXML/3.0'


def capx(xml):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('score.xml',xml.encode())
    return buf.getvalue()


def font_char(glyph, y=5):
    return (
        '<drawObjects><drawObj>'
        f'<text x="0" y="{y}"><font face="capella3" height="18"/>'
        f'<content>{glyph}</content></text>'
        '</drawObj></drawObjects>'
    )


def words(label, y=-7, height=14):
    return (
        '<drawObjects><drawObj>'
        f'<text x="0" y="{y}"><font face="Arial" height="{height}"/>'
        f'<content>{label}</content></text>'
        '</drawObj></drawObjects>'
    )


def chord(pitch='C5', attached=''):
    return (
        '<chord><duration base="1/4"/>'+attached+
        f'<heads><head pitch="{pitch}"/></heads></chord>'
    )


def make_score(first_voice,second_voice=None, page_objects=''):
    if second_voice:
        voices=(
            '<voice><noteObjects>'+first_voice+'</noteObjects></voice>'
            '<voice><noteObjects>'+second_voice+'</noteObjects></voice>'
        )
    else:
        voices='<voice><noteObjects>'+first_voice+'</noteObjects></voice>'
    return capx(
        '<score xmlns="'+NS+'">'+page_objects+
        '<layout><staves><staffLayout description="Piccolo 1"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo 1"><voices>'
        +voices+'</voices></staff></staves></system></systems></score>'
    )


@pytest.mark.parametrize('layout',('balanced','compact','original'))
def test_old_capella3_dynamics_remain_editable_in_all_layouts(layout):
    music=(
        '<timeSign time="4/4"/>'
        +chord('C5',font_char('p'))
        +chord('D5',font_char('f'))
        +chord('E5',font_char('j'))
        +chord('F5',font_char('i'))
    )
    xml,report=serialize_musicxml(make_score(music),layout)
    root=ET.fromstring(xml)
    assert report['measures']==1
    assert len(root.findall('./part/measure/note'))==4
    assert [next(iter(x)).tag for x in root.findall(
        './part/measure/direction/direction-type/dynamics'
    )]==['p','f','mf','mp']
    assert root.find('./part/measure/direction/direction-type/words[.="p"]') is None


@pytest.mark.parametrize('layout',('balanced','compact','original'))
def test_trill_fermata_and_note_articulation_survive(layout):
    music=(
        '<timeSign time="4/4"/>'
        +chord('C5',font_char('t'))
        +chord('D5',font_char('u'))
        +chord('E5','<articulation type="staccato"/>')
        +chord('F5','<articulation type="normalAccent"/>')
    )
    xml,report=serialize_musicxml(make_score(music),layout)
    root=ET.fromstring(xml)
    notes=root.findall('./part/measure/note')
    assert notes[0].find('./notations/ornaments/trill-mark') is not None
    assert notes[1].find('./notations/fermata[@type="upright"]') is not None
    assert notes[2].find('./notations/articulations/staccato') is not None
    assert notes[3].find('./notations/articulations/accent') is not None


@pytest.mark.parametrize('layout',('balanced','compact','original'))
def test_section_headers_and_fine_navigation_have_semantic_export(layout):
    music=(
        '<timeSign time="4/4"/>'
        +chord('C5',words('Thema A'))
        +chord('D5')
        +chord('E5')
        +chord('F5',words('D.C. al Fine',height=12))
        +chord('G5',words('Var. 1 allegretto C'))
        +chord('A5',font_char('d'))
        +chord('B5',words('Fine',height=12))
        +chord('C6')
    )
    xml,report=serialize_musicxml(make_score(music),layout)
    root=ET.fromstring(xml)
    assert report['measures']==2
    assert 'Thema A' in report['variation_numbers']
    assert 'Var. 1 allegretto C' in report['variation_numbers']
    assert any(d.get('dacapo')=='yes' for d in root.findall('.//direction/sound'))
    assert any(d.get('fine')=='yes' for d in root.findall('.//direction/sound'))


def test_named_wavy_gliss_preserved_without_guessing_other_wavy_lines():
    wavy=(
        '<drawObjects><drawObj><wavyLine x1="0" y1="1" x2="0" y2="2"/>'
        '<basic noteRange="1"/></drawObj></drawObjects>'
    )
    music=(
        '<timeSign time="4/4"/>'
        +chord('C5',wavy)
        +chord('G5',words('Gliss',y=3,height=8))
        +chord('E5')
        +chord('F5')
    )
    xml,_=serialize_musicxml(make_score(music))
    root=ET.fromstring(xml)
    notes=root.findall('./part/measure/note')
    assert notes[0].find('./notations/glissando[@type="start"]') is not None
    assert notes[1].find('./notations/glissando[@type="stop"]') is not None
    assert 'Gliss' in ''.join(root.itertext())


def test_page_credit_small_composer_byline_not_lost():
    page=(
        '<pageObjects><drawObj><text x="50" y="3"><font face="Arial" height="16"/>'
        '<content>5 Variationen über Ei du scheene</content></text></drawObj>'
        '<drawObj><text x="50" y="6"><font face="Arial" height="9"/>'
        '<content>René Brielmann, ??</content></text></drawObj>'
        '<drawObj><text x="83" y="2"><font face="Arial" height="9"/>'
        '<content>06.10.2007 DWe</content></text></drawObj></pageObjects>'
    )
    music='<timeSign time="4/4"/>'+chord()*4
    xml,report=serialize_musicxml(make_score(music,page_objects=page))
    assert report['composer']=='René Brielmann, ??'
    assert report['title']=='5 Variationen über Ei du scheene'


def test_first_system_combined_composer_arranger_credit():
    title=(
        '<drawObjects><drawObj><text x="25" y="-17">'
        '<font face="Germany" height="37"/>'
        '<content>Alte Kameraden 94</content></text></drawObj></drawObjects>'
    )
    credit=(
        '<drawObjects><drawObj><text x="11" y="-24">'
        '<font face="Arial" height="9"/>'
        '<content>Carl Teike Arr. für Piccolo: René Brielmann '
        'Trommeltext: Urs Eble</content></text></drawObj></drawObjects>'
    )
    music='<timeSign time="4/4"/>'+chord('C5',title)+chord('D5',credit)+chord()*2
    _,report=serialize_musicxml(make_score(music))
    assert report['title']=='Alte Kameraden 94'
    assert report['composer']=='Carl Teike'
    assert 'René Brielmann' in report['subtitle']
    assert 'Urs Eble' in report['subtitle']
