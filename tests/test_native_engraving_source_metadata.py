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


def test_graphical_rests_use_native_symbols_not_font_dependent_text(tmp_path):
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
    names=[n.text for n in native.findall('.//Symbol/name')]
    assert names==['rest8th','rest16th']
    prepare_print_ready(path, {'variable_staff_systems':False})
    assert [n.text for n in reread(path).findall('.//Symbol/name')] == names


def test_legacy_cap_text_chords_and_rest_only_staff_are_normalized(tmp_path):
    path=tmp_path/'legacy-cap.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    staff=ET.SubElement(score,'Staff',id='1')
    frame=ET.SubElement(staff,'VBox')
    ET.SubElement(frame,'height').text='8'
    measure=ET.SubElement(staff,'Measure')
    voice=ET.SubElement(measure,'voice')
    opening=ET.SubElement(voice,'StaffText')
    ET.SubElement(opening,'autoplace').text='0'
    ET.SubElement(opening,'text').text='A'
    title=ET.SubElement(voice,'StaffText')
    ET.SubElement(title,'autoplace').text='0'
    ET.SubElement(title,'text').text='S Äffli'
    chord=ET.SubElement(voice,'Chord')
    lyric=ET.SubElement(chord,'Lyrics')
    ET.SubElement(lyric,'text').text='Hm'
    ET.SubElement(chord,'durationType').text='whole'
    lyric_chord=ET.SubElement(voice,'Chord')
    lyric=ET.SubElement(lyric_chord,'Lyrics')
    ET.SubElement(lyric,'text').text='Alle'
    ET.SubElement(lyric_chord,'durationType').text='whole'
    rest_staff=ET.SubElement(score,'Staff',id='2')
    rest_measure=ET.SubElement(rest_staff,'Measure')
    rest_voice=ET.SubElement(rest_measure,'voice')
    rest=ET.SubElement(rest_voice,'Rest')
    ET.SubElement(rest,'durationType').text='whole'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(root))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['chord_annotations']==1
    assert native.find('.//Harmony/root').text=='19'
    assert native.find('.//Harmony/name').text=='m'
    assert native.find('.//Lyrics/text').text=='Alle'
    assert native.find('./Score/Staff[1]/VBox/Text[style="Title"]/text').text=='S Äffli'
    assert native.find('./Score/Staff[1]/Measure/voice/StaffText[text="A"]/autoplace').text=='1'
    assert native.find('./Score/Staff[2]/visible').text=='0'
