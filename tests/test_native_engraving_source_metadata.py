"""Native MSCZ post-import formatting regressions (no private musical files)."""
import zipfile
from lxml import etree as ET
from app.engraving import prepare_print_ready


def make_mscz(path):
    score=ET.Element('museScore')
    body=ET.SubElement(score,'Score')
    ET.SubElement(body,'Style')
    part=ET.SubElement(body,'Part')
    ET.SubElement(part,'Staff',id='1')
    instrument=ET.SubElement(part,'Instrument')
    ET.SubElement(instrument,'shortName').text='P1'
    staff=ET.SubElement(body,'Staff',id='1')
    frame=ET.SubElement(staff,'VBox')
    title=ET.SubElement(frame,'Text')
    ET.SubElement(title,'style').text='Title'
    ET.SubElement(title,'text').text='S Nunnefirzli, vom George Gruntz'
    composer=ET.SubElement(frame,'Text')
    ET.SubElement(composer,'style').text='Composer'
    ET.SubElement(composer,'text').text='George Gruntz'
    measure=ET.SubElement(staff,'Measure')
    voice=ET.SubElement(measure,'voice')
    musical=ET.SubElement(voice,'StaffText')
    ET.SubElement(musical,'text').text='1 Solo'
    rest=ET.SubElement(voice,'Rest')
    ET.SubElement(rest,'durationType').text='quarter'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(score,encoding='utf-8'))


def reread(path):
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read('score.mscx'))


def test_original_colors_credits_and_no_fake_abbreviations(tmp_path):
    path=tmp_path/'sample.mscz'
    make_mscz(path)
    metadata={
        'title':'S Nunnefirzli, vom George Gruntz',
        'subtitle':'',
        'composer':'George Gruntz',
        'source_text_colors':{'1 Solo':'#0000FF',
                               'S Nunnefirzli, vom George Gruntz':'#000080'},
        'closing_credits':['FPe 2012 / HH 2020'],
        'variable_staff_systems':True
    }
    prepare_print_ready(path,metadata)
    root=reread(path)
    assert root.findtext('./Score/Style/hideEmptyStaves')=='1'
    assert root.findtext('./Score/Style/dontHideStavesInFirstSystem')=='0'
    assert root.findtext('./Score/Style/createMultiMeasureRests')=='1'
    assert root.findtext('./Score/Style/showFooter')=='0'
    assert root.findtext('./Score/Part/Instrument/shortName') in ('',None)
    titles=root.xpath('./Score/Staff/VBox/Text[style="Composer"]')
    assert not titles
    annotation=root.find('.//StaffText[text="1 Solo"]')
    assert annotation is not None
    color=annotation.find('color')
    assert color is not None
    assert (color.get('r'),color.get('g'),color.get('b'))==('0','0','255')
    editorial=root.find('.//StaffText[text="FPe 2012 / HH 2020"]')
    assert editorial is not None
    # The importer saves a canonical MSCZ twice. Postprocessing is idempotent.
    prepare_print_ready(path,metadata)
    root=reread(path)
    assert len(root.xpath('.//StaffText[text="FPe 2012 / HH 2020"]'))==1


def test_nonvariable_three_part_score_does_not_hide_resting_staffs(tmp_path):
    path=tmp_path/'regular.mscz'
    make_mscz(path)
    prepare_print_ready(path,{'variable_staff_systems':False})
    root=reread(path)
    assert root.findtext('./Score/Style/hideEmptyStaves')=='0'


def test_repeated_annotations_deduplicated_only_inside_source_multirests(tmp_path):
    path=tmp_path/'drum.mscz'
    make_mscz(path)
    root=reread(path)
    staff=root.find('./Score/Staff')
    for _ in range(5):
        measure=ET.SubElement(staff,'Measure')
        ET.SubElement(measure,'voice')
    for mi in (0,0,1,3,3,4):
        voice=staff.findall('Measure')[mi].find('voice')
        stafftext=ET.SubElement(voice,'StaffText')
        ET.SubElement(stafftext,'text').text='Trommelsolo'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(root,encoding='utf-8'))
    metadata={'multi_rest_labels':[
        {'label':'Trommelsolo','start_measure':0,'measures':3},
        {'label':'Trommelsolo','start_measure':3,'measures':3}]}
    prepare_print_ready(path,metadata)
    root=reread(path)
    labels=root.xpath('.//StaffText/text[text()="Trommelsolo"]')
    assert len(labels)==2,len(labels)
    prepare_print_ready(path,metadata)
    assert len(reread(path).xpath('.//StaffText/text[text()="Trommelsolo"]'))==2


def test_graphical_rests_use_native_smufl_markup_not_missing_font(tmp_path):
    path=tmp_path/'graphical-rests.mscz'
    make_mscz(path)
    with zipfile.ZipFile(path) as z:
        root=ET.fromstring(z.read('score.mscx'))
    voice=root.find('./Score/Staff/Measure/voice')
    for ch in ('\U0001D13E','\U0001D13F'):
        item=ET.SubElement(voice,'StaffText')
        ET.SubElement(item,'text').text=ch
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(root))
    prepare_print_ready(path, {'variable_staff_systems':False})
    native=reread(path)
    symbols=[t.text for t in native.findall('.//StaffText/text')]
    assert '<sym>rest8th</sym>' in symbols
    assert '<sym>rest16th</sym>' in symbols
    prepare_print_ready(path, {'variable_staff_systems':False})
    assert [t.text for t in reread(path).findall('.//StaffText/text')] == symbols
