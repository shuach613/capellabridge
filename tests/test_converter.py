"""Tests run without MuseScore installed. Integration tests mock the CLI only."""
import io
import os
import zipfile
from pathlib import Path
import pytest
from lxml import etree as ET
from fastapi.testclient import TestClient
from app import converter
from app.source_audit import audit_capx_against_musicxml
from app.semantic_audit import SemanticIntegrityError


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


def test_source_audit_catches_the_capx_boundary_before_musescore():
    xml, report = converter.serialize_musicxml(capx(MINIMAL))
    audit = audit_capx_against_musicxml(capx(MINIMAL), xml)
    assert audit['status'] == 'strict'
    assert audit['source_events'] == audit['musicxml_events'] == report['notes']


def test_source_audit_rejects_a_dropped_dynamic():
    dynamic_source=MINIMAL.replace(
        b'<heads><head pitch="C5"/></heads>',
        b'<drawObjects><drawObj><text><font face="capella3"/>'
        b'<content>f</content></text></drawObj></drawObjects>'
        b'<heads><head pitch="C5"/></heads>',1)
    raw=capx(dynamic_source)
    xml,_=converter.serialize_musicxml(raw)
    document=ET.fromstring(xml)
    dynamic=document.xpath('.//direction[direction-type/dynamics]')[0]
    dynamic.getparent().remove(dynamic)
    with pytest.raises(SemanticIntegrityError,match='dynamics changed'):
        audit_capx_against_musicxml(raw,ET.tostring(document))



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
    assert report['variation_numbers']==[str(x) for x in range(1,12)]
    assert len(doc.xpath('./part[1]/measure/direction/direction-type/words'))==11
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
    monkeypatch.setattr(main,'audit_musicxml_against_mscz',lambda source,path: {})
    client=TestClient(main.app)
    homepage=client.get('/')
    assert homepage.status_code==200
    assert homepage.headers['cache-control']=='no-store'
    assert 'v1.7.8' in homepage.text
    assert 'start-over' in homepage.text
    assert 'Every score produces an editable result' in homepage.text
    assert client.get('/api/info').status_code==200
    app_js=client.get('/static/app.js')
    assert app_js.status_code==200
    assert app_js.headers['cache-control']=='no-store'
    assert 'background=1' in app_js.text
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


def test_background_batch_accepts_maximum_thirty_files(monkeypatch,tmp_path):
    from app import main
    monkeypatch.setattr(main,'JOBS',tmp_path/'jobs')

    def fake_process(name,ext,raw,work,layout,allow_unverified=False):
        stem=Path(name).stem
        mscz=work/f'{stem}.mscz'
        musicxml=work/f'{stem}.musicxml'
        pdf=work/f'{stem}.pdf'
        preview=work/f'{stem}.png'
        report=work/f'{stem}-report.json'
        with zipfile.ZipFile(mscz,'w') as archive:
            archive.writestr('score.mscx','<museScore version="3.02"><Score><Staff/></Score></museScore>')
        musicxml.write_text('<score-partwise/>')
        pdf.write_bytes(b'%PDF-1.4\n%test')
        preview.write_bytes(b'PNG')
        report.write_text('{}')
        return {'source_filename':name,'layout':layout,'warnings':[],
                'files':{'mscz':mscz.name,'musicxml':musicxml.name,
                         'pdf':pdf.name,'preview':preview.name,
                         'report':report.name},'parts':[],'measures':1}

    monkeypatch.setattr(main,'process_score',fake_process)
    data=capx(MINIMAL)
    files=[('files',(f'Score {index}.capx',data,'application/octet-stream'))
           for index in range(30)]
    with TestClient(main.app) as client:
        response=client.post('/api/convert?layout=balanced&background=1',files=files)
        assert response.status_code==202,response.text
        payload=response.json()
        assert payload['total']==30
        status=None
        for _ in range(200):
            status=client.get(payload['status_url'])
            assert status.status_code==200,status.text
            if status.json()['state'] in ('completed','failed'):break
        result=status.json()
        assert result['state']=='completed',result
        assert result['completed']==30
        assert len(result['results'])==30
        assert all(item['ok'] for item in result['results'])
        assert client.get(result['batch_url']).status_code==200


def test_structurally_uncertified_mscz_is_retained_for_manual_review(
        monkeypatch,tmp_path):
    from app import main
    source=tmp_path/'source.musicxml'
    source.write_text('<score-partwise version="4.0"/>',encoding='utf-8')
    target=tmp_path/'result.mscz'

    def fake_convert(_source,output):
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr(
                'score.mscx',
                '<museScore version="3.02"><Score><Staff id="1">'
                '<Measure><voice><Chord><durationType>quarter</durationType>'
                '</Chord></voice></Measure></Staff></Score></museScore>')

    monkeypatch.setattr(main,'convert',fake_convert)
    warnings=[]
    report={}
    status=main.save_validated_mscz(source,target,report,warnings)
    assert status=='manual-review'
    assert target.is_file()
    assert report['verification']=='manual-review'
    assert report['verification_diagnostics']
    assert any('structural measure issue' in warning for warning in warnings)


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


def test_multimeasure_first_ending_runs_through_backward_repeat():
    xml = b'''<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">
    <layout><staves><staffLayout description="Piccolo"/></staves></layout>
    <systems><system><staves><staff layout="Piccolo"><voices><voice><noteObjects>
    <timeSign time="2/4"/><barline type="repBegin"/>
    <chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="E5"/></heads>
      <drawObjects><drawObj><volta firstNumber="1"/></drawObj></drawObjects></chord>
    <chord><duration base="1/4"/><heads><head pitch="F5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="G5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="A5"/></heads></chord>
    <barline type="repEnd"/>
    <chord><duration base="1/4"/><heads><head pitch="B5"/></heads>
      <drawObjects><drawObj><volta firstNumber="2"/></drawObj></drawObjects></chord>
    <chord><duration base="1/4"/><heads><head pitch="C6"/></heads></chord>
    </noteObjects></voice></voices></staff></staves></system></systems></score>'''
    result,report=converter.serialize_musicxml(capx(xml))
    score=ET.fromstring(result)
    first_start=score.find('./part/measure[2]/barline[@location="left"]/ending')
    first_stop=score.find('./part/measure[3]/barline[@location="right"]/ending')
    assert (first_start.get('number'),first_start.get('type'))==('1','start')
    assert (first_stop.get('number'),first_stop.get('type'))==('1','stop')
    assert score.find('./part/measure[2]/barline[@location="right"]/ending') is None
    assert report['ending_ranges'][0]=={
        'start_measure':2,'end_measure':3,'number':'1'}


def test_short_final_measure_is_implicit_without_invented_tail_padding():
    xml = b'''<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">
    <layout><staves><staffLayout description="Piccolo"/></staves></layout>
    <systems><system><staves><staff layout="Piccolo"><voices><voice><noteObjects>
    <timeSign time="2/4"/>
    <chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>
    <chord><duration base="1/4"/><heads><head pitch="E5"/></heads></chord>
    </noteObjects></voice></voices></staff></staves></system></systems></score>'''
    result,_=converter.serialize_musicxml(capx(xml))
    measures=ET.fromstring(result).findall('./part/measure')
    assert len(measures)==2
    assert measures[0].get('implicit') is None
    assert measures[1].get('implicit')=='yes'


def test_only_balanced_matching_ties_are_exported():
    xml = MINIMAL.replace(
        b'<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>',
        b'<chord><duration base="1/8"/><heads><head pitch="C5"><tie begin="true"/></head></heads></chord>'
        b'<chord><duration base="1/8"/><heads><head pitch="C5"><tie end="true"/></head></heads></chord>',1
    ).replace(
        b'<chord><duration base="1/4"/><heads><head pitch="F5"><alter step="0"/></head></heads></chord>',
        b'<chord><duration base="1/8"/><heads><head pitch="D5"/></heads></chord>'
        b'<chord><duration base="1/8"/><heads><head pitch="D5"><tie end="true"/></head></heads></chord>',1
    )
    result,_=converter.serialize_musicxml(capx(xml))
    score=ET.fromstring(result)
    notes=score.findall('./part/measure/note')
    assert [tie.get('type') for tie in notes[0].findall('tie')]==['start']
    assert [tie.get('type') for tie in notes[1].findall('tie')]==['stop']
    assert notes[3].find('tie') is None


def test_balanced_start_only_ties_may_span_intervening_notes():
    xml = MINIMAL.replace(
        b'<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>',
        b'<chord><duration base="1/4"/><heads><head pitch="C5"><tie begin="true"/></head></heads></chord>',1
    ).replace(
        b'<chord><duration base="1/4"/><heads><head pitch="F5"><alter step="0"/></head></heads></chord>',
        b'<chord><duration base="1/4"/><heads><head pitch="F5"><alter step="0"/></head></heads></chord>'
        b'<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>',1
    )
    result,_=converter.serialize_musicxml(capx(xml))
    score=ET.fromstring(result)
    tied=score.xpath('./part/measure/note[tie]')
    assert [note.findtext('pitch/step') for note in tied]==['C']
    assert [note.find('tie').get('type') for note in tied]==['start']


def test_wedge_numbers_stay_in_musescore3_portable_range():
    notes=[]
    for index in range(14):
        wedge=(b'<drawObjects><drawObj><basic noteRange="1"/>'
               b'<wedge/></drawObj></drawObjects>' if index%2==0 else b'')
        notes.append(b'<chord><duration base="1/4"/>'+wedge+
                     b'<heads><head pitch="C5"/></heads></chord>')
    xml=(b'<?xml version="1.0"?><score xmlns="http://www.capella.de/CapXML/2.0">'
         b'<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
         b'<systems><system><staves><staff layout="Piccolo"><voices><voice><noteObjects>'
         b'<timeSign time="4/4"/>'+b''.join(notes)+
         b'</noteObjects></voice></voices></staff></staves></system></systems></score>')
    result,_=converter.serialize_musicxml(capx(xml))
    score=ET.fromstring(result)
    starts=score.findall('.//wedge[@type="crescendo"]')
    assert [int(wedge.get('number')) for wedge in starts]==[1,2,3,4,5,6,1]


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


def test_capella_graphic_variation_headings_all_modes():
    """Prominent source-system numbers survive, tiny tuplet labels do not."""
    systems=[]
    for index in range(3):
        staffs=[]
        for staff_no in range(1,5):
            marks=(
                '<drawObjects><drawObj><text y="-5">'
                '<font face="Lato" height="17" weight="700"/>'
                f'<content>{index+1}</content></text></drawObj>'
                '<drawObj><text y="-7"><font face="Times New Roman" height="8"/>'
                '<content>3</content></text></drawObj></drawObjects>'
            ) if staff_no==1 else ''
            staffs.append(
                f'<staff layout="P{staff_no}"><voices><voice><noteObjects>'
                + ('<timeSign time="2/4"/>' if index==0 else '') +
                '<chord><duration base="1/4"/>'
                + marks +
                '<heads><head pitch="C5"/></heads></chord>'
                '<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
                '</noteObjects></voice></voices></staff>'
            )
        systems.append('<system><staves>'+''.join(staffs)+'</staves></system>')
    source=capx((
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves>'+''.join(f'<staffLayout description="P{i}"/>' for i in range(1,5))+
        '</staves><brackets><bracket from="0" to="3"/></brackets></layout>'
        '<systems>'+''.join(systems)+'</systems></score>'
    ).encode())
    for layout in ('balanced','compact','original'):
        result,report=converter.serialize_musicxml(source,layout)
        score=ET.fromstring(result)
        headings=score.xpath('./part[1]/measure/direction/direction-type/words/text()')
        assert headings==['1','2','3'],(layout,headings)
        assert len(score.xpath('./part[position()>1]//direction'))==0
        assert report['variation_numbers']==['1','2','3']
        assert report['source_systems']==3
        assert score.xpath('./part-list/part-group[group-barline="yes"]')


def test_multi_part_native_barline_span_expansion(tmp_path):
    from app.engraving import prepare_print_ready
    output=tmp_path/'grouped.mscz'
    score='<museScore version="3.02"><Score>'
    score+=''.join(
        '<Part><Staff id="%s">%s</Staff></Part>'%(i,'<barLineSpan>4</barLineSpan>' if i==1 else '')
        for i in range(1,5)
    )
    score+='</Score></museScore>'
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('grouped.mscx',score)
    prepare_print_ready(output)
    prepare_print_ready(output)  # patch is idempotent
    with zipfile.ZipFile(output) as archive:
        doc=ET.fromstring(archive.read('grouped.mscx'))
        staves=doc.findall('./Score/Part/Staff')
        assert [x.findtext('barLineSpan') for x in staves]==['1','1','1',None]


def test_title_frame_credits_are_in_reading_order(tmp_path):
    from app.engraving import prepare_print_ready
    path=tmp_path/'credits.mscz'
    data=(
        '<museScore version="3.02"><Score><Staff id="1">'
        '<VBox><height>9.7</height>'
        '<Text><style>Title</style><text>Old title</text></Text>'
        '<Text><style>Subtitle</style><text>Old composer</text></Text>'
        '<Text><style>Subtitle</style><text>Old subtitle</text></Text>'
        '</VBox></Staff></Score></museScore>'
    )
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('score.mscx',data)
    prepare_print_ready(path,{'title':'Brandywine','subtitle':'Four flutes',
                              'composer':'Rene Brielmann'})
    with zipfile.ZipFile(path) as archive:
        doc=ET.fromstring(archive.read('score.mscx'))
    texts=doc.findall('./Score/Staff/VBox/Text')
    assert [el.findtext('text') for el in texts]==[
        'Brandywine','Four flutes','Rene Brielmann']
    assert [el.findtext('style') for el in texts]==['Title','Subtitle','Composer']
    assert float(doc.findtext('./Score/Staff/VBox/height'))>=14


def test_original_mode_avoids_pathological_pagination(tmp_path, monkeypatch):
    from app import main
    def fake_convert(source,target):
        target=Path(target)
        if target.suffix=='.mscz':
            with zipfile.ZipFile(target,'w') as archive:
                archive.writestr('score.mscx','<museScore version="3.02"><Score>'
                                 '<Staff id="1"><VBox><height>10</height></VBox>'
                                 '</Staff></Score></museScore>')
        elif target.suffix=='.pdf':
            target.write_bytes(b'%PDF-1.4\n%%EOF\n')
    monkeypatch.setattr(main,'convert',fake_convert)
    monkeypatch.setattr(main,'create_preview',
                        lambda pdf,png:Path(png).write_bytes(b'PNG'))
    monkeypatch.setattr(main,'audit_musicxml_against_mscz',lambda source,path: {})
    monkeypatch.setattr(main,'pdf_page_count',lambda path:40)
    data=capx(MINIMAL)
    work=tmp_path/'one';work.mkdir()
    info=main.process_score('Test.capx','.capx',data,work,'original')
    assert info['layout']=='balanced-fallback'
    assert any('used a balanced print layout' in warning for warning in info['warnings'])
    assert (work/'Test.mscz').exists()


def test_meter_changes_tuplets_grace_and_full_bar_rests_in_all_layouts():
    """Generic regression: multi-meter CAPX must have no phantom barlines."""
    first = (
        '<timeSign time="2/4"/>'
        '<chord><duration base="1/8"/>'
        '<drawObjects><drawObj><text y="-6"><font face="Lato" height="16"/>'
        '<content>No 1</content></text></drawObj></drawObjects>'
        '<heads><head pitch="C5"/></heads></chord>'
        '<chord><duration base="1/8"/><heads><head pitch="D5"/></heads></chord>'
        '<chord><duration base="1/4"/><heads><head pitch="E5"/></heads></chord>'
    )
    graces = ''.join(
        '<chord><duration base="1/32" noDuration="true"/>'
        '<heads><head pitch="F5"/></heads></chord>' for _ in range(3)
    )
    tuplets = ''.join(
        '<chord><duration base="1/8"><tuplet count="3"/></duration>'
        + ('<drawObjects><drawObj><text y="-6">'
           '<font face="Lato" height="16"/><content>No 2</content>'
           '</text></drawObj></drawObjects>' if i==0 else '') +
        '<heads><head pitch="G5"/></heads></chord>' for i in range(3)
    )
    second = (
        '<timeSign time="6/8"/><barline type="repEndBegin"/>'
        +graces+tuplets+
        '<chord><duration base="1/2"/><heads><head pitch="A5"/></heads></chord>'
    )
    def staff(name,objects):
        return ('<staff layout="'+name+'"><voices><voice><noteObjects>'
                +objects+'</noteObjects></voice></voices></staff>')
    source = capx((
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Upper"/>'
        '<staffLayout description="Lower"/></staves></layout>'
        '<systems><system><staves>'
        + staff('Upper',first)
        + staff('Lower','<timeSign time="2/4"/><rest><duration base="1"/>'
                          '<display churchStyle="true"/></rest>')
        + '</staves></system><system><staves>'
        + staff('Upper',second)
        + staff('Lower','<timeSign time="6/8"/><barline type="repEndBegin"/>'
                          '<rest><duration base="1"/><display churchStyle="true"/>'
                          '</rest>')
        + '</staves></system></systems></score>'
    ).encode())
    for layout in ('balanced','compact','original'):
        xml,report=converter.serialize_musicxml(source,layout)
        doc=ET.fromstring(xml)
        assert report['measures']==2, (layout,report)
        assert report['warnings']==[], (layout,report['warnings'])
        assert report['variation_numbers']==['No 1','No 2']
        upper,lower=doc.findall('./part')
        assert len(upper.findall('./measure'))==2
        assert len(lower.findall('./measure'))==2
        assert upper.findtext('./measure[2]/attributes/time/beats')=='6'
        assert upper.findtext('./measure[2]/attributes/time/beat-type')=='8'
        assert len(upper.findall('./measure[2]/note/grace'))==3
        assert not any(n.find('duration') is not None
                       for n in upper.findall('./measure[2]/note[grace]'))
        assert len(upper.findall('./measure[2]/note/time-modification'))==3
        assert [r.findtext('duration') for r in lower.findall('./measure/note')]==[
            '20160','30240']
        assert len(lower.findall('./measure/note/rest[@measure="yes"]'))==2
        assert len(upper.findall('./measure[1]/barline/repeat[@direction="backward"]'))==1
        assert len(upper.findall('./measure[2]/barline/repeat[@direction="forward"]'))==1
        assert [n.text for n in upper.findall(
            './measure/direction/direction-type/words')]==['No 1','No 2']


def test_three_to_two_tuplets_reduce_sounding_time():
    from fractions import Fraction
    root=ET.fromstring(
        '<chord xmlns="http://www.capella.de/CapXML/2.0">'
        '<duration base="1/8"><tuplet count="3"/></duration>'
        '<heads><head pitch="C5"/></heads></chord>'
    )
    assert converter.duration_of(root)==Fraction(1,12)
    source=ET.fromstring(
        '<chord xmlns="http://www.capella.de/CapXML/2.0">'
        '<duration base="1/32" noDuration="true"/>'
        '<heads><head pitch="C5"/></heads></chord>'
    )
    assert converter.duration_of(source)==0
