"""Artificial notation cases modeled on old Capella over-barline scores."""
from fractions import Fraction
from io import BytesIO
from zipfile import ZipFile
from lxml import etree as ET
from app.converter import serialize_musicxml

def note(base='1/8', chord=True, dots=0, tuplet=False):
    kind='chord' if chord else 'rest'
    extras=(' dots="%s"'%dots if dots else '')
    dur='<duration base="%s"%s>'%(base,extras) + ('<tuplet count="3"/>' if tuplet else '')+'</duration>'
    return '<'+kind+'>'+dur+('<heads><head pitch="C5"/></heads>' if chord else '')+'</'+kind+'>'

def make(voices, meter='2/4'):
    xml=('''<score xmlns="http://www.capella.de/CapXML/2.0">
    <layout><staves><staffLayout description="Piccolo"/></staves></layout>
    <systems><system><staves><staff layout="Piccolo"><voices>'''
    +''.join('<voice><noteObjects><timeSign time="'+meter+'"/>'+events+'</noteObjects></voice>' for events in voices)
    +'</voices></staff></staves></system></systems></score>')
    io=BytesIO()
    with ZipFile(io,'w') as arc:arc.writestr('score.xml',xml)
    result,report=serialize_musicxml(io.getvalue())
    return ET.fromstring(result),report

def get_pitched(xml, voice='1'):
    return [n for n in xml.findall('.//note') if n.findtext('voice')==voice and n.find('pitch') is not None]

def test_plain_sounding_note_across_barline_becomes_two_tied_eighths():
    xml,_=make([note()*7+note('1/4')])
    pitches=get_pitched(xml)
    assert len(pitches)==9
    assert [n.findtext('type') for n in pitches[-2:]]==['eighth','eighth']
    assert pitches[-2].find('./tie[@type="start"]') is not None
    assert pitches[-1].find('./tie[@type="stop"]') is not None
    assert sum(int(n.findtext('duration')) for n in pitches)==10080*4*Fraction(9,8)

def test_dotted_quarter_across_barline_gets_eighth_tied_to_quarter():
    xml,_=make([note()*7+note('1/4',dots=1)])
    pitches=get_pitched(xml)
    assert [n.findtext('type') for n in pitches[-2:]]==['eighth','quarter']
    assert pitches[-2].find('./notations/tied[@type="start"]') is not None
    assert pitches[-1].find('./notations/tied[@type="stop"]') is not None

def test_secondary_voice_half_rest_is_divided_without_sounding_ties():
    voice1=note()*8
    voice2=note()*7+note('1/2',chord=False)
    xml,_=make([voice1,voice2])
    second=[n for n in xml.findall('.//note') if n.findtext('voice')=='2']
    assert [n.findtext('type') for n in second[-2:]]==['eighth','quarter']
    assert second[-1].find('dot') is not None
    assert all(n.find('tie') is None for n in second[-2:])
    assert sum(int(n.findtext('duration')) for n in second)==10080*4*Fraction(11,8)

def test_unusual_source_tuplet_offset_rest_preserves_exact_total_duration():
    # Source offset 89/96; the last 1/8 rest crosses 2/4 synthetic barline.
    events=note()*6+note('1/8',chord=False,tuplet=True)+note('1/32')*3+note('1/8',chord=False)
    xml,_=make([events])
    out=xml.findall('.//note')
    rest_chunks=[n for n in out if n.find('rest') is not None]
    assert [int(n.findtext('duration')) for n in rest_chunks[-2:]]==[2940,2100]
    assert sum(int(n.findtext('duration')) for n in out)==10080*4*Fraction(101,96)
