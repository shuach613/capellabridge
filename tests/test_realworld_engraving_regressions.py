"""Protect real-world Capella notation patterns discovered by PDF/MSCZ comparison.

Fixtures are synthetic and contain no user-owned score or reference PDF.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml
from app.engraving import STYLE_VALUES

NS='http://www.capella.de/CapXML/2.0'


def obj(pitch='C5',dur='1/4',extra='',head=''):
    return (f'<chord><duration base="{dur}"/>{extra}'
            f'<heads><head pitch="{pitch}">{head}</head></heads></chord>')


def graphic(label,face='Arial',size=10,color='000080',y=-7):
    return (f'<drawObj><text x="0" y="{y}">'
            f'<font face="{face}" height="{size}" color="{color}"/>'
            f'<content>{label}</content></text></drawObj>')


def grp(*objects):
    return '<drawObjects>'+''.join(objects)+'</drawObjects>'


def capx(parts,metre='4/4',page=''):
    layouts=''.join(
        f'<staffLayout description="{name}"/>' for name,_ in parts)
    staves=''.join(
        f'<staff layout="{name}"><voices><voice><noteObjects>'
        f'<timeSign time="{metre}"/>{music}'
        '</noteObjects></voice></voices></staff>'
        for name,music in parts)
    xml=(f'<score xmlns="{NS}">{page}'
         '<layout><staves>'+layouts+'</staves></layout>'
         '<systems><system><staves>'+staves+'</staves></system></systems>'
         '</score>')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as zipout:
        zipout.writestr('score.xml',xml.encode())
    return out.getvalue()


def convert(parts,metre='4/4',page=''):
    xml,report=serialize_musicxml(capx(parts,metre,page))
    return ET.fromstring(xml),report


def test_hidden_tied_xshift_rest_trick_preserves_sound_and_fixes_display():
    # A minimized representation of the Waggis number-eight workaround.
    hidden=(
        '<chord><duration base="1/8"/><display invisible="true" xShift="1.25"/>'
        '<heads><head pitch="D7"><tie begin="true"/></head></heads></chord>')
    printed=(
        '<chord><duration base="1/8"/><display xShift="-1.75"/>'
        '<articulation type="strongAccent"/>'
        +grp(graphic('L','capella3',18))
        +'<heads><head pitch="D7"><tie end="true"/></head></heads></chord>')
    first=hidden+printed+obj('E7','1/4')+obj('G6','1/2')
    doc,report=convert([('Piccolo',first)])
    notes=doc.findall('./part/measure/note')
    assert len(notes)==4
    assert notes[0].get('print-object')!='no'
    assert notes[1].get('print-object')=='no'
    assert notes[0].find('./tie[@type="start"]') is not None
    assert notes[1].find('./tie[@type="stop"]') is not None
    assert notes[0].find('./notations/articulations/strong-accent') is not None
    assert notes[0].find('./notations/tied') is None
    assert notes[1].find('./notations/tied') is None
    assert [x.findtext('duration') for x in notes]==['5040','5040','10080','20160']
    assert doc.find('.//direction-type/words[.="𝄾"]') is not None


def test_unrelated_hidden_note_is_not_made_visible():
    hidden='<display invisible="true"/>'
    music=obj('C5',extra=hidden)+obj('D5')+obj('E5')+obj('F5')
    doc,_=convert([('Piccolo',music)])
    assert doc.find('./part/measure/note').get('print-object')=='no'


def test_rest_attached_1st_and_2nd_voltas_are_not_lost():
    a='<rest><duration base="1/4"/>'+grp(
        '<drawObj><volta firstNumber="1"/></drawObj>')+'</rest>'
    b='<rest><duration base="1/4"/>'+grp(
        '<drawObj><volta firstNumber="2"/></drawObj>')+'</rest>'
    doc,_=convert([('Piccolo',a+b)],metre='1/4')
    starts=doc.findall('.//barline/ending[@type="start"]')
    assert [x.get('number') for x in starts]==['1','2']


def test_multimeasure_rest_label_printed_once_not_each_measure():
    multi=('<rest><duration base="3"/>'
           +grp(graphic('Trommelsolo',size=12))+'</rest>')
    tail=obj('C5')+obj('D5')
    doc,report=convert([('Piccolo',multi+tail)],metre='2/4')
    assert report['printed_directions'].count('Trommelsolo')==1
    assert len(doc.findall('.//direction-type/words[.="Trommelsolo"]'))==1
    assert doc.findtext('.//measure-style/multiple-rest')=='3'
    assert len(doc.findall('./part/measure'))==4


def test_breath_comma_and_small_blue_source_annotations():
    notations=grp(
        graphic(',',face='Times New Roman',size=12)
        +graphic('1  Solo',size=6,color='0000FF')
        +graphic('"glockig"',size=10,color='000080',y=4.5))
    music=obj(extra=notations)+obj()+obj()+obj()
    doc,report=convert([('1. Stimme',music)])
    assert doc.find('.//notations/articulations/breath-mark') is not None
    words=[x.text for x in doc.findall('.//direction-type/words')]
    assert '1 Solo' in words and '"glockig"' in words
    assert report['source_text_colors']['1 Solo']=='#0000FF'


def test_no_generated_p1_part_abbreviation():
    doc,_=convert([('1. Piccolo',obj()*4)])
    assert doc.find('./part-list/score-part/part-name').text=='1. Piccolo'
    assert doc.find('./part-list/score-part/part-abbreviation') is None


def test_engine_can_hide_late_parts_and_group_rest_counts():
    assert STYLE_VALUES['createMultiMeasureRests']=='1'
    assert STYLE_VALUES['dontHideStavesInFirstSystem']=='0'
