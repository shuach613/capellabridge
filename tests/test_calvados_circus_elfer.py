"""Synthetic CapXML regression cases from five October reference pairs.

No uploaded CAPX, CAP or PDF files are stored in the repository.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml

NS2='http://www.capella.de/CapXML/2.0'
NS3='http://www.capella.de/CapXML/3.0'


def textbox(label, font='Arial', height=12, x=0, y=-7, weight=None):
    extra=f' weight="{weight}"' if weight is not None else ''
    return (f'<drawObj><text x="{x}" y="{y}">'
            f'<font face="{font}" height="{height}"{extra}/>'
            f'<content>{label}</content></text></drawObj>')


def note(*graphics):
    drawings=('<drawObjects>'+''.join(graphics)+'</drawObjects>') if graphics else ''
    return ('<chord><duration base="1/4"/>'+drawings+
            '<heads><head pitch="C5"/></heads></chord>')


def fixture(notes, page='', ns=NS2, meter='4/4'):
    score=(f'<score xmlns="{ns}">{page}'
           '<layout><staves><staffLayout description="Piccolo"/></staves></layout>'
           '<systems><system><staves><staff layout="Piccolo"><voices>'
           f'<voice><noteObjects><timeSign time="{meter}"/>'
           +notes+'</noteObjects></voice></voices></staff></staves></system></systems>'
           '</score>')
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as archive:
        archive.writestr('score.xml',score.encode('utf-8'))
    return buf.getvalue()


def page_text(title,extra='',height=20):
    return ('<pageObjects>'
            +textbox(title,height=height,x=60,y=5)
            +extra+'</pageObjects>')


def test_circus_page_credit_is_arranger_not_composer():
    page=page_text('CIRCUS Märschli',
                   textbox('arrangiert vom Thomas Heid',height=11,x=130,y=5))
    xml,report=serialize_musicxml(fixture(note()*4,page=page),
                                  filename='Circus Märschli.capx')
    root=ET.fromstring(xml)
    assert report['title']=='CIRCUS Märschli'
    assert report['composer']==''
    assert 'arrangiert vom Thomas Heid' in report['subtitle']
    assert root.findtext('./identification/creator[@type="arranger"]')=='Thomas Heid'
    assert root.find('./identification/creator[@type="composer"]') is None


def test_circus_rehearsal_letter_prints_in_rectangle():
    rectangle=('<drawObj><rectangle x1="-4" y1="-9" x2="0" y2="-5"/>'
               '<basic noAdjust="true"/></drawObj>')
    boxed=note(rectangle,textbox('A',font='Myriad Web Pro Condensed',
                               height=12,y=-6,weight=700))
    xml,report=serialize_musicxml(fixture(boxed+note()*3))
    root=ET.fromstring(xml)
    assert report['variation_numbers']==['A']
    words=root.find('.//direction-type/words[.="A"]')
    assert words is not None and words.get('enclosure')=='rectangle'


def test_small_unboxed_letter_is_not_a_rehearsal_label():
    xml,report=serialize_musicxml(fixture(
        note(textbox('A',height=12,y=-6))+note()*3))
    assert report['variation_numbers']==[]


def test_elfer_page_title_but_note_attached_author_and_fourth_part():
    page=page_text('Elfer',height=16)
    graphics=(textbox('1',height=17,y=-7,weight=700)
              +textbox('Karl Roth 1911',height=10,y=-12)
              +textbox('4. Schtimm: Christoph Walliser',height=9,y=-11))
    xml,report=serialize_musicxml(fixture(note(graphics)+note()*3,page=page))
    root=ET.fromstring(xml)
    assert report['title']=='Elfer'
    assert report['composer']=='Karl Roth 1911'
    assert '4. Schtimm: Christoph Walliser' in report['subtitle']
    assert root.findtext('./identification/creator[@type="composer"]')=='Karl Roth 1911'
    assert report['variation_numbers']==['1']


def test_elfer_capxml3_circled_digits_have_sibling_ellipse():
    # CapXML 3.0 stores the ellipse BESIDE the text, not inside its drawObj.
    images=[]
    for i in range(1,5):
        ellipse=('<drawObj><ellipse x1="-5" y1="-10" x2="0" y2="-5"/>'
                 '<basic noAdjust="true"/></drawObj>')
        images.append(note(textbox(str(i),font='capella3',height=18,y=-8),
                           ellipse))
    # Every circled variation begins a new 1/4 measure in this fixture.
    xml,report=serialize_musicxml(fixture(''.join(images),ns=NS3,meter='1/4'))
    root=ET.fromstring(xml)
    assert report['variation_numbers']==['①','②','③','④']
    assert [n.text for n in root.findall('.//direction-type/words')]==[
        '①','②','③','④']


def test_elfer_capxml3_title_and_karl_roth_year_credit():
    # In this variant the pageObjects contain only page-number templates.
    page=page_text('(Elfer) Seite #',height=8)
    graphics=(textbox('Elfer',font='Bookman Old Style',height=16,y=-7)
              +textbox('Karl Roth 1911',height=9,y=-8)
              +textbox('4. Schtimm und Revision: Christoph Walliser',
                       height=9,y=-11))
    xml,report=serialize_musicxml(fixture(note(graphics)+note()*3,
                                           page=page,ns=NS3))
    root=ET.fromstring(xml)
    assert report['title']=='Elfer'
    assert report['composer']=='Karl Roth 1911'
    assert '4. Schtimm und Revision: Christoph Walliser' in report['subtitle']
    assert root.findtext('./identification/creator[@type="composer"]')=='Karl Roth 1911'
