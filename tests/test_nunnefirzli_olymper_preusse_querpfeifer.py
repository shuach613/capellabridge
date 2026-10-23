"""Source-geometry regression tests for six paired Basel score references.

Musical note sequences are synthetic: the actual CAPX/CAP/PDF files are
not checked into GitHub. Real source geometry and short credits only.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml

def graphic(value, size=10, y=-8, x=0, face="Arial",
            italic=False):
    italic_attr=' italic="true"' if italic else ''
    return (f'<drawObj><text x="{x}" y="{y}">'
            f'<font face="{face}" height="{size}"{italic_attr}/>'
            f'<content>{value}</content></text></drawObj>')

def chord(draws='', note='C5'):
    return ('<chord><duration base="1/4"/>'
            + ('<drawObjects>'+draws+'</drawObjects>' if draws else '')
            +f'<heads><head pitch="{note}"/></heads></chord>')

def make_source(first, later=None, page='', meter='1/4', version='2.0', staves=1):
    body=''
    sections=[first]+([later] if later is not None else [])
    for xml in sections:
        joined=''
        for i in range(staves):
            name=f'Staff {i+1}'
            joined+=(
                f'<staff layout="{name}"><voices><voice><noteObjects>'
                f'<timeSign time="{meter}"/>{xml if i==0 else chord()*len(xml.split("<chord>")) if False else xml}'
                '</noteObjects></voice></voices></staff>'
            )
        body+='<system><staves>'+joined+'</staves></system>'
    layouts=''.join(f'<staffLayout description="Staff {i+1}"/>' for i in range(staves))
    score=(f'<score xmlns="http://www.capella.de/CapXML/{version}">'
           +page+'<layout><staves>'+layouts+'</staves></layout>'
           '<systems>'+body+'</systems></score>')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:z.writestr('score.xml',score)
    return buffer.getvalue()

def score(source):
    data,report=serialize_musicxml(source)
    return ET.fromstring(data),report

def test_nunnefirzli_title_includes_vom_author_and_section_text():
    head=(graphic('S Nunnefirzli, vom George Gruntz',25,-26.84375,
                  face='Myriad Web Pro Condensed')
          +graphic("S Original, aber uff 4 Syte, fir d'Wiehlmys",
                   12,-20.0625,face='Myriad Web Pro Condensed')
          +graphic('1 dr Aafang',10,-6.84375,
                   face='Myriad Web Pro Condensed'))
    root,r=score(make_source(chord(head)+chord(),meter='2/4',
                              version='1.0',staves=3))
    assert r['title']=='S Nunnefirzli, vom George Gruntz'
    assert r['composer']=='George Gruntz'
    assert "S Original, aber uff 4 Syte" in r['subtitle']
    assert '1 dr Aafang' in r['variation_numbers']
    assert root.findtext('./identification/creator[@type="composer"]')=='George Gruntz'
    print_words=[n.text for n in root.findall('./credit/credit-words')]
    assert print_words.count('George Gruntz')==0

def test_ohremyggeli_normal_page_author_and_rights_unchanged():
    page=('<pageObjects>'
          +graphic("s' Ohremyggeli",25,4.15625,42.5,'Myriad Condensed Web')
          +graphic('George Gruntz',11,2.03125,99.78125,'Myriad Condensed Web')
          +graphic('© Seibi-Clique',7,5.6875,100.5625)
          +'</pageObjects>')
    root,r=score(make_source(chord()+chord(),page=page,meter='2/4',staves=3))
    assert r['title']=="s' Ohremyggeli"
    assert r['composer']=='George Gruntz'
    assert root.findtext('./identification/rights')=='© Seibi-Clique'
    assert len(root.findall('./part'))==3

def test_olymper_note_attached_composer_trio_and_closing_dedication():
    page=('<pageObjects>'+graphic('(Olymper) Seite #/ 2',8,1.5625,148)
          +'</pageObjects>')
    first=chord(graphic('Intro',14,-7)
                 +graphic('OLYMPER',17,-10)
                 +graphic('H. Weber',12,-13)
                 +graphic('Basel, 7. Sept. 1932',11,-9.5))
    last=chord(graphic('Trio',14,-8)
               +graphic('Basel, den 18. September 1932',12,10.5)
               +graphic('Der "Olympia" zum 25. Geburtstage gewidmet von Max Höner',
                        12,10.5))
    root,r=score(make_source(first+chord(),last+chord(),page=page,
                             meter='2/4',staves=3))
    assert r['title']=='OLYMPER'
    assert r['composer']=='H. Weber'
    assert root.findtext('./identification/creator[@type="composer"]')=='H. Weber'
    assert 'Intro' in r['printed_directions']
    assert 'Trio' in r['printed_directions']
    end=[c.text for c in root.findall('./credit[@page="2"]/credit-words')]
    assert 'Basel, den 18. September 1932' in end
    assert 'Der "Olympia" zum 25. Geburtstage gewidmet von Max Höner' in end

def test_preusse_inline_metronome_and_two_digit_section_labels():
    notes=(
        chord(graphic('¢=100',18,-5.3125,face='capella3'))
        +chord(graphic('1',24,-5.3125,face='capella3')
               +graphic('Der Alte Dessauer-Marsch (90)',9,-7.5,
                        face='Bookman Old Style',italic=True))
        +chord(graphic('14',24,-5.3125,face='capella3')
               +graphic('Der Königsgrätzer',9,-7.5,
                        face='Bookman Old Style',italic=True)
               +graphic('¢=110',18,-4,face='capella3'))
    )
    root,r=score(make_source(notes,meter='1/4',version='3.0',staves=3))
    assert r['variation_numbers']==['1','14']
    assert 'Der Alte Dessauer-Marsch (90)' in r['printed_directions']
    assert 'Der Königsgrätzer' in r['printed_directions']
    bpm=[m.text for m in root.findall('.//direction-type/metronome/per-minute')]
    assert bpm==['100','110']
    assert len(root.findall('.//direction/sound[@tempo="100"]'))==1
    assert len(root.findall('.//direction/sound[@tempo="110"]'))==1

def test_querpfeifer_author_on_staff_and_last_page_revision():
    page=('<pageObjects>'+graphic('Querpfeifer',18,3.90625,55.4)
          +graphic('(Querpfeifer) Seite #/4',9,2.53125,102.9)
          +'</pageObjects>')
    first=chord(graphic('1',18,-8.25,face='Myriad Web Pro Condensed')
                +graphic('Karl Roth',10,-15,41.5625,'Myriad Web Pro Condensed'))
    last=chord(graphic('U. Ganz 2000 / F. Peter 2014',7,7.90625,
                       face='Myriad Web Pro Condensed'))
    root,r=score(make_source(first+chord(),last+chord(),page=page,meter='2/4'))
    assert r['title']=='Querpfeifer'
    assert r['composer']=='Karl Roth'
    assert '1' in r['variation_numbers']
    assert 'U. Ganz 2000 / F. Peter 2014' in [
        n.text for n in root.findall('./credit[@page="4"]/credit-words')]
