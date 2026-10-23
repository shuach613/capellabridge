"""Regressions uncovered by Vaudois, Waggis and Calvados CAPX source layouts.

These are small synthetic fixtures, not redistributed user-owned scores.
"""
import io
import zipfile
from lxml import etree as ET
import pytest
from app.converter import serialize_musicxml

NS='http://www.capella.de/CapXML/2.0'


def capx(xml):
    file=io.BytesIO()
    with zipfile.ZipFile(file,'w') as archive:
        archive.writestr('score.xml',xml.encode('utf-8'))
    return file.getvalue()


def staff(name,notes):
    return (f'<staff layout="{name}"><voices><voice><noteObjects>{notes}'
            '</noteObjects></voice></voices></staff>')


def note(pitch='C5',duration='1/4',marks=''):
    return (f'<chord><duration base="{duration}"/>{marks}'
            f'<heads><head pitch="{pitch}"/></heads></chord>')


def rest(duration='1/4',marks=''):
    return f'<rest><duration base="{duration}"/>{marks}</rest>'


def draw_words(label,height=15):
    return (f'<drawObjects><drawObj><text y="-5">'
            f'<font face="Arial" height="{height}"/>'
            f'<content>{label}</content></text></drawObj></drawObjects>')


def page_credits(title,composer):
    return (
        '<pageObjects>'
        '<drawObj><text x="106" y="2"><font face="Arial" height="8"/>'
        '<content>(Page) Seite # / 2</content></text></drawObj>'
        f'<drawObj><text x="55" y="4"><font face="Arial" height="21"/>'
        f'<content>{title}</content></text></drawObj>'
        f'<drawObj><text x="102" y="3"><font face="Arial" height="12"/>'
        f'<content>{composer}</content></text></drawObj>'
        '</pageObjects>'
    )


def make_score(layouts,systems,credits=''):
    return capx(
        f'<score xmlns="{NS}">{credits}'
        '<layout><staves>'+''.join(
            f'<staffLayout description="{name}"/>' for name in layouts
        )+'</staves>'
        f'<brackets><bracket from="0" to="{len(layouts)-1}"/></brackets></layout>'
        '<systems>'+''.join('<system><staves>'+''.join(x)+'</staves></system>'
                            for x in systems)
        +'</systems></score>'
    )


@pytest.mark.parametrize('layout',('balanced','compact','original'))
def test_waggis_late_fourth_staff_page_title_and_rest_section_label(layout):
    """Final-only fourth piccolo is retained without showing it at the start."""
    opening=[staff(name,
        '<timeSign time="2/4"/>'
        +(rest(marks=draw_words('1')) if i==0 else note())
        +note('D5'))
        for i,name in enumerate(('Piccolo 1','Piccolo 2','Piccolo 3'))]
    final=[staff(name,note('E5')+note('F5')) for name in
           ('Piccolo 1','Piccolo 2','Piccolo 3','Piccolo 4')]
    data=make_score(['Piccolo 1','Piccolo 2','Piccolo 3','Piccolo 4'],
        [opening,final],page_credits('Waggis','Karl Scherrer'))
    xml,report=serialize_musicxml(data,layout)
    doc=ET.fromstring(xml)
    assert doc.findtext('./movement-title')=='Waggis'
    assert doc.findtext('./identification/creator')=='Karl Scherrer'
    assert report['parts']==['Piccolo 1','Piccolo 2','Piccolo 3','Piccolo 4']
    assert report['measures']==2
    assert report['variable_staff_systems'] is True
    assert report['variation_numbers']==['1']
    assert len(doc.findall('./part'))==4
    fourth=doc.findall('./part')[3]
    assert fourth.find('./measure[1]/attributes/staff-details').get('print-object')=='no'
    assert fourth.find('./measure[2]/attributes/staff-details').get('print-object')=='yes'
    assert len(fourth.findall('./measure[2]/note'))==2
    assert doc.find('./part[1]/measure[2]/print').get('new-system')=='yes'


@pytest.mark.parametrize('layout',('balanced','compact','original'))
@pytest.mark.parametrize('rest_measures',(7,8))
def test_calvados_mixed_meter_multibar_rest_and_performance_text(layout,rest_measures):
    """Integer rest bases count full measures, not whole notes."""
    first=[staff(name,'<timeSign time="2/4"/>'+rest(str(rest_measures))) for name in
           ('Piccolo 1','Piccolo 2','Piccolo 3')]
    # Create exactly 6/8 in second system for all staves.
    second=[staff(name,
        '<timeSign time="6/8"/>'
        + (rest('1/4',draw_words('Trommelsolo')) if i==0 else note(duration='1/4'))
        +note(duration='1/2'))
        for i,name in enumerate(('Piccolo 1','Piccolo 2','Piccolo 3'))]
    data=make_score(['Piccolo 1','Piccolo 2','Piccolo 3'],[first,second],
                    page_credits('Calvados','Christoph Walliser'))
    xml,report=serialize_musicxml(data,layout)
    doc=ET.fromstring(xml)
    assert report['measures']==rest_measures+1,report
    assert report['variable_staff_systems'] is False
    assert report['title']=='Calvados'
    assert doc.findtext('./identification/creator')=='Christoph Walliser'
    assert 'Trommelsolo' in report['printed_directions']
    assert doc.find('.//direction-type/words[.="Trommelsolo"]') is not None
    assert [t.findtext('beats')+'/'+t.findtext('beat-type')
            for t in doc.findall('./part[1]/measure/attributes/time')]==['2/4','6/8']


def test_vaudois_fine_navigation_repeats_and_accents():
    first=staff('Piccolo 1',
        '<timeSign time="2/4"/>'
        +note(marks='<articulation type="normalAccent"/>')
        +rest(marks=draw_words('fine',12))
        +'<barline type="repEnd"/>')
    second=staff('Piccolo 1',
        '<barline type="repBegin"/>'
        +note()+rest(marks=draw_words('D.C. al Fine',12)))
    data=make_score(['Piccolo 1'],[[first],[second]],
                    page_credits('Der Vaudois','Gustave Doret 1922'))
    xml,report=serialize_musicxml(data,'balanced')
    doc=ET.fromstring(xml)
    assert report['title']=='Der Vaudois'
    assert report['composer']=='Gustave Doret 1922'
    assert sorted(report['printed_directions'])==['D.C. al Fine','fine']
    assert len(doc.findall('.//articulations/accent'))==1
    assert doc.find('./part/measure[1]/barline/repeat[@direction="backward"]') is not None
    assert doc.find('./part/measure[2]/barline/repeat[@direction="forward"]') is not None


@pytest.mark.parametrize('layout',('balanced','compact','original'))
def test_capxml_3_namespace_replacement_staff_and_logo_title(layout):
    """Fyyrvogel-type 3.0 files replace a quartet with a solo Zier part."""
    group=('Piccolo 1','Piccolo 2','Piccolo 3','Piccolo 4')
    opening=[staff(n,'<timeSign time="2/4"/>'+note()+note('D5'))
             for n in group]
    closing=[staff('Piccolo Zier',note('E5')+note('F5'))]
    page=(
        '<pageObjects>'
        '<drawObj><text x="150" y="2"><font face="Arial" height="11"/>'
        '<content>Michael Robertson&#10;Trommeltext: Andri Obrist</content>'
        '</text></drawObj>'
        '<drawObj><metafile file="logo.wmf" x="67" y="-3" width="30" height="11"/>'
        '</drawObj>'
        '<drawObj><text x="18" y="11"><font face="Arial" height="12"/>'
        '<content>Intro</content></text></drawObj>'
        '</pageObjects>'
    )
    fixture=make_score(list(group)+['Piccolo Zier'],[opening,closing],page)
    fixture=fixture.replace(b'CapXML/2.0',b'CapXML/3.0')
    xml,report=serialize_musicxml(fixture,layout,filename='Fyyrvogel.capx')
    tree=ET.fromstring(xml)
    assert report['capxml_version']=='3.0'
    assert report['title']=='Fyyrvogel'
    assert report['composer']=='Michael Robertson'
    assert report['subtitle']=='Trommeltext: Andri Obrist'
    assert report['variable_staff_systems']
    assert len(tree.findall('./part'))==5
    assert tree.find('./part[5]/measure[1]/attributes/staff-details').get('print-object')=='no'
    assert tree.find('./part[5]/measure[2]/attributes/staff-details').get('print-object')=='yes'
    assert tree.find('./part[1]/measure[2]/attributes/staff-details').get('print-object')=='no'
    assert tree.find('./part[1]/measure[2]/print').get('new-system')=='yes'


@pytest.mark.parametrize('layout',('balanced','compact','original'))
def test_capxml_1_namespace_second_voice_sections_and_hairpins(layout):
    """Nunnefirzli uses CapXML 1.0, 2 voices and expression hairpins."""
    wedge=(
        '<drawObjects><drawObj><wedge decrescendo="true" color="FF0000"/>'
        '<basic noteRange="1"/></drawObj></drawObjects>'
    )
    heading=draw_words('1 dr Aafang',height=12)
    leading='<timeSign time="2/4"/>'+note(marks=heading+wedge)+note('D5')
    voice_two=note('C5',duration='1/8')*4
    multi_staff=(
        '<staff layout="1. Stimme"><voices>'
        '<voice><noteObjects>'+leading+'</noteObjects></voice>'
        '<voice><noteObjects>'+voice_two+'</noteObjects></voice>'
        '</voices></staff>'
    )
    accompaniment=staff('2. Stimme','<timeSign time="2/4"/>'+note()+note('D5'))
    fixture=make_score(['1. Stimme','2. Stimme'],[[multi_staff,accompaniment]])
    fixture=fixture.replace(b'CapXML/2.0',b'CapXML/1.0')
    xml,report=serialize_musicxml(fixture,layout,filename='Nunnefirzli.capx')
    tree=ET.fromstring(xml)
    assert report['capxml_version']=='1.0'
    assert report['variation_numbers']==['1 dr Aafang']
    assert report['notation_features']['hairpins']==1
    assert tree.find('.//wedge[@type="diminuendo"]') is not None
    assert tree.find('.//wedge[@type="stop"]') is not None
    assert len(tree.findall('./part[1]/measure[1]/note[voice="2"]'))==4
    assert tree.find('./part[1]/measure[1]/backup') is not None
