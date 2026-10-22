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
    staff_xml = ''
    for n in range(1, 5):
        decorations = ''
        if n == 1:
            decorations = (
                '<drawObjects>'
                '<drawObj><text x="0" y="-25"><font face="Lato" height="37"/>'
                '<content>Concert Example</content></text></drawObj>'
                '<drawObj><text x="0" y="-17"><font face="Lato" height="17"/>'
                '<content>Four flutes&#10;von Example Composer</content></text></drawObj>'
                '<drawObj><text x="-3" y="-5"><font face="Lato" height="17"/>'
                '<content>1</content></text></drawObj>'
                '</drawObjects>'
            )
        staff_xml += (
            f'<staff layout="Piccolo {n}"><voices><voice><noteObjects>'
            '<timeSign time="2/4"/>'
            '<chord><duration base="1/4"/>'
            + decorations +
            '<heads><head pitch="C5"/></heads></chord>'
            '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
            '</noteObjects></voice></voices></staff>'
        )
    score_xml = (
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves>'
        + ''.join(f'<staffLayout description="Piccolo {n}"/>' for n in range(1, 5))
        + '</staves><brackets><bracket from="0" to="3"/></brackets></layout>'
        '<systems><system><staves>' + staff_xml + '</staves></system></systems></score>'
    ).encode()
    for ensemble_layout in ('balanced', 'compact', 'original'):
        body,ctype=make_upload(score_xml)
        result=json.loads(get('/api/convert?layout='+ensemble_layout, body, ctype))
        assert result['results'][0]['ok'], result
        from xml.etree import ElementTree as ET
        music_xml=ET.fromstring(get(result['results'][0]['downloads']['musicxml']))
        assert music_xml.findtext('./movement-title') == 'Concert Example'
        assert music_xml.findtext('./identification/creator') == 'Example Composer'
        words=music_xml.findall('./part/measure/direction/direction-type/words')
        assert [x.text for x in words] == ['1'], [(x.text,x.attrib) for x in words]
        mscz=get(result['results'][0]['downloads']['mscz'])
        with zipfile.ZipFile(io.BytesIO(mscz)) as z:
            from xml.etree import ElementTree as ET
            root=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
            part_info=[]
            for p in root.findall('./Score/Part'):
                part_info.append([ET.tostring(ch,encoding='unicode')[:450] for ch in p.findall('./Staff')])
            print('DEBUG_NATIVE_PART_STAVES',str(part_info)[:3500],flush=True)
            spans=[p.findtext('./Staff/barLineSpan') for p in root.findall('./Score/Part')]
            assert spans == ['1','1','1',None], spans
            first_vbox=root.find('./Score/Staff/VBox')
            if first_vbox is not None:
                print('DEBUG_TITLE_VBOX',ET.tostring(first_vbox,encoding='unicode')[:3000],flush=True)
            assert len(root.findall('./Score/Staff'))==4
            print('DEBUG_NATIVE_INITIAL_STAVES',
                  [ET.tostring(x,encoding='unicode')[:250] for x in root.findall('./Score/Staff')[:4]],flush=True)
        import fitz
        rendered=get(result['results'][0]['downloads']['pdf'])
        with fitz.open(stream=rendered,filetype='pdf') as doc:
            page=doc[0]
            import unicodedata
            spans=[span for block in page.get_text('dict')['blocks'] if 'lines' in block
                   for line in block['lines'] for span in line['spans']]
            heads={
                name:[fitz.Rect(span['bbox']) for span in spans
                      if name in unicodedata.normalize('NFKC',span['text'])]
                for name in ('Concert Example','Four flutes','Example Composer')
            }
            print('DEBUG_RENDERED_TITLES',
                  {key:[tuple(round(v,1) for v in r) for r in rects]
                   for key,rects in heads.items()},flush=True)
            print('DEBUG_FIRST_PAGE_TEXT',repr(page.get_text()[:800]),flush=True)
            assert all(heads.values()), heads
            assert heads['Concert Example'][0].y1 <= heads['Four flutes'][0].y0+1
            assert heads['Four flutes'][0].y1 <= heads['Example Composer'][0].y0+1
            numbers=[(span['text'],hex(span['color'])) for span in spans
                     if span['text'].strip()=='1']
            print('DEBUG_RENDERED_NUMBERS',numbers[:20],flush=True)
            assert numbers, 'Source variation number absent from final PDF'
        print(f'Four-staff {ensemble_layout}: PDF credits, variation number, and barlines verified',flush=True)

    print('All three layout modes passed the live Docker conversion test')


if __name__ == "__main__":
    main()
