"""Regressions distilled from three CAPX/PDF/MSZ discrepancies.

This suite deliberately uses synthetic notes, not the original works.
It verifies visible engraving semantics, not only pitches or successful export.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml


def make_score(system_parts, *, meter='2/4', version='2.0', page=''):
    layout='<layout><staves>'+''.join(
        f'<staffLayout description="P{i+1}"/>' for i in range(len(system_parts)))+'</staves></layout>'
    staves='<staves>'+''.join(
        f'<staff layout="P{i+1}"><voices><voice><noteObjects>'
        f'<timeSign time="{meter}"/>{xml}'
        '</noteObjects></voice></voices></staff>'
        for i,xml in enumerate(system_parts))+'</staves>'
    xml=(f'<score xmlns="http://www.capella.de/CapXML/{version}">'
         +page+layout+'<systems><system>'+staves+'</system></systems></score>')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('score.xml',xml.encode('utf-8'))
    return out.getvalue()


def chord(pitch='D7', duration='1/8', *, display='', graphics='', articulation='', head=''):
    return ('<chord><duration base="'+duration+'"/></duration>' if False else
            '<chord><duration base="'+duration+'"/>'
            +(f'<display {display}/>' if display else '')
            +(f'<articulation type="{articulation}"/>' if articulation else '')
            +(f'<drawObjects>{graphics}</drawObjects>' if graphics else '')
            +f'<heads><head pitch="{pitch}">{head}</head></heads></chord>')


def draw(label,face='capella3',height=18,y=-5,color='000080'):
    return ('<drawObj><text x="0" y="'+str(y)+'">'
            f'<font face="{face}" height="{height}" color="{color}"/>'
            f'<content>{label}</content></text></drawObj>')


def volta(number):
    return f'<drawObjects><drawObj><volta firstNumber="{number}" x1="-1" x2="3" y="-8"/></drawObj></drawObjects>'


def convert(data):
    data, report=serialize_musicxml(data)
    return ET.fromstring(data),report


def test_waggis_hidden_note_shifted_pair_becomes_visible_note_followed_by_true_rest():
    seq=(
        chord('D7',display='invisible="true" xShift="1.25"',
              head='<tie begin="true"/>')
        +chord('D7',display='xShift="-1.75"',
               articulation='strongAccent',graphics=draw('L'),
               head='<tie end="true"/>')
        +chord('E7',duration='1/4')
        +chord('E7',duration='1/8')
    )
    root,_=convert(make_score([seq]))
    notes=root.findall('./part/measure/note')
    assert len(notes)==4
    assert notes[0].find('./pitch') is not None
    assert notes[0].get('print-object')!='no'
    assert notes[1].find('./rest') is not None
    assert notes[1].get('print-object')!='no'
    assert notes[0].find('./notations/articulations/strong-accent') is not None
    assert notes[0].find('./tie') is None
    assert notes[1].find('./tie') is None
    assert not root.findall('.//direction-type/words[.="&#119102;"]')
    assert [n.findtext('duration') for n in notes[:2]]==['5040','5040']


def test_graphical_rest_pattern_not_inferred_without_adjacent_shifted_matching_note():
    seq=(chord('D7',display='invisible="true" xShift="1.25"')
         +chord('E7',display='xShift="-1.75"',graphics=draw('L'))
         +chord('G7',duration='1/4')+chord('G7',duration='1/8'))
    root,_=convert(make_score([seq]))
    notes=root.findall('./part/measure/note')
    assert notes[0].get('print-object')=='no'
    assert notes[1].find('./pitch') is not None


def test_invisible_silent_head_with_graphical_rest_is_printed_as_real_rest():
    seq=(chord('D7',display='invisible="true"',
               head='',graphics=draw('L')).replace(
               '<head pitch="D7"></head>','<head pitch="D7" silent="true"></head>')
         +chord('E7')+chord('G7',duration='1/4')+chord('G7',duration='1/8'))
    root,_=convert(make_score([seq]))
    first=root.find('./part/measure/note')
    assert first.find('./rest') is not None
    assert first.get('print-object')!='no'


def test_nunnefirzli_first_second_ending_brackets_attached_to_rests():
    rest=lambda number: ('<rest><duration base="1/4"/>'
                         +volta(number)+'</rest>')
    xml=(rest('1')+rest('2'))
    root,_=convert(make_score([xml],meter='1/4'))
    ending=root.findall('.//barline/ending')
    starts=[x.get('number') for x in ending if x.get('type')=='start']
    assert '1' in starts and '2' in starts


def test_calvados_multi_measure_rest_instruction_once_not_seven_times():
    seven=('<rest><duration base="7"/>'
           '<drawObjects>'+draw('Trommelsolo',face='Arial',height=10)
           +'</drawObjects></rest>')
    root,report=convert(make_score([seven,seven,seven],meter='2/4'))
    assert report['printed_directions']==['Trommelsolo']
    assert len(root.findall('.//direction-type/words[.="Trommelsolo"]'))==1
    assert report['source_multi_measure_rests']==3
    first=root.find('./part/measure/attributes/measure-style/multiple-rest')
    assert first is not None and first.text=='7'


def test_small_solo_and_lower_staff_glockig_direction_survive():
    first=(chord(graphics=draw('1  Solo',face='Arial',height=6))
           +chord(graphics=draw('s Firzli',face='Arial',height=10))
           +chord(duration='1/4'))
    second=(chord(graphics=draw('"glockig"',face='Arial',height=10,y=4.5))
            +chord()+chord(duration='1/4'))
    root,report=convert(make_score([first,second],meter='1/2'))
    texts=[n.text for n in root.findall('.//direction-type/words')]
    assert '1 Solo' in report['printed_directions']
    assert 's Firzli' in report['printed_directions']
    assert '"glockig"' in texts


def test_no_generic_p1_p2_instrument_abbreviations():
    root,_=convert(make_score([
        chord(duration='1/4')*2,
        chord(duration='1/4')*2,
    ]))
    assert [n.text for n in root.findall('./part-list/score-part/part-abbreviation')]==[None,None]
