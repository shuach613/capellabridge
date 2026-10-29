"""Native MSCZ post-import formatting regressions (no private musical files)."""
import zipfile
from lxml import etree as ET
from app.engraving import prepare_print_ready
from app.legacy_cap import inspect_native_mscz


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
    assert changes['removed_staves']==[2]
    assert len(native.findall('./Score/Staff'))==1
    assert native.findtext('./Score/showInvisible')=='0'
    assert native.findtext('./Score/showUnprintable')=='0'


def test_track_addressed_rest_only_staff_is_hidden_without_renumbering(tmp_path):
    path=tmp_path/'track-addressed-rest.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    for staff_id in ('1','2'):
        part=ET.SubElement(score,'Part')
        ET.SubElement(part,'Staff',id=staff_id)
        staff=ET.SubElement(score,'Staff',id=staff_id)
        measure=ET.SubElement(staff,'Measure')
        voice=ET.SubElement(measure,'voice')
        event=ET.SubElement(voice,'Chord' if staff_id=='1' else 'Rest')
        ET.SubElement(event,'durationType').text='whole'
        if staff_id=='1':
            ET.SubElement(event,'Note')
    ET.SubElement(score,'track').text='4'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('score.mscx',ET.tostring(root))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['removed_staves']==[]
    assert changes['hidden_staves']==['2']
    assert [staff.get('id') for staff in native.findall('./Score/Staff')]==['1','2']
    assert native.findtext('./Score/Staff[@id="2"]/visible')=='0'
    assert native.findtext('./Score/Part/Staff[@id="2"]/visible')=='0'
    assert native.findtext('./Score/track')=='4'


def test_generated_guitar_chord_layer_is_hidden_but_kept_for_playback(tmp_path):
    path=tmp_path/'guitar-layer.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    part=ET.SubElement(score,'Part')
    ET.SubElement(part,'trackName').text='Gitarre Akkorde'
    ET.SubElement(part,'Staff',id='1')
    staff=ET.SubElement(score,'Staff',id='1')
    for label in ('D / D9','G / G7'):
        measure=ET.SubElement(staff,'Measure')
        voice=ET.SubElement(measure,'voice')
        text=ET.SubElement(voice,'StaffText')
        ET.SubElement(text,'text').text=label
        chord=ET.SubElement(voice,'Chord')
        ET.SubElement(chord,'durationType').text='whole'
        for pitch in ('50','54','57','62'):
            note=ET.SubElement(chord,'Note')
            ET.SubElement(note,'pitch').text=pitch
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('score.mscx',ET.tostring(root))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['hidden_note_layers']==['1']
    assert len(native.findall('.//Chord'))==2
    assert native.xpath('.//Chord/visible/text()')==['0','0']
    assert len(native.findall('.//Chord/Note'))==8
    assert native.xpath('.//Chord/Note/visible/text()')==['0']*8
    assert native.findtext('./Score/Staff[@id="1"]/visible')!='0'
    part_staff=native.find('./Score/Part/Staff[@id="1"]')
    assert part_staff.findtext('visible')!='0'
    assert part_staff.findtext('StaffType/lines')=='1'
    assert part_staff.findtext('StaffType/genClef')=='0'
    assert part_staff.findtext('StaffType/genKeysig')=='0'
    assert part_staff.findtext('StaffType/genTimesig')=='0'
    audit=inspect_native_mscz(path)
    assert audit['counts']['hidden_pitched_events']==0
    assert audit['counts']['intentional_hidden_playback_events']==8
    assert not any('hidden pitched event' in warning
                   for warning in audit['warnings'])


def test_rest_only_staff_with_voice_barline_is_pruned_and_transferred(tmp_path):
    path=tmp_path/'barline-empty-staff.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    for staff_id in ('1','2'):
        part=ET.SubElement(score,'Part')
        ET.SubElement(part,'Staff',id=staff_id)
    empty=ET.SubElement(score,'Staff',id='1')
    measure=ET.SubElement(empty,'Measure')
    voice=ET.SubElement(measure,'voice')
    rest=ET.SubElement(voice,'Rest')
    ET.SubElement(rest,'durationType').text='half'
    barline=ET.SubElement(voice,'BarLine')
    ET.SubElement(barline,'subtype').text='double'
    musical=ET.SubElement(score,'Staff',id='2')
    measure=ET.SubElement(musical,'Measure')
    voice=ET.SubElement(measure,'voice')
    chord=ET.SubElement(voice,'Chord')
    ET.SubElement(chord,'durationType').text='half'
    ET.SubElement(chord,'Note')
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('score.mscx',ET.tostring(root))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['removed_staves']==[1]
    assert len(native.findall('./Score/Staff'))==1
    assert native.findtext('.//BarLine/subtype')=='double'


def test_legacy_cap_prunes_empty_part_and_preserves_its_structure(tmp_path):
    path=tmp_path/'legacy-leading-rest.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    empty_part=ET.SubElement(score,'Part')
    empty_part_staff=ET.SubElement(empty_part,'Staff',id='1')
    bracket=ET.SubElement(empty_part_staff,'bracket',type='1')
    bracket.set('span','2')
    ET.SubElement(empty_part_staff,'barLineSpan').text='2'
    musical_part=ET.SubElement(score,'Part')
    ET.SubElement(musical_part,'Staff',id='2')
    empty=ET.SubElement(score,'Staff',id='1')
    frame=ET.SubElement(empty,'VBox')
    ET.SubElement(frame,'height').text='8'
    title=ET.SubElement(frame,'Text')
    ET.SubElement(title,'style').text='Title'
    ET.SubElement(title,'text').text='Transferred title'
    empty_measure=ET.SubElement(empty,'Measure')
    ET.SubElement(empty_measure,'startRepeat')
    empty_voice=ET.SubElement(empty_measure,'voice')
    time=ET.SubElement(empty_voice,'TimeSig')
    ET.SubElement(time,'sigN').text='2'
    ET.SubElement(time,'sigD').text='4'
    ET.SubElement(empty_voice,'Rest').append(ET.Element('durationType'))
    empty_voice.find('./Rest/durationType').text='half'
    musical=ET.SubElement(score,'Staff',id='2')
    musical_measure=ET.SubElement(musical,'Measure')
    musical_voice=ET.SubElement(musical_measure,'voice')
    chord=ET.SubElement(musical_voice,'Chord')
    ET.SubElement(chord,'durationType').text='half'
    ET.SubElement(chord,'Note')
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(root))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['removed_staves']==[1]
    assert [staff.get('id') for staff in native.findall('./Score/Staff')]==['1']
    assert [staff.get('id') for staff in native.findall('./Score/Part/Staff')]==['1']
    assert native.findtext('./Score/Staff/VBox/Text/text')=='Transferred title'
    assert native.find('./Score/Staff/Measure/startRepeat') is not None
    assert native.findtext('./Score/Staff/Measure/voice/TimeSig/sigN')=='2'


def test_legacy_cap_pruning_deduplicates_equivalent_meter_whitespace(tmp_path):
    """Pretty-print tails must not create a second visible time signature."""
    path=tmp_path/'legacy-duplicate-meter.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    empty=ET.SubElement(score,'Staff',id='1')
    empty_measure=ET.SubElement(empty,'Measure')
    empty_voice=ET.SubElement(empty_measure,'voice')
    empty_time=ET.SubElement(empty_voice,'TimeSig')
    ET.SubElement(empty_time,'sigN').text='2'
    ET.SubElement(empty_time,'sigD').text='4'
    rest=ET.SubElement(empty_voice,'Rest')
    ET.SubElement(rest,'durationType').text='half'
    musical=ET.SubElement(score,'Staff',id='2')
    musical_measure=ET.SubElement(musical,'Measure')
    musical_voice=ET.SubElement(musical_measure,'voice')
    musical_time=ET.SubElement(musical_voice,'TimeSig')
    ET.SubElement(musical_time,'sigN').text='2'
    ET.SubElement(musical_time,'sigD').text='4'
    chord=ET.SubElement(musical_voice,'Chord')
    ET.SubElement(chord,'durationType').text='half'
    ET.SubElement(chord,'Note')
    # Reparse formatted XML so indentation lives in element tails, matching
    # the real CAP importer output that exposed the regression.
    payload=ET.tostring(root,pretty_print=True)
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',payload)

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['removed_staves']==[1]
    assert changes['deduplicated_structure']==0
    assert len(native.findall('./Score/Staff/Measure/voice/TimeSig'))==1


def test_legacy_cap_removes_preexisting_exact_duplicate_meter(tmp_path):
    path=tmp_path/'legacy-existing-duplicate-meter.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    staff=ET.SubElement(score,'Staff',id='1')
    measure=ET.SubElement(staff,'Measure')
    voice=ET.SubElement(measure,'voice')
    for _ in range(2):
        time=ET.SubElement(voice,'TimeSig')
        ET.SubElement(time,'sigN').text='2'
        ET.SubElement(time,'sigD').text='4'
    chord=ET.SubElement(voice,'Chord')
    ET.SubElement(chord,'durationType').text='half'
    ET.SubElement(chord,'Note')
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(root,pretty_print=True))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['deduplicated_structure']==1
    assert len(native.findall('./Score/Staff/Measure/voice/TimeSig'))==1


def test_legacy_cap_keeps_rest_staff_with_textual_content(tmp_path):
    path=tmp_path/'legacy-rest-text.mscz'
    root=ET.Element('museScore')
    score=ET.SubElement(root,'Score')
    for staff_id in ('1','2'):
        staff=ET.SubElement(score,'Staff',id=staff_id)
        measure=ET.SubElement(staff,'Measure')
        voice=ET.SubElement(measure,'voice')
        rest=ET.SubElement(voice,'Rest')
        ET.SubElement(rest,'durationType').text='whole'
        if staff_id=='1':
            text=ET.SubElement(voice,'StaffText')
            ET.SubElement(text,'text').text='crescendo'

    with zipfile.ZipFile(path,'w') as z:
        z.writestr('score.mscx',ET.tostring(root))

    changes=prepare_print_ready(path,source_format='cap')
    native=reread(path)

    assert changes['removed_staves']==[2]
    assert native.find('.//StaffText[text="crescendo"]') is not None
