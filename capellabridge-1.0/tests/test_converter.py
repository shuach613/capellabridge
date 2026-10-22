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


def test_web_upload_and_auth(monkeypatch,tmp_path):
    from app import main
    monkeypatch.setattr(main,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(main,'PASSWORD','secret123')
    monkeypatch.setattr(main,'USERNAME','tester')
    def fake_convert(source,target):
        out=Path(target)
        if out.suffix=='.mscz':
            with zipfile.ZipFile(out,'w') as z:z.writestr('score.mscx','<museScore/>')
        else:out.write_bytes(b'%PDF-1.4\n%test' if out.suffix=='.pdf' else b'<score-partwise/>')
    def fake_preview(pdf,png):Path(png).write_bytes(b'\x89PNG\r\n\x1a\n')
    monkeypatch.setattr(main,'convert',fake_convert)
    monkeypatch.setattr(main,'create_preview',fake_preview)
    client=TestClient(main.app)
    assert client.get('/').status_code==401
    with client:
        response=client.post('/api/convert?layout=balanced',auth=('tester','secret123'),
                             files=[('files',('Tiny.capx',capx(MINIMAL),'application/octet-stream'))])
    assert response.status_code==200,response.text
    payload=response.json()
    assert payload['results'][0]['ok'] is True
    files=payload['results'][0]['downloads']
    assert client.get(files['mscz'],auth=('tester','secret123')).status_code==200
    assert client.get(payload['batch_url'],auth=('tester','secret123')).status_code==200
    assert client.get(files['mscz']).status_code==401
