#!/usr/bin/env python3
"""End-to-end smoke test against a built Docker image with real MuseScore CLI."""
from __future__ import annotations
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile

def main():
    BASE = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:18765'


    def get(path, data=None, content_type=None):
        headers={}
        if content_type: headers['Content-Type']=content_type
        req=urllib.request.Request(BASE+path, data=data, headers=headers, method='POST' if data else 'GET')
        with urllib.request.urlopen(req,timeout=165) as response:
            return response.read()


    xml=b'''<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">
    <layout><staves><staffLayout description="Piano"/></staves></layout>
    <systems><system><staves><staff layout="Piano"><voices><voice><noteObjects>
    <clefSign clef="treble"/><keySign fifths="0"/><timeSign time="2/4"/>
    <chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="E5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="G5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="C6"/></heads></chord>
    </noteObjects></voice></voices></staff></staves></system></systems></score>'''


    def make_upload(xml_bytes=xml):
        score=io.BytesIO()
        with zipfile.ZipFile(score,'w') as z:
            z.writestr('score.xml', xml_bytes)
        boundary='CAPELLABRIDGE_SMOKE_BOUNDARY'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="Smoke.capx"\r\n'
              'Content-Type: application/octet-stream\r\n\r\n').encode()+score.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
        return body, 'multipart/form-data; boundary='+boundary


    for attempt in range(40):
        try:
            with urllib.request.urlopen(BASE+'/health',timeout=3) as response:
                health=json.loads(response.read())
            if health.get('status')=='ok':
                assert health.get('engine_available'), health
                break
        except (OSError, TimeoutError, urllib.error.URLError):
            pass
        time.sleep(2)
    else:
        raise SystemExit('The container did not become healthy')

    for layout in ('balanced', 'compact', 'original'):
        body,ctype=make_upload()
        result=json.loads(get('/api/convert?layout='+layout, body, ctype))
        assert result['results'], (layout,result)
        one=result['results'][0]
        assert one['ok'], (layout,one)
        assert one['measures']==2, (layout,one)
        assert len(one['parts'])==1, (layout,one)
        mscz=get(one['downloads']['mscz'])
        with zipfile.ZipFile(io.BytesIO(mscz)) as z:
            assert any(n.endswith('.mscx') for n in z.namelist()), z.namelist()
            from xml.etree import ElementTree as ET
            score=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
            assert score.findtext('./Score/Style/showMeasureNumber')=='1'
            assert score.findtext('./Score/Style/measureNumberSystem')=='1'
            assert score.findtext('./Score/Style/measureNumberAllStaffs')=='0'
        assert get(one['downloads']['pdf']).startswith(b'%PDF-')
        assert get(one['downloads']['preview']).startswith(b'\x89PNG\r\n\x1a\n')
        assert get(one['downloads']['musicxml']).startswith(b'<?xml')
        with zipfile.ZipFile(io.BytesIO(get(result['batch_url']))) as z:
            assert any(name.endswith('.mscz') for name in z.namelist())
        print(f'{layout}: MuseScore MSCZ, PDF, PNG, MusicXML and ZIP verified')
    # Diagnostic: MuseScore's handling of four grouped independent parts.
    staff_xml = ''.join(
        f'<staff layout="Piccolo {n}"><voices><voice><noteObjects>'
        '<timeSign time="2/4"/>'
        '<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
        '</noteObjects></voice></voices></staff>'
        for n in range(1, 5)
    )
    score_xml = (
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves>'
        + ''.join(f'<staffLayout description="Piccolo {n}"/>' for n in range(1, 5))
        + '</staves><brackets><bracket from="0" to="3"/></brackets></layout>'
        '<systems><system><staves>' + staff_xml + '</staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(score_xml)
    result=json.loads(get('/api/convert?layout=balanced', body, ctype))
    assert result['results'][0]['ok'], result
    mscz=get(result['results'][0]['downloads']['mscz'])
    with zipfile.ZipFile(io.BytesIO(mscz)) as z:
        from xml.etree import ElementTree as ET
        root=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
        part_info=[]
        for p in root.findall('./Score/Part'):
            part_info.append([ET.tostring(ch,encoding='unicode')[:450] for ch in p.findall('./Staff')])
        print('DEBUG_NATIVE_PART_STAVES',str(part_info)[:3500],flush=True)
        print('DEBUG_NATIVE_INITIAL_STAVES',
              [ET.tostring(x,encoding='unicode')[:250] for x in root.findall('./Score/Staff')[:4]],flush=True)
    print('Four-staff group conversion diagnostic passed',flush=True)

    print('All three layout modes passed the live Docker conversion test')


if __name__ == "__main__":
    main()
