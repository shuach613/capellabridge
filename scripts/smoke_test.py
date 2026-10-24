#!/usr/bin/env python3
"""End-to-end smoke test against a built Docker image with real MuseScore CLI."""
from __future__ import annotations
import io
import os
from pathlib import Path
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


    def make_upload(xml_bytes=xml, filename='Smoke.capx', extras=None):
        score=io.BytesIO()
        with zipfile.ZipFile(score,'w') as z:
            z.writestr('score.xml', xml_bytes)
            for name,content in (extras or {}).items():
                z.writestr(name,content)
        boundary='CAPELLABRIDGE_SMOKE_BOUNDARY'
        body=(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
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
                '<content>Four flutes in an extended ensemble arrangement&#10;A second subtitle line for the quartet&#10;von Example Composer</content></text></drawObj>'
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
        if os.environ.get('STUDIO_COMPAT_OUTPUT') and ensemble_layout=='balanced':
            Path(os.environ['STUDIO_COMPAT_OUTPUT']).write_bytes(mscz)

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
                for name in ('Concert Example','Four flutes','A second subtitle','Example Composer')
            }
            print('DEBUG_RENDERED_TITLES',
                  {key:[tuple(round(v,1) for v in r) for r in rects]
                   for key,rects in heads.items()},flush=True)
            print('DEBUG_FIRST_PAGE_TEXT',repr(page.get_text()[:800]),flush=True)
            assert all(heads.values()), heads
            assert heads['Concert Example'][0].y1 <= heads['Four flutes'][0].y0+1
            assert heads['Four flutes'][0].y1 <= heads['A second subtitle'][0].y0+1
            assert heads['A second subtitle'][0].y1 <= heads['Example Composer'][0].y0+1
            assert '<br/>' not in page.get_text(), 'Literal linebreak markup printed in PDF'
            numbers=[(span['text'],hex(span['color'])) for span in spans
                     if span['text'].strip()=='1']
            print('DEBUG_RENDERED_NUMBERS',numbers[:20],flush=True)
            assert numbers, 'Source variation number absent from final PDF'
        print(f'Four-staff {ensemble_layout}: PDF credits, variation number, and barlines verified',flush=True)

    # Exercise actual MuseScore CLI with changing meters, triplets, grace
    # notes, and integer whole-measure rests rather than just a simple tune.
    def one_staff(name, music):
        return (f'<staff layout="{name}"><voices><voice><noteObjects>'
                +music+'</noteObjects></voice></voices></staff>')
    first = ('<timeSign time="2/4"/>'
             '<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
             '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>')
    second = ('<timeSign time="6/8"/>'
              '<chord><duration base="1/32" noDuration="true"/>'
              '<heads><head pitch="G5"/></heads></chord>'
              + ''.join('<chord><duration base="1/8"><tuplet count="3"/></duration>'
                        '<heads><head pitch="C5"/></heads></chord>' for _ in range(3))
              + '<chord><duration base="1/2"/><heads><head pitch="D5"/></heads></chord>')
    mixed_xml = (
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Upper"/>'
        '<staffLayout description="Lower"/></staves></layout><systems>'
        '<system><staves>'+one_staff('Upper',first)
        +one_staff('Lower','<timeSign time="2/4"/>'
                   '<rest><duration base="1"/></rest>')+'</staves></system>'
        '<system><staves>'+one_staff('Upper',second)
        +one_staff('Lower','<timeSign time="6/8"/>'
                   '<rest><duration base="1"/></rest>')+'</staves></system>'
        '</systems></score>'
    ).encode()
    body,ctype=make_upload(mixed_xml)
    res=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert res['results'][0]['ok'],res
    item=res['results'][0]
    assert item['measures']==2,item
    assert not any('crosses a barline' in w for w in item['warnings']),item
    from xml.etree import ElementTree as ET
    musicxml=ET.fromstring(get(item['downloads']['musicxml']))
    assert len(musicxml.findall('./part'))==2
    assert len(musicxml.findall('./part/measure[2]/note/grace'))==1
    assert len(musicxml.findall('./part/measure[2]/note/time-modification'))==3
    assert get(item['downloads']['pdf']).startswith(b'%PDF-')
    packed=get(item['downloads']['mscz'])
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        native=ET.fromstring(archive.read(next(
            name for name in archive.namelist() if name.endswith('.mscx'))))
        assert len(native.findall('./Score/Staff'))==2
    print('Multi-meter, tuplets, grace notes and measure rests rendered by MuseScore',flush=True)

    # Two repeat signs can occupy one instant: previous-system repEnd
    # plus next-system repBegin. Verify both survive MuseScore round-trips.
    def repeat_system(prefix, ending):
        chord='<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
        return ('<system><staves><staff layout="Flute"><voices><voice><noteObjects>'
                +prefix+chord+chord+ending+
                '</noteObjects></voice></voices></staff></staves></system>')
    repeat_xml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Flute"/></staves></layout>'
        '<systems>'
        +repeat_system('<timeSign time="2/4"/>','<barline type="repEnd"/>')
        +repeat_system('<barline type="repBegin"/>','<barline type="repEndBegin"/>')
        +repeat_system('','')
        +'</systems></score>'
    ).encode()
    body,ctype=make_upload(repeat_xml)
    result=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert result['results'][0]['ok'],result
    item=result['results'][0]
    assert item['repeat_boundaries']=={'forward':2,'backward':2},item
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
        assert len(native.findall('.//startRepeat'))==2
        assert len(native.findall('.//endRepeat'))==2
    print('Joined backward+forward Capella repeats survived native MuseScore export')

    # Cross-file safeguard: source-wide strongBeat markers should not
    # become marcato accents on literally every note in MuseScore.
    marked_notes=''.join(
        '<chord><duration base="1/4"/><articulation type="strongBeat"/>'
        '<heads><head pitch="C5"/></heads></chord>' for _ in range(40)
    )
    marked_xml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Flute"/></staves></layout>'
        '<systems><system><staves><staff layout="Flute"><voices><voice><noteObjects>'
        '<timeSign time="4/4"/>'+marked_notes+
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(marked_xml)
    response=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert response['results'][0]['ok'],response
    entry=response['results'][0]
    assert entry['normalized_uniform_strongbeat']==40,entry
    with zipfile.ZipFile(io.BytesIO(get(entry['downloads']['mscz']))) as z:
        from xml.etree import ElementTree as ET
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
        assert len(native.findall('.//Chord'))==40
        assert not any(
            a.findtext('subtype')=='articMarcatoAbove'
            for a in native.findall('.//Articulation')
        ),'Uniform accents were reintroduced during MuseScore conversion'
    print('Uniform strongBeat marks suppressed in final editable MSCZ',flush=True)

    # Source-library patterns: Waggis introduces an additional piccolo only
    # in its final system; Calvados uses 7-bar rests and a 2/4 -> 6/8 change.
    def staff_by_name(name,notes):
        return f'<staff layout="{name}"><voices><voice><noteObjects>{notes}</noteObjects></voice></voices></staff>'
    names=['Piccolo 1','Piccolo 2','Piccolo 3','Piccolo 4']
    quarter='<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
    opening=''.join(staff_by_name(n,'<timeSign time="2/4"/>'+quarter+quarter)
                    for n in names[:3])
    ending=''.join(staff_by_name(n,quarter+quarter) for n in names)
    variable_xml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<pageObjects><drawObj><text x="50" y="4"><font face="Arial" height="21"/>'
        '<content>Waggis</content></text></drawObj>'
        '<drawObj><text x="100" y="3"><font face="Arial" height="12"/>'
        '<content>Karl Scherrer</content></text></drawObj></pageObjects>'
        '<layout><staves>'+''.join(f'<staffLayout description="{n}"/>' for n in names)+
        '</staves><brackets><bracket from="0" to="3"/></brackets></layout>'
        '<systems><system><staves>'+opening+'</staves></system>'
        '<system><staves>'+ending+'</staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(variable_xml)
    rsp=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert rsp['results'][0]['ok'],rsp
    score=rsp['results'][0]
    assert score['variable_staff_systems'] is True,score
    assert len(score['parts'])==4,score
    assert score['title']=='Waggis',score
    from xml.etree import ElementTree as ET
    xmltree=ET.fromstring(get(score['downloads']['musicxml']))
    last=xmltree.findall('./part')[3]
    assert last.find('./measure[1]/attributes/staff-details').get('print-object')=='no'
    assert last.find('./measure[2]/attributes/staff-details').get('print-object')=='yes'
    assert get(score['downloads']['pdf']).startswith(b'%PDF-')
    # The cross-version CI run must reopen the challenging Waggis-like
    # score, not merely a stable four-part score without staff transitions.
    if os.environ.get('STUDIO_COMPAT_OUTPUT'):
        Path(os.environ['STUDIO_COMPAT_OUTPUT']).write_bytes(
            get(score['downloads']['mscz'])
        )

    print('Later-appearing fourth staff and page-level credits rendered by MuseScore',flush=True)

    old_rest='<rest><duration base="7"/></rest>'
    solo='<rest><duration base="1/4"/><drawObjects><drawObj><text y="-4">'
    solo+='<font face="Arial" height="12"/><content>Trommelsolo</content>'
    solo+='</text></drawObj></drawObjects></rest>'
    names=names[:3]
    first=''.join(staff_by_name(n,'<timeSign time="2/4"/>'+old_rest) for n in names)
    second=''.join(staff_by_name(n,
                   '<timeSign time="6/8"/>'+ (solo if i==0 else quarter)
                   +'<chord><duration base="1/2"/><heads><head pitch="D5"/></heads></chord>')
                   for i,n in enumerate(names))
    long_rest_xml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves>'+''.join(f'<staffLayout description="{n}"/>' for n in names)+
        '</staves></layout>'
        '<systems><system><staves>'+first+'</staves></system>'
        '<system><staves>'+second+'</staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(long_rest_xml)
    rsp=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert rsp['results'][0]['ok'],rsp
    score=rsp['results'][0]
    assert score['measures']==8,score
    assert 'Trommelsolo' in score['printed_directions'],score
    assert get(score['downloads']['pdf']).startswith(b'%PDF-')
    with zipfile.ZipFile(io.BytesIO(get(score['downloads']['mscz']))) as z:
        assert any(n.endswith('.mscx') for n in z.namelist())
    print('Seven-measure rest and 6/8 transition rendered and archived',flush=True)

    # CapXML 3.0 can replace a quartet with a new solo Zier staff.
    # A graphical title logo (WMF) is not machine-readable; the upload
    # filename becomes the editable title while composer text is preserved.
    layouts=['Piccolo 1','Piccolo 2','Piccolo 3','Piccolo 4','Piccolo Zier']
    regular=''.join(staff_by_name(n,'<timeSign time="2/4"/>'+quarter+quarter)
                    for n in layouts[:4])
    solo=staff_by_name('Piccolo Zier',quarter+quarter)
    v3=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/3.0">'
        '<pageObjects><drawObj><metafile file="logo.wmf" x="65" y="-3" width="32" height="11"/></drawObj>'
        '<drawObj><text x="150" y="2"><font face="Arial" height="11"/>'
        '<content>Michael Robertson&#10;Trommeltext: Andri Obrist</content>'
        '</text></drawObj></pageObjects><layout><staves>'+
        ''.join(f'<staffLayout description="{n}"/>' for n in layouts)
        +'</staves></layout><systems>'
        '<system><staves>'+regular+'</staves></system>'
        '<system><staves>'+solo+'</staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(v3,filename='Fyyrvogel.capx')
    output=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert output['results'][0]['ok'],output
    record=output['results'][0]
    assert record['title']=='Fyyrvogel',record
    assert record['composer']=='Michael Robertson',record
    assert record['capxml_version']=='3.0',record
    assert len(record['parts'])==5,record
    assert get(record['downloads']['pdf']).startswith(b'%PDF-')
    with zipfile.ZipFile(io.BytesIO(get(record['downloads']['mscz']))) as z:
        assert any(n.endswith('.mscx') for n in z.namelist())
    print('CapXML 3.0 graphical title and full-staff replacement rendered',flush=True)

    # CapXML 1.0: two independent voices in one staff, and a graphical
    # red diminuendo wedge linked to the following note.
    wedge=('<drawObjects><drawObj><wedge decrescendo="true" color="FF0000"/>'
           '<basic noteRange="1"/></drawObj></drawObjects>')
    first_voice='<timeSign time="2/4"/>'+(
        '<chord><duration base="1/4"/>'+wedge+
        '<heads><head pitch="E5"/></heads></chord>')+quarter
    second_voice=''.join(
        '<chord><duration base="1/8"/><heads><head pitch="C5"/></heads></chord>'
        for _ in range(4)
    )
    v1=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/1.0">'
        '<layout><staves><staffLayout description="1. Stimme"/></staves></layout>'
        '<systems><system><staves><staff layout="1. Stimme"><voices>'
        '<voice><noteObjects>'+first_voice+'</noteObjects></voice>'
        '<voice><noteObjects>'+second_voice+'</noteObjects></voice>'
        '</voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(v1,filename='Nunnefirzli.capx')
    output=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert output['results'][0]['ok'],output
    record=output['results'][0]
    assert record['capxml_version']=='1.0',record
    xmltree=ET.fromstring(get(record['downloads']['musicxml']))
    assert len(xmltree.findall('./part/measure/note[voice="2"]'))==4
    assert xmltree.find('.//wedge[@type="diminuendo"]') is not None
    assert xmltree.find('.//wedge[@type="stop"]') is not None
    assert get(record['downloads']['pdf']).startswith(b'%PDF-')
    print('CapXML 1.0 polyphony and crescendo/diminuendo graphics rendered',flush=True)

    # Legacy Capella3 font symbols must round-trip through real MuseScore,
    # not only through the Python-to-MusicXML parser.
    def glyph_note(pitch, glyph):
        return (
            '<chord><duration base="1/4"/>'
            '<drawObjects><drawObj><text x="0" y="5">'
            '<font face="capella3" height="18"/><content>'+glyph+
            '</content></text></drawObj></drawObjects>'
            '<heads><head pitch="'+pitch+'"/></heads></chord>'
        )
    dynamic_capxml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/3.0">'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="4/4"/>'
        +glyph_note('C5','p')+glyph_note('D5','f')
        +glyph_note('E5','j')+glyph_note('F5','i')
        +'</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,content_type=make_upload(dynamic_capxml)
    dynamic_reply=json.loads(get('/api/convert?layout=balanced',body,content_type))
    assert dynamic_reply['results'][0]['ok'],dynamic_reply
    dynamic_entry=dynamic_reply['results'][0]
    from xml.etree import ElementTree as ET
    exported=ET.fromstring(get(dynamic_entry['downloads']['musicxml']))
    dynamic_values=[child.tag for d in exported.findall('.//direction-type/dynamics')
                    for child in d]
    assert dynamic_values==['p','f','mf','mp'],dynamic_values
    archive=get(dynamic_entry['downloads']['mscz'])
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
        assert len(native.findall('.//Dynamic'))>=4, 'MuseScore dropped Capella dynamics'
    assert get(dynamic_entry['downloads']['pdf']).startswith(b'%PDF-')
    print('Legacy Capella p, f, mf, mp survived native MuseScore/PDF export',flush=True)

    # New reference scores: real MuseScore rendering of a boxed rehearsal
    # mark, a circled Capella3 number, and a credited arranger.
    # This is synthetic and does not publish private user-owned scores.
    rehearsal=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/3.0">'
        '<pageObjects><drawObj><text x="60" y="5"><font face="Arial" height="20"/>'
        '<content>CIRCUS Märschli</content></text></drawObj>'
        '<drawObj><text x="130" y="5"><font face="Arial" height="11"/>'
        '<content>arrangiert vom Thomas Heid</content></text></drawObj></pageObjects>'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="1/4"/>'
        '<chord><duration base="1/4"/>'
        '<drawObjects><drawObj><rectangle x1="-4" y1="-9" x2="0" y2="-5"/>'
        '<basic noAdjust="true"/></drawObj><drawObj>'
        '<text y="-6"><font face="Arial" height="12"/><content>A</content>'
        '</text></drawObj></drawObjects><heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/4"/>'
        '<drawObjects><drawObj><text y="-8"><font face="capella3" height="18"/>'
        '<content>2</content></text></drawObj><drawObj>'
        '<ellipse x1="-5" y1="-10" x2="0" y2="-5"/><basic noAdjust="true"/>'
        '</drawObj></drawObjects><heads><head pitch="D5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="E5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="F5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(rehearsal,filename='Circus-Marks.capx')
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    record=reply['results'][0]
    assert record['variation_numbers']==['A','②'],record
    from xml.etree import ElementTree as ET
    xmltree=ET.fromstring(get(record['downloads']['musicxml']))
    assert xmltree.findtext('./identification/creator[@type="arranger"]')=='Thomas Heid'
    boxed=xmltree.find('.//direction-type/words[.="A"]')
    assert boxed is not None and boxed.get('enclosure')=='rectangle'
    assert xmltree.find('.//direction-type/words[.="②"]') is not None
    mscz=get(record['downloads']['mscz'])
    with zipfile.ZipFile(io.BytesIO(mscz)) as z:
        assert any(name.endswith('.mscx') for name in z.namelist())
    assert get(record['downloads']['pdf']).startswith(b'%PDF-')
    print('Circus/Elfer boxed and circled headings rendered by native MuseScore',flush=True)

    # Two distinct tempo encodings from the new source references:
    # a printed Fyyrvogel quarter-note=95 grouped graphic and a Glaibasler
    # playback-only tempo=180. Both must import into real MuseScore.
    metronome_xml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/3.0">'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="4/4"/>'
        '<chord><duration base="1/4"/><drawObjects><drawObj><group>'
        '<drawObj><text y="-7"><font face="capella3" height="18"/>'
        '<content>¢</content></text></drawObj>'
        '<drawObj><text y="-7"><font face="Times New Roman" height="10"/>'
        '<content>=95</content></text></drawObj>'
        '</group></drawObj></drawObjects>'
        '<heads><head pitch="C5"/></heads></chord>'
        '<chord tempo="180"><duration base="1/4"/>'
        '<heads><head pitch="D5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="E5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="F5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(metronome_xml,filename='TempoPrintAndPlayback.capx')
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    record=reply['results'][0]
    from xml.etree import ElementTree as ET
    parsed=ET.fromstring(get(record['downloads']['musicxml']))
    assert parsed.findtext('.//metronome/per-minute')=='95'
    assert parsed.find('.//direction/sound[@tempo="95"]') is not None
    assert parsed.find('./part/measure/sound[@tempo="180"]') is not None
    with zipfile.ZipFile(io.BytesIO(get(record['downloads']['mscz']))) as z:
        assert any(x.endswith('.mscx') for x in z.namelist())
    assert get(record['downloads']['pdf']).startswith(b'%PDF-')
    print('Printed 95 BPM and playback-only 180 BPM survived live MuseScore export',flush=True)

    # Verify that the actual native MuseScore CLI accepts the repaired
    # first-page title and separate metadata/rights credit. Regression
    # modelled after a low-positioned Capella title, without publishing
    # the original Hanswurscht score.
    byline_xml=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
        '<pageObjects>'
        '<drawObj><text x="55" y="9.3125"><font face="Arial" height="28"/>'
        '<content>Dr Hanswurscht</content></text></drawObj>'
        '<drawObj><text x="0" y="6.125"><font face="Arial" height="12"/>'
        '<content>© Znynibigger</content></text></drawObj>'
        '<drawObj><text x="96.46875" y="6.125">'
        '<font face="Arial" height="12"/><content>René Brielmann 1982'
        '</content></text></drawObj></pageObjects>'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="2/4"/>'
        '<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems>'
        '</score>'
    ).encode()
    body,ctype=make_upload(byline_xml,filename='Dr Hanswurscht.capx')
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    assert item['title']=='Dr Hanswurscht'
    assert item['composer']=='René Brielmann 1982'
    xmltree=ET.fromstring(get(item['downloads']['musicxml']))
    assert xmltree.findtext('./identification/rights')=='© Znynibigger'
    assert xmltree.findtext('./identification/creator[@type="composer"]')=='René Brielmann 1982'
    assert get(item['downloads']['pdf']).startswith(b'%PDF-')
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        assert any(x.endswith('.mscx') for x in z.namelist())
    print('Low title and separate rights/composer survived native MuseScore export',flush=True)

    # D'Preusse uses compact capella3 inline glyphs "¢=100"/"¢=110"
    # rather than Fyyrvogel's grouped metronome. Check the real output
    # pipeline without publishing user-owned CAPX or copyrighted notes.
    source=(
        '<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/3.0">'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="1/4"/>'
        '<chord><duration base="1/4"/><drawObjects><drawObj>'
        '<text y="-5"><font face="capella3" height="18"/>'
        '<content>¢=100</content></text></drawObj></drawObjects>'
        '<heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/4"/><drawObjects><drawObj>'
        '<text y="-5"><font face="capella3" height="24"/>'
        '<content>14</content></text></drawObj><drawObj>'
        '<text y="-7"><font face="Bookman Old Style" height="9" italic="true"/>'
        '<content>Der Königsgrätzer</content></text></drawObj><drawObj>'
        '<text y="-4"><font face="capella3" height="18"/>'
        '<content>¢=110</content></text></drawObj></drawObjects>'
        '<heads><head pitch="D5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems>'
        '</score>'
    ).encode('utf-8')
    body,ctype=make_upload(source,filename='Preusse-tempo.capx')
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    assert item['variation_numbers']==['14']
    assert 'Der Königsgrätzer' in item['printed_directions']
    xmltree=ET.fromstring(get(item['downloads']['musicxml']))
    assert [x.text for x in xmltree.findall('.//metronome/per-minute')]==['100','110']
    assert get(item['downloads']['pdf']).startswith(b'%PDF-')
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        assert any(x.endswith('.mscx') for x in z.namelist())
    print('Preusse inline 100/110 BPM and multi-digit section survived MuseScore export',flush=True)

    # A source-shaped four-part Sperber fixture: the visible author, date
    # and dedication stay together, while composer metadata is name-only.
    # The full original score is private and not stored in this test.
    page=(
        '<pageObjects>'
        '<drawObj><text x="49.90625" y="3"><font face="Bookman Old Style" height="16"/>'
        '<content>Sperber</content></text></drawObj>'
        '<drawObj><text x="49.96875" y="6.0625">'
        '<font face="Bookman Old Style" height="9"/>'
        '<content>René Brielmann, 22.06.1983, für die Musketiere</content>'
        '</text></drawObj></pageObjects>'
    )
    staves=[]
    layouts=[]
    for number in range(4):
        name='Piccolo-'+str(number+1)
        layouts.append('<staffLayout description="'+name+'"/>')
        score_marks=(
            '<drawObjects><drawObj><text x="-2" y="-5">'
            '<font face="capella3" height="22"/>'
            '<content>1</content></text></drawObj></drawObjects>'
            if number==0 else '')
        staves.append(
            '<staff layout="'+name+'"><voices><voice><noteObjects>'
            '<timeSign time="2/4"/><chord><duration base="1/4"/>'
            +score_marks+'<heads><head pitch="C5"/></heads></chord>'
            '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
            '</noteObjects></voice></voices></staff>')
    mini=(
        '<score xmlns="http://www.capella.de/CapXML/3.0">'+page
        +'<layout><staves>'+''.join(layouts)+'</staves></layout>'
        '<systems><system><staves>'+''.join(staves)
        +'</staves></system></systems></score>'
    ).encode('utf-8')
    body,ctype=make_upload(mini,filename='Sperber.capx')
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    assert item['title']=='Sperber'
    assert item['composer']=='René Brielmann'
    assert item['subtitle']=='René Brielmann, 22.06.1983, für die Musketiere'
    xmltree=ET.fromstring(get(item['downloads']['musicxml']))
    assert len(xmltree.findall('./part'))==4
    assert xmltree.findtext('./identification/creator[@type="composer"]')=='René Brielmann'
    visible=[v.text for v in xmltree.findall('./credit/credit-words')]
    assert visible.count('René Brielmann, 22.06.1983, für die Musketiere')==1
    assert get(item['downloads']['pdf']).startswith(b'%PDF-')
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        assert any(n.endswith('.mscx') for n in z.namelist())
    print('Sperber composer and full printed dedication survived native MuseScore export',flush=True)

    # Native MuseScore smoke: final source-pair navigation, including an
    # embedded RTF D.S. al Segno / Coda instruction. Synthetic only.
    rtf_content=(
        r'{\rtf1\ansi{\fonttbl{\f0 capella3;}{\f1 Arial;}}'
        r'\pard\f0 e\f1  al \f0 $\f1  con rep. et poi Coda\par}'
    ).encode('cp1252')
    rtf_xml=(
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<pageObjects><drawObj><text x="49" y="4">'
        '<font face="Arial" height="18"/><content>Uncle Sam</content>'
        '</text></drawObj></pageObjects>'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="2/4"/>'
        '<chord><duration base="1/4"/><drawObjects><drawObj>'
        '<text x="0" y="-5"><font face="capella3" height="18"/>'
        '<content>M</content></text></drawObj></drawObjects>'
        '<heads><head pitch="C5"/></heads></chord>'
        '<rest><duration base="1/4"/><drawObjects><drawObj>'
        '<text x="0" y="-5"><font face="capella3" height="18"/>'
        '<content>L</content></text></drawObj><drawObj>'
        '<richText file="obj____1.rtf" x="-5.5" y="-14" width="22"/>'
        '</drawObj></drawObjects></rest>'
        '</noteObjects></voice></voices></staff></staves></system></systems>'
        '</score>'
    ).encode('utf-8')
    body,ctype=make_upload(rtf_xml,filename='Uncle Sam.capx',
                            extras={'obj____1.rtf':rtf_content})
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    xmltree=ET.fromstring(get(item['downloads']['musicxml']))
    words=[d.text for d in xmltree.findall('.//direction-type/words')]
    assert 'D.S. al Segno con rep. et poi Coda' in words,words
    assert '\U0001D13E' in words and '\U0001D13F' in words,words
    assert xmltree.find('.//direction/sound[@dalsegno="segno"][@tocoda="coda"]') is not None
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        assert any(n.endswith('.mscx') for n in z.namelist())
    assert get(item['downloads']['pdf']).startswith(b'%PDF-')
    print('Embedded RTF D.S. al Segno navigation survived native MSCZ/PDF export',flush=True)

    # Regression through the actual MuseScore importer, not just MusicXML:
    # Waggis's hidden-note/shifted-tie/graphic-rest workaround must
    # become one audible/visible note followed by a REAL timed eighth rest.
    # This guards both correct engraving and playback rhythm.
    tricky=(
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Piccolo 1"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo 1"><voices>'
        '<voice><noteObjects><timeSign time="2/4"/>'
        '<chord><duration base="1/8"/><display invisible="true" xShift="1.25"/>'
        '<heads><head pitch="D7"><tie begin="true"/></head></heads></chord>'
        '<chord><duration base="1/8"/><display xShift="-1.75"/>'
        '<articulation type="strongAccent"/>'
        '<drawObjects><drawObj><text x="3" y="-3">'
        '<font face="capella3" height="18"/><content>L</content>'
        '</text></drawObj></drawObjects>'
        '<heads><head pitch="D7"><tie end="true"/></head></heads></chord>'
        '<chord><duration base="1/4"/><drawObjects><drawObj>'
        '<text x="0" y="-3"><font face="capella3" height="18"/>'
        '<content>M</content></text></drawObj></drawObjects>'
        '<heads><head pitch="E7"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode('utf-8')
    body,ctype=make_upload(tricky,filename='HiddenShiftedRest.capx')
    response=json.loads(get('/api/convert?layout=original',body,ctype))
    assert response['results'][0]['ok'],response
    result=response['results'][0]
    from xml.etree import ElementTree as ET
    mx=ET.fromstring(get(result['downloads']['musicxml']))
    ns=mx.findall('./part/measure/note')
    assert len(ns)==3
    assert ns[0].get('print-object')!='no'
    assert ns[1].get('print-object')!='no'
    assert ns[0].find('./pitch') is not None
    assert ns[1].find('./rest') is not None
    assert ns[0].find('./tie') is None
    assert ns[1].find('./tie') is None
    assert mx.find('.//direction-type/words[.="𝄾"]') is None
    with zipfile.ZipFile(io.BytesIO(get(result['downloads']['mscz']))) as z:
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
    native_chords=native.findall('./Score/Staff/Measure/voice/Chord')
    assert len(native_chords)==2, len(native_chords)
    assert native_chords[0].findtext('visible')!='0'
    native_rests=native.findall('./Score/Staff/Measure/voice/Rest')
    assert any(r.findtext('durationType')=='eighth' for r in native_rests)
    symbols=[n.text for n in native.findall('.//Symbol/name')]
    assert 'rest16th' in symbols,symbols
    visual_out=Path('/tmp/capellabridge-render-check')
    visual_out.mkdir(parents=True,exist_ok=True)
    visual_pdf=get(result['downloads']['pdf'])
    assert visual_pdf.startswith(b'%PDF-')
    (visual_out/'Waggis-hidden-shifted-rest.pdf').write_bytes(visual_pdf)
    import fitz
    with fitz.open(stream=visual_pdf,filetype='pdf') as pdf_doc:
        glyphs=''.join(c['c'] for block in pdf_doc[0].get_text('rawdict')['blocks']
                       if 'lines' in block for line in block['lines']
                       for span in line['spans'] for c in span['chars'])
    assert '\ue4e6' in glyphs and '\ue4e7' in glyphs, repr(glyphs)
    assert '<sym>' not in glyphs
    print('Native shifted-note+rest workaround produced timed rest without invisible pitched playback',flush=True)

    multirest=(
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<pageObjects><drawObj><text x="55" y="4">'
        '<font face="Arial" height="22"/><content>Calvados</content>'
        '</text></drawObj></pageObjects>'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="2/4"/>'
        '<rest><duration base="3"/><drawObjects><drawObj>'
        '<text x="0" y="-7"><font face="Arial" height="12"/>'
        '<content>Trommelsolo</content></text></drawObj></drawObjects></rest>'
        '<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(multirest,filename='LongRest.capx')
    reply=json.loads(get('/api/convert?layout=original',body,ctype))
    assert reply['results'][0]['ok'],reply
    one=reply['results'][0]
    xml=ET.fromstring(get(one['downloads']['musicxml']))
    assert len(xml.findall('.//direction-type/words[.="Trommelsolo"]'))==1
    with zipfile.ZipFile(io.BytesIO(get(one['downloads']['mscz']))) as z:
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
    assert native.findtext('./Score/Style/createMultiMeasureRests')=='1'
    texts=[''.join(x.itertext()) for x in native.findall('.//StaffText/text')]
    assert sum('Trommelsolo' in t for t in texts)==1,texts
    visual_pdf=get(one['downloads']['pdf'])
    assert visual_pdf.startswith(b'%PDF-')
    (visual_out/'Calvados-multi-rest.pdf').write_bytes(visual_pdf)
    print('Native multi-measure rest retains one solo direction and multi-rest style',flush=True)

    # Rest-attached first/second endings reproduce Nunnefirzli's last pair.
    # Their source namespace is CapXML 1.0; normalization must not silently
    # discard rest drawObjects or change the ending/repeat semantics.
    volta_score=(
        '<score xmlns="http://www.capella.de/CapXML/1.0">'
        '<pageObjects><drawObj><text x="55" y="4">'
        '<font face="Arial" height="20"/><content>Nunnefirzli</content>'
        '</text></drawObj></pageObjects>'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="1/4"/>'
        '<rest><duration base="1/4"/><drawObjects><drawObj>'
        '<volta firstNumber="1" x1="-2" x2="14" y="-8" />'
        '</drawObj></drawObjects></rest>'
        '<rest><duration base="1/4"/><drawObjects><drawObj>'
        '<volta firstNumber="2" x1="-2" x2="14" y="-8" />'
        '</drawObj></drawObjects></rest>'
        '</noteObjects></voice></voices></staff></staves></system></systems>'
        '</score>'
    ).encode('utf-8')
    body,ctype=make_upload(volta_score,filename='Nunnefirzli-rest-endings.capx')
    reply=json.loads(get('/api/convert?layout=original',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    mx=ET.fromstring(get(item['downloads']['musicxml']))
    ending_numbers=[e.get('number') for e in mx.findall('.//ending[@type="start"]')]
    assert ending_numbers==['1','2'],ending_numbers
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
    native_ending_numbers=[str(x.text) for x in native.findall('.//Volta/endings')]
    assert native_ending_numbers or native.findall('.//Volta'), 'Lost rest-attached volta graphics'
    visual_pdf=get(item['downloads']['pdf'])
    assert visual_pdf.startswith(b'%PDF-')
    (visual_out/'Nunnefirzli-rest-attached-endings.pdf').write_bytes(visual_pdf)
    print('Native rest-attached first and second endings survived MuseScore export',flush=True)

    colored=(
        '<score xmlns="http://www.capella.de/CapXML/1.0">'
        '<pageObjects><drawObj><text x="50" y="4">'
        '<font face="Arial" height="20" color="000080"/>'
        '<content>Blue fidelity</content></text></drawObj></pageObjects>'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices>'
        '<voice><noteObjects><timeSign time="2/4"/>'
        '<chord><duration base="1/4"/><drawObjects><drawObj>'
        '<text x="-1" y="-8"><font face="Arial" height="8" color="0000FF"/>'
        '<content>1  Solo</content></text></drawObj></drawObjects>'
        '<heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/4"/><drawObjects><drawObj>'
        '<text x="2" y="4"><font face="Arial" height="10" color="000080"/>'
        '<content>"glockig"</content></text></drawObj></drawObjects>'
        '<heads><head pitch="D5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(colored,filename='Colored-annotations.capx')
    reply=json.loads(get('/api/convert?layout=original',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    assert item['source_text_colors']['1 Solo']=='#0000FF'
    assert item['source_text_colors']['"glockig"']=='#000080'
    native_pdf=get(item['downloads']['pdf'])
    assert native_pdf.startswith(b'%PDF-')
    (visual_out/'Nunnefirzli-colored-annotations.pdf').write_bytes(native_pdf)
    with fitz.open(stream=native_pdf,filetype='pdf') as pdf_doc:
        spans=[sp for block in pdf_doc[0].get_text('dict')['blocks']
               if 'lines' in block for line in block['lines']
               for sp in line['spans']]
    assert any('Solo' in sp.get('text','') and sp['color']==0x0000FF
               for sp in spans),[(sp['text'],hex(sp['color'])) for sp in spans]
    assert any('glockig' in sp.get('text','') and sp['color']==0x000080
               for sp in spans),[(sp['text'],hex(sp['color'])) for sp in spans]
    print('Source blue expression text survives native MuseScore PDF export',flush=True)

    # A single eighth grace in a 2/4 bar is a zero-duration decoration.
    # Previously the MSCZ auditor counted it as 1/8, falsely claiming
    # "5/8 of 1/2" and rejecting perfectly valid Tango scores.
    tango_grace=(
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
        '<systems><system><staves><staff layout="Piccolo"><voices><voice>'
        '<noteObjects><timeSign time="2/4"/>'
        '<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/8" noDuration="true"/>'
        '<display small="true"/><heads><head pitch="D5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="E5"/></heads></chord>'
        '</noteObjects></voice></voices></staff></staves></system></systems></score>'
    ).encode()
    body,ctype=make_upload(tango_grace,filename='Tango-grace-validation.capx')
    reply=json.loads(get('/api/convert?layout=balanced',body,ctype))
    assert reply['results'][0]['ok'],reply
    item=reply['results'][0]
    mx=ET.fromstring(get(item['downloads']['musicxml']))
    assert len(mx.findall('.//note/grace'))==1
    assert sum(int(n.text) for n in mx.findall('.//note/duration'))==20160
    with zipfile.ZipFile(io.BytesIO(get(item['downloads']['mscz']))) as z:
        native=ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith('.mscx'))))
    assert native.find('.//Chord/grace8') is not None or native.find('.//Chord/grace16') is not None
    assert get(item['downloads']['pdf']).startswith(b'%PDF-')
    print('Native Tango eighth grace survives without false measure-overfull rejection',flush=True)

    print('All three layout modes passed the live Docker conversion test')


if __name__ == "__main__":
    main()
