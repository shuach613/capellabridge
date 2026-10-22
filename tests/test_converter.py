"""Tests run without MuseScore installed. Integration tests mock the CLI only."""
import io
import os
import zipfile
from pathlib import Path
import pytest
from lxml import etree as ET
from fastapi.testclient import TestClient
from app import converter


def capx(xml):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:z.writestr('score.xml',xml)
    return buf.getvalue()


MINIMAL=b'''<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">
<layout><staves><staffLayout description="Flute"/></staves></layout>
<systems><system><staves><staff layout="Flute"><voices><voice><noteObjects>
<clefSign clef="treble"/><keySign fifths="1"/><timeSign time="2/4"/>
<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>
<chord><duration base="1/4"/><heads><head pitch="F5"><alter step="0"/></head></heads></chord>
</noteObjects></voice></voices></staff></staves></system></systems></score>'''


def test_single_staff_and_octave():
    xml,report=converter.serialize_musicxml(capx(MINIMAL))
    doc=ET.fromstring(xml)
    assert report['measures']==1
    assert report['parts']==['Flute']
    notes=doc.findall('.//part/measure/note')
    assert len(notes)==2
    assert notes[0].findtext('./pitch/octave')=='4'  # Capella's middle C is C5
    assert notes[1].findtext('./pitch/alter') is None
    assert notes[1].findtext('./accidental')=='natural'



def test_first_page_credits_and_multiline_composer():
    decorated=MINIMAL.replace(
        b'<clefSign clef="treble"/>',
        b'''<clefSign clef="treble"/><chord><duration base="1/4"/>
        <drawObjects>
          <drawObj><text y="-24"><font face="Lato" height="37"/><content>Brandywine</content></text></drawObj>
          <drawObj><text y="-17"><font face="Lato" height="17"/><content>Variationen fuer vier Floeten
von Rene Brielmann</content></text></drawObj>
        </drawObjects><heads><head pitch="D5"/></heads></chord>'''
    )
    xml,report=converter.serialize_musicxml(capx(decorated))
    doc=ET.fromstring(xml)
    assert doc.findtext('./movement-title')=='Brandywine'
    assert doc.findtext('./identification/creator')=='Rene Brielmann'
    credits={credit.findtext('credit-type'):credit.findtext('credit-words')
             for credit in doc.findall('credit')}
    assert credits['title']=='Brandywine'
    assert credits['subtitle']=='Variationen fuer vier Floeten'
    assert credits['composer']=='Rene Brielmann'

def test_capx_rejects_bad_input():
    with pytest.raises(converter.CapellaError):converter.read_capx(b'not zip')
    with pytest.raises(converter.CapellaError):converter.read_capx(capx(b'<!DOCTYPE score [<!ENTITY x "a">]><score/>'))


def test_modes_are_distinct():
    src=capx(MINIMAL)
    a,_=converter.serialize_musicxml(src,'balanced')
    b,_=converter.serialize_musicxml(src,'compact')
    assert a!=b


def test_brandywine_from_environment():
    path=os.getenv('BRANDYWINE_CAPX')
    if not path:pytest.skip('Set BRANDYWINE_CAPX to provided test fixture')
    xml,report=converter.serialize_musicxml(Path(path).read_bytes())
    doc=ET.fromstring(xml)
    assert report['title']=='Brandywine'
    assert doc.findtext('./identification/creator')=='René Brielmann'
    assert doc.findtext('./credit[credit-type="title"]/credit-words')=='Brandywine'
    assert 'Variationen für 4 Flöten' in doc.findtext('./credit[credit-type="subtitle"]/credit-words')
    assert report['notes']==1592
    assert report['measures']==102
    assert len(report['parts'])==4
    assert len(doc.findall('part'))==4
    assert len(doc.findall('.//note'))==1721  # chords + rests
    assert len(doc.findall('.//tie'))==18
    assert len(doc.findall('.//ending'))==144
    assert len(doc.findall('.//notations/slur'))>150
    assert doc.findtext('./part/measure/note/pitch/octave')=='6'
    for part in doc.findall('part'):
        assert len(part.findall('measure'))==102


def test_web_upload_without_login(monkeypatch,tmp_path):
    from app import main
    monkeypatch.setattr(main,'JOBS',tmp_path/'jobs')
    def fake_convert(source,target):
        out=Path(target)
        if out.suffix=='.mscz':
            with zipfile.ZipFile(out,'w') as z:z.writestr('score.mscx','<museScore version="3.02"><Score><Staff/></Score></museScore>')
        else:out.write_bytes(b'%PDF-1.4\n%test' if out.suffix=='.pdf' else b'<score-partwise/>')
    def fake_preview(pdf,png):Path(png).write_bytes(b'\x89PNG\r\n\x1a\n')
    monkeypatch.setattr(main,'convert',fake_convert)
    monkeypatch.setattr(main,'create_preview',fake_preview)
    client=TestClient(main.app)
    assert client.get('/').status_code==200
    assert client.get('/api/info').status_code==200
    assert client.get('/static/app.js').status_code==200
    with client:
        response=client.post('/api/convert?layout=balanced',files=[('files',('Tiny.capx',capx(MINIMAL),'application/octet-stream'))])
    assert response.status_code==200,response.text
    payload=response.json()
    assert payload['results'][0]['ok'] is True
    files=payload['results'][0]['downloads']
    assert client.get(files['mscz']).status_code==200
    assert client.get(payload['batch_url']).status_code==200
    assert client.get(files['mscz']).status_code==200
    assert client.post('/api/account/password',json={'current_password':'x','new_password':'abcdefghijkl'}).status_code==404


def test_only_top_staff_has_ending_brackets_and_measure_numbers():
    from app import converter
    xml = b'''<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">
    <layout><staves><staffLayout description="First"/><staffLayout description="Second"/></staves></layout>
    <systems><system><staves>
    <staff layout="First"><voices><voice><noteObjects>
    <timeSign time="2/4"/>
    <chord><duration base="1/4"/><heads><head pitch="C5"/></heads>
    <drawObjects><drawObj><volta firstNumber="1"/></drawObj></drawObjects></chord>
    <chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>
    <barline type="repEnd"/>
    </noteObjects></voice></voices></staff>
    <staff layout="Second"><voices><voice><noteObjects>
    <timeSign time="2/4"/>
    <chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>
    <barline type="repEnd"/>
    </noteObjects></voice></voices></staff>
    </staves></system></systems></score>'''
    result,_=converter.serialize_musicxml(capx(xml))
    score=ET.fromstring(result)
    top,bottom=score.findall('./part')
    assert top.xpath('count(.//barline/ending)')==2
    assert bottom.xpath('count(.//barline/ending)')==0
    assert top.findtext('./measure/print/measure-numbering')=='system'
    assert bottom.find('./measure/print/measure-numbering') is None
    assert top.xpath('count(.//barline/repeat)')==1
    assert bottom.xpath('count(.//barline/repeat)')==1


def test_print_ready_mscz_preserves_score_and_styles(tmp_path):
    from app.engraving import prepare_print_ready, STYLE_VALUES
    import zipfile
    output=tmp_path/'print.mscz'
    with zipfile.ZipFile(output,'w') as f:
        f.writestr('META-INF/container.xml','<container/>')
        f.writestr('score.mscx', '<museScore version="3.02"><Score><Style><showMeasureNumber>0</showMeasureNumber></Style><Staff/></Score></museScore>')
    prepare_print_ready(output)
    with zipfile.ZipFile(output) as f:
        assert f.read('META-INF/container.xml')==b'<container/>'
        doc=ET.fromstring(f.read('score.mscx'))
        for key,value in STYLE_VALUES.items():
            assert doc.findtext('./Score/Style/'+key)==value
        assert len(doc.findall('./Score/Staff'))==1


def test_all_three_layouts_have_clean_system_numbering():
    """Original breaks at source system starts, not every later measure."""
    systems=[]
    for si in range(3):
        staff_parts=[]
        for name in ('First','Second'):
            objects='<timeSign time="2/4"/>' if si == 0 else ''
            objects += (
                '<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
                '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
            )
            staff_parts.append(
                f'<staff layout="{name}"><voices><voice><noteObjects>{objects}'
                '</noteObjects></voice></voices></staff>'
            )
        systems.append('<system><staves>'+''.join(staff_parts)+'</staves></system>')
    source=capx((
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="First"/>'
        '<staffLayout description="Second"/></staves></layout>'
        '<systems>'+''.join(systems)+'</systems></score>'
    ).encode())
    for layout in ('balanced','compact','original'):
        xml,report=converter.serialize_musicxml(source,layout)
        doc=ET.fromstring(xml)
        assert report['measures']==3
        parts=doc.findall('./part')
        assert len(parts)==2
        assert [len(p.findall('./measure')) for p in parts]==[3,3]
        assert parts[0].findtext('./measure/print/measure-numbering')=='system'
        assert parts[1].find('./measure/print') is None
        forced=parts[0].xpath('./measure/print[@new-system="yes"]')
        if layout == 'original':
            assert len(forced)==2
            assert [node.getparent().get('number') for node in forced]==['2','3']
        else:
            assert forced==[]
