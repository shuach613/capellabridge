"""Synthetic regressions for six paired source scores supplied in October.

No user-owned score bytes are committed; real source files are inspected
separately and full visual matching remains a manual validation step.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml

NS="http://www.capella.de/CapXML/4.0"


def text(s, size=11, x=0, y=-5, face="Arial"):
    return (f'<drawObj><text x="{x}" y="{y}"><font face="{face}" '
            f'height="{size}"/><content>{s}</content></text></drawObj>')


def chord(graphics="",pitch="C5"):
    return (f'<chord><duration base="1/4"/>'
            +(f'<drawObjects>{graphics}</drawObjects>' if graphics else '')
            +f'<heads><head pitch="{pitch}"/></heads></chord>')


def make_file(staves, page="", meter="4/4", version="4.0"):
    names=list(staves)
    layout="".join(f'<staffLayout description="{n}"/>' for n in names)
    staves_xml=""
    for name,notes in staves.items():
        staves_xml+=(
            f'<staff layout="{name}"><voices><voice><noteObjects>'
            f'<timeSign time="{meter}"/>{notes}'
            '</noteObjects></voice></voices></staff>'
        )
    score=(
        f'<score xmlns="http://www.capella.de/CapXML/{version}">'
        +page+'<layout><staves>'+layout+'</staves></layout>'
        '<systems><system><staves>'+staves_xml+'</staves></system></systems>'
        '</score>'
    ).encode('utf-8')
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:
        z.writestr('score.xml',score)
    return f.getvalue()


def test_capxml4_fuffzgerli_six_staves_and_12_8_source_metadata():
    page=('<pageObjects>'+text('Fuffzgerli',size=16,x=50,y=3)
          +text('Christoph Walliser, 12/96',size=12,x=50,y=6)
          +'</pageObjects>')
    # Six independent parts, same rhythmic length, single 12/8 measure.
    staves={name:chord()*6 for name in
            ['P1','P2','P3','P4','P5','Piccolo Solo']}
    xml,report=serialize_musicxml(make_file(staves,page,meter='12/8'),
                                  filename='Fuffzgerli 6-stimmig.capx')
    root=ET.fromstring(xml)
    assert report['title']=='Fuffzgerli'
    assert report['composer']=='Christoph Walliser, 12/96'
    assert report['capxml_version']=='4.0'
    assert len(report['parts'])==6
    assert len(root.findall('./part'))==6
    assert root.findtext('./part/measure/attributes/time/beats')=='12'
    assert root.findtext('./part/measure/attributes/time/beat-type')=='8'


def test_fyyrvogel_graphical_title_upload_suffix_and_split_drum_text_credit():
    page=('<pageObjects>'
          '<drawObj><metafile name="obj____1.wmf"/></drawObj>'
          +text('(Fyyrvogel) #/4',size=10,x=150,y=-1)
          +text('Michael Robertson&#10;Trommeltext: Andri Obrist',
                size=11,x=148,y=1)
          +text('Intro',size=12,x=18,y=11)
          +'</pageObjects>')
    xml,report=serialize_musicxml(make_file({'Piccolo 1':chord()*4},
                                             page,version='3.0'),
                                  filename='Fyyrvogel(1).capx')
    root=ET.fromstring(xml)
    assert report['title']=='Fyyrvogel'
    assert report['composer']=='Michael Robertson'
    assert 'Trommeltext: Andri Obrist' in report['subtitle']
    assert 'Intro' in report['printed_directions']
    assert root.findtext('./identification/creator[@type="composer"]')=='Michael Robertson'


def test_mozart_and_arranger_both_identified_from_page_multiline():
    page=('<pageObjects>'
          +text('Die glaini Nachtmuusig',size=16,x=50,y=3)
          +text('von W. A. Mozart&#10;für 4 Piccolo arr. R. Juen, 1980',
                size=9,x=50,y=6)
          +'</pageObjects>')
    xml,report=serialize_musicxml(make_file({'1.Piccolo':chord()*4},
                                             page,version='3.0'))
    root=ET.fromstring(xml)
    assert report['title']=='Die glaini Nachtmuusig'
    assert report['composer']=='W. A. Mozart'
    assert 'für 4 Piccolo arr. R. Juen, 1980' in report['subtitle']
    assert root.findtext('./identification/creator[@type="arranger"]')=='R. Juen'


def test_gross_basel_medley_preserves_subtitle_and_tune_caption():
    graphics=(
        text('GrossBaselExpress',size=16,y=-14)
        +text('Medley zur Weltausstellung 1967 in Montreal'
              '&#10;Aus E.F. Goldman; S.P. Friedman, A. Holzmann und '
              'J.P.Sousa-Melodien zusammengestellt von Urs Ganz'
              '&#10;überarbeitet für die Alten Kameraden von R. Schneider',
              size=9,y=-11)
        +text('The chimes of Liberty (Goldman)',size=9,y=-7)
    )
    second=text('Down the field (Friedman)',size=9,y=-8)
    staves={'1. Piccolo': chord(graphics)+chord(second)+chord()+chord()}
    xml,report=serialize_musicxml(make_file(staves,version='2.0',
                                             meter='1/4'))
    root=ET.fromstring(xml)
    assert report['title']=='GrossBaselExpress'
    assert report['composer']==''
    assert 'Medley zur Weltausstellung 1967 in Montreal' in report['subtitle']
    assert 'überarbeitet für die Alten Kameraden von R. Schneider' in report['subtitle']
    assert 'The chimes of Liberty (Goldman)' in report['printed_directions']
    assert 'Down the field (Friedman)' in report['printed_directions']
    nodes=root.findall('.//direction-type/words')
    tune=[n for n in nodes if n.text=='The chimes of Liberty (Goldman)']
    assert len(tune)==1
    assert tune[0].get('font-style')=='italic'
    assert tune[0].get('color')=='#008000'


def test_glaibasler_two_scores_keep_separate_instrument_layouts():
    title='<pageObjects>'+text('Glaibasler Luft',16,50,3)+text(
        'René Brielmann, 16.05.1997',9,50,6)+'</pageObjects>'
    four={f'{n}.Piccolo':chord()*6 for n in range(1,5)}
    xml,report=serialize_musicxml(make_file(four,title,
                                             meter='6/8',version='3.0'))
    assert len(report['parts'])==4
    assert report['composer']=='René Brielmann, 16.05.1997'
    title_piano='<pageObjects>'+text('Glaibasler Luft (mit Klavier)',16,50,3)+text(
        'René Brielmann, 16.05.1997',9,50,6)+'</pageObjects>'
    with_piano={'Klavier':chord()*6,'Klavier1':chord()*6,**four}
    xml,report=serialize_musicxml(make_file(with_piano,title_piano,
                                             meter='6/8',version='3.0'))
    assert len(report['parts'])==6
    assert report['title']=='Glaibasler Luft (mit Klavier)'


def test_fyyrvogel_grouped_quarter_metronome_is_printed_with_playback_tempo():
    group=('<drawObj><group>'
           '<drawObj><text y="-7"><font face="capella3" height="18"/>'
           '<content>¢</content></text></drawObj>'
           '<drawObj><text y="-7"><font face="Times New Roman" height="10"/>'
           '<content>=95</content></text></drawObj>'
           '</group></drawObj>')
    xml,report=serialize_musicxml(make_file({'Piccolo 1':chord(group)+chord()*3},
                                            version='3.0'))
    root=ET.fromstring(xml)
    assert root.findtext('.//metronome/beat-unit')=='quarter'
    assert root.findtext('.//metronome/per-minute')=='95'
    assert root.find('.//direction/sound[@tempo="95"]') is not None


def test_piano_playback_tempo_does_not_invent_printed_metronome():
    first='<chord tempo="180"><duration base="1/4"/>' \
          '<heads><head pitch="C5"/></heads></chord>'
    xml,report=serialize_musicxml(make_file(
        {'Klavier':first+chord()*3},version='3.0'))
    root=ET.fromstring(xml)
    assert root.find('./part/measure/sound[@tempo="180"]') is not None
    assert root.find('.//metronome') is None
    assert root.find('.//direction-type/words[.="180"]') is None


def test_dynamic_on_later_note_does_not_move_to_beginning_of_measure():
    second=chord(text('p',font='capella3',size=18,y=4.5))
    xml,report=serialize_musicxml(make_file(
        {'Piccolo 1':chord()+second+chord()*2},version='3.0'))
    measure=ET.fromstring(xml).find('./part/measure')
    tags=[ET.QName(c).localname for c in measure]
    assert 'direction' in tags
    assert tags.index('direction')>tags.index('note')
    assert measure.find('.//direction-type/dynamics/p') is not None
