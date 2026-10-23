"""Synthetic source-shaped regression tests for three paired CAPX/PDF scores.

The original scores remain private; these small fixtures reproduce just the
actual metadata, notation types and page-text geometry needed for validation.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml


def label(value, height=12, x=0, y=-7, font='Arial'):
    return (
        f'<drawObj><text x="{x}" y="{y}"><font face="{font}" '
        f'height="{height}"/><content>{value}</content></text></drawObj>'
    )


def chord(markings='', pitch='C5'):
    return ('<chord><duration base="1/4"/>'
            + ('<drawObjects>'+markings+'</drawObjects>' if markings else '')
            + f'<heads><head pitch="{pitch}"/></heads></chord>')


def source(pages, first='', last=None, staves=3, version='2.0', meter='2/4'):
    layouts=''.join(f'<staffLayout description="P{i+1}"/>' for i in range(staves))

    def system(notes):
        staff_tags=[]
        for i in range(staves):
            part_notes=notes if i==0 else chord()*2
            staff_tags.append(
                f'<staff layout="P{i+1}"><voices><voice><noteObjects>'
                f'<timeSign time="{meter}"/>{part_notes}'
                '</noteObjects></voice></voices></staff>')
        return '<system><staves>'+''.join(staff_tags)+'</staves></system>'

    xml=(f'<score xmlns="http://www.capella.de/CapXML/{version}">'
         '<pageObjects>'+pages+'</pageObjects>'
         '<layout><staves>'+layouts+'</staves></layout>'
         '<systems>'+system(first or chord()*2)
         +(system(last) if last is not None else '')+'</systems></score>')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('score.xml',xml.encode('utf-8'))
    return out.getvalue()


def converted(raw):
    document,report=serialize_musicxml(raw)
    return ET.fromstring(document),report


def test_rossignol_original_page_credit_and_revision_on_page_four():
    page=(label('(Rossignol) Seite #/4',8,104.125,1.65625)
          +label('Rossignol',18,55.6875,3.125)
          +label('Hans Schneider',11,97.125,3.625))
    first=chord(label('1',18,-2.9375,-7.25))+chord()
    last=chord(label('U. Ganz 2000, F. Peter 2014',
                     8,-10.65625,11))+chord()
    doc,report=converted(source(page,first,last,version='1.0'))
    assert report['title']=='Rossignol'
    assert report['composer']=='Hans Schneider'
    assert len(report['parts'])==3
    assert '1' in report['variation_numbers']
    assert doc.findtext('./identification/creator[@type="composer"]')=='Hans Schneider'
    assert 'U. Ganz 2000, F. Peter 2014' in [
        c.text for c in doc.findall('./credit[@page="4"]/credit-words')]


def test_sperber_preserves_entire_dedication_but_composer_is_name_only():
    byline='René Brielmann, 22.06.1983, für die Musketiere'
    page=(label('Sperber',16,49.90625,3,'Bookman Old Style')
          +label(byline,9,49.96875,6.0625,'Bookman Old Style')
          +label('06.09.2008 DWe',9,84.4375,1.96875))
    first=chord(label('1',22,-2.6875,-4.9375,'capella3'))+chord()
    doc,report=converted(source(page,first,staves=4,version='3.0'))
    assert report['title']=='Sperber'
    assert report['composer']=='René Brielmann'
    assert report['subtitle']==byline
    assert len(report['parts'])==4
    assert '1' in report['variation_numbers']
    assert doc.findtext('./identification/creator[@type="composer"]')=='René Brielmann'
    words=[e.text for e in doc.findall('./credit/credit-words')]
    assert words.count(byline)==1
    assert words.count('René Brielmann')==0
    assert '06.09.2008 DWe' in words


def test_tango_keeps_original_composer_3rd_section_and_revision():
    page=(label('(Tango) Seite # / 4',8,104.96875,1.5)
          +label('Tango',18,55.65625,4.125)
          +label('Karl Roth',12,106.9375,4.28125))
    first=chord(label('1',18,-4.125,-8.3125)
                +label('t',18,-1,-4,'capella3'))+chord()
    last=chord(label('3',18,-4.125,-8.3125)
               +label('U. Ganz 2000 / F. Peter 2007',8,-12.625,17.5)
               +label('t',18,0,-3.5,'capella3'))+chord()
    doc,report=converted(source(page,first,last,staves=4,version='2.0'))
    assert report['title']=='Tango'
    assert report['composer']=='Karl Roth'
    assert '1' in report['variation_numbers']
    assert '3' in report['variation_numbers']
    assert len(report['parts'])==4
    assert 'U. Ganz 2000 / F. Peter 2007' in [
        c.text for c in doc.findall('./credit[@page="4"]/credit-words')]
    assert len(doc.findall('.//notations/ornaments/trill-mark')) >= 2
