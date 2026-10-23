"""Synthetic score metadata fixtures based on five newly supplied CAPX/PDF pairs.

The source scores and PDFs remain private. These reproduce the page
text geometry and source credits without committing anyone's music.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml

NS="http://www.capella.de/CapXML/3.0"


def label(value,x,y,height,face='Bookman Old Style'):
    return (f'<drawObj><text x="{x}" y="{y}"><font face="{face}" '
            f'height="{height}"/><content>{value}</content></text></drawObj>')


def capx(pagetext,staves=3,meter='2/4',version='3.0'):
    page='<pageObjects>'+pagetext+'</pageObjects>'
    staff_names=[f'P{i+1}' for i in range(staves)]
    layouts=''.join(f'<staffLayout description="{n}"/>' for n in staff_names)
    notes=('<chord><duration base="1/4"/>'
           '<heads><head pitch="C5"/></heads></chord>')*(
              3 if meter=='6/8' else 2)
    music=''.join(
        f'<staff layout="{n}"><voices><voice><noteObjects>'
        f'<timeSign time="{meter}"/>{notes}'
        '</noteObjects></voice></voices></staff>' for n in staff_names)
    score=(f'<score xmlns="http://www.capella.de/CapXML/{version}">'
           +page+'<layout><staves>'+layouts+'</staves></layout>'
           '<systems><system><staves>'+music+'</staves></system></systems></score>')
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('score.xml',score.encode('utf-8'))
    return buf.getvalue()


def exported(page,staves=3,meter='2/4',version='3.0'):
    xml,report=serialize_musicxml(capx(page,staves,meter,version))
    return ET.fromstring(xml),report


def test_hanswurscht_low_page_title_is_not_replaced_by_section_one():
    page=(
        label('(Dr Hanswurscht) Seite #/4',86.25,5.75,9)
        +label('Dr Hanswurscht',55,9.3125,28)
        +label('© Znynibigger',0,6.125,12)
        +label('René Brielmann 1982',96.46875,6.125,12)
    )
    root,report=exported(page)
    assert report['title']=='Dr Hanswurscht'
    assert report['composer']=='René Brielmann 1982'
    assert '© Znynibigger' not in report['subtitle']
    assert root.findtext('./identification/rights')=='© Znynibigger'
    assert root.findtext('./identification/creator[@type="composer"]')=='René Brielmann 1982'
    credits=[x.text for x in root.findall('./credit/credit-words')]
    assert '© Znynibigger' in credits


def test_mozart_romanze_full_printed_byline_not_a_composer_name():
    page=(
        label('19.10.2008 DWe',83.40625,1.9375,9)
        +label('Eine kleine Nachtmusik',49.9375,3.0625,16)
        +label('W. A. Mozart, arr. René Brielmann 1977',49.9375,6.03125,9)
    )
    root,report=exported(page,staves=4,meter='4/4')
    assert report['title']=='Eine kleine Nachtmusik'
    assert report['composer']=='W. A. Mozart'
    assert report['subtitle']=='W. A. Mozart, arr. René Brielmann 1977'
    assert root.findtext('./identification/creator[@type="arranger"]')=='René Brielmann'
    assert root.findtext('./identification/creator[@type="composer"]')=='W. A. Mozart'
    printed=[c.text for c in root.findall('./credit/credit-words')]
    assert printed.count('W. A. Mozart, arr. René Brielmann 1977')==1
    assert printed.count('W. A. Mozart')==0
    assert '19.10.2008 DWe' in printed


def test_mozart_rondo_movement_and_composer_not_confused():
    page=(
        label('Eine kleine Nachtmusik',50.0625,3.09375,16)
        +label('Rondo v. W.A. Mozart, René Brielmann, 26.10.1997',
               50,5.75,9)
        +label('25.10.2008 DWe',84.46875,1.9375,9)
    )
    root,report=exported(page,staves=4)
    assert report['title']=='Eine kleine Nachtmusik'
    assert report['composer']=='W.A. Mozart'
    assert report['subtitle']=='Rondo v. W.A. Mozart, René Brielmann, 26.10.1997'
    assert root.findtext('./identification/creator[@type="composer"]')=='W.A. Mozart'
    assert root.find('./identification/creator[@type="arranger"]') is None
    printed=[x.text for x in root.findall('./credit/credit-words')]
    assert printed.count('Rondo v. W.A. Mozart, René Brielmann, 26.10.1997')==1
    assert printed.count('W.A. Mozart')==0


def test_haebsli_credit_and_date_are_separate_from_subtitle():
    page=(
        label('Häbsli',50,3,16)
        +label('René Brielmann, 25.09.1996',49.96875,6.03125,9)
        +label('18.03.2008 DWe',84.375,1.96875,9)
    )
    root,report=exported(page,staves=3,meter='6/8')
    assert report['title']=='Häbsli'
    assert report['composer']=='René Brielmann, 25.09.1996'
    assert not report['subtitle']
    assert '18.03.2008 DWe' in [x.text for x in root.findall('./credit/credit-words')]
    assert len(root.findall('./part'))==3
    assert root.findtext('./part/measure/attributes/time/beats')=='6'


def test_laeggerli_page_title_and_duri_schmid_author():
    page=(
        label("(s'Läggerli) # / 4",100.46875,1.5,8,'Myriad Web Pro Condensed')
        +label('Duri Schmid',104.59375,3.59375,12,'Myriad Web Pro Condensed')
        +label('S Läggerli',56.03125,3.875,18,'Myriad Web Pro Condensed')
    )
    root,report=exported(page,staves=3,version='2.0')
    assert report['title']=='S Läggerli'
    assert report['composer']=='Duri Schmid'
    assert root.findtext('./identification/creator[@type="composer"]')=='Duri Schmid'
