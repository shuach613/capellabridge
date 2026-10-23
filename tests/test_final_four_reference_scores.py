"""Last four user references: synthetic source-geometry regressions.

These fixtures intentionally contain no uploaded music; each tests a concrete
Capella representation verified in the private source and PDF pairs.
"""
import io
import zipfile
from lxml import etree as ET
from app.converter import serialize_musicxml


def draw(value,height=10,x=0,y=-7,face='Myriad Web Pro Condensed',
         color='000080',italic=False):
    emphasis=' italic="true"' if italic else ''
    return (f'<drawObj><text x="{x}" y="{y}">'
            f'<font face="{face}" height="{height}" color="{color}"{emphasis}/>'
            f'<content>{value}</content></text></drawObj>')


def chord(*graphics,pitch='C5'):
    graphics=('<drawObjects>'+''.join(graphics)+'</drawObjects>') if graphics else ''
    return ('<chord><duration base="1/4"/>'+graphics+
            f'<heads><head pitch="{pitch}"/></heads></chord>')


def score(page, lead=None, last=None, count=3, version='2.0', meter='2/4'):
    staves=''.join(f'<staffLayout description="P{i+1}"/>' for i in range(count))
    def system(primary):
        return ('<system><staves>'+
                ''.join(f'<staff layout="P{i+1}"><voices><voice><noteObjects>'
                        f'<timeSign time="{meter}"/>'+
                        (primary if i==0 else chord()+chord())+
                        '</noteObjects></voice></voices></staff>'
                        for i in range(count))+
                '</staves></system>')
    xml=(f'<score xmlns="http://www.capella.de/CapXML/{version}">'
         '<pageObjects>'+page+'</pageObjects>'
         '<layout><staves>'+staves+'</staves></layout>'
         '<systems>'+system(lead or chord()+chord())+
         (system(last) if last is not None else '')+
         '</systems></score>')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('score.xml',xml.encode())
    return out.getvalue()


def output(capx):
    xml, report=serialize_musicxml(capx)
    return ET.fromstring(xml),report


def test_uncle_sam_parenthesized_arranger_not_composer_and_tune_titles():
    page=(draw('(Uncle Sam) Seite # / 4',8,96.3,1.75)
          +draw('Uncle Sam',18,47.9,4.156)
          +draw('(arr. R. Brielmann 1988)',12,88.4,4.281))
    lead=(chord(draw('Intro (R. Brielmann)',9,0,-7,italic=True),
                draw('Maestoso',10,0,-5,color='FF0000'))
          +chord(draw('Battle Hymn of the Republic (trad.)',
                      9,0,-7,face='Arial',italic=True)))
    last=(chord(draw('Lively',10,0,-5,color='FF0000'))+
          chord(draw('Marcial',10,0,-5,color='FF0000'),
                draw('U. Ganz 1997 / F. Peter 2014',8,0,6.2)))
    xml,report=output(score(page,lead,last,count=3))
    assert report['title']=='Uncle Sam'
    assert report['composer']==''
    assert report['subtitle']=='(arr. R. Brielmann 1988)'
    assert xml.findtext('./identification/creator[@type="arranger"]')=='R. Brielmann'
    assert xml.find('./identification/creator[@type="composer"]') is None
    text=report['printed_directions']
    for expected in ('Intro (R. Brielmann)','Maestoso','Lively','Marcial',
                     'Battle Hymn of the Republic (trad.)'):
        assert expected in text, expected
    assert 'U. Ganz 1997 / F. Peter 2014' in [
        x.text for x in xml.findall('./credit[@page="4"]/credit-words')]


def test_vaudois_red_page_level_da_capo_and_invisible_white_hint():
    page=(draw('(Vaudois) Seite # / 2',8,104.9,2.6875)
          +draw('Der "Vaudois"',18,55.75,4.15625)
          +draw('Gustave Doret 1922',10,102.7,2.781)
          +draw('d.c.al fine',12,102.03,123.09,color='FF0000'))
    lead=(chord(draw('fine',12,-1,-6,color='FF0000'))+chord())
    last=(chord()+chord(
        draw('d. c. al fine con rep',12,-13,-9,color='FFFFFF'),
        draw('U. Ganz 2000 / F. Peter 2018',7,-14,7.875)))
    xml,report=output(score(page,lead,last,count=3))
    assert report['title']=='Der "Vaudois"'
    assert report['composer']=='Gustave Doret 1922'
    assert report['printed_directions'].count('d.c.al fine')==1
    assert 'd. c. al fine con rep' not in report['printed_directions']
    found=xml.xpath('.//direction[sound[@dacapo="yes"]]')
    dc=found[0] if found else None
    assert dc is not None
    words=dc.find('./direction-type/words')
    assert words is not None and words.text=='d.c.al fine'
    assert words.get('color')=='#FF0000'
    assert xml.find('.//direction/sound[@fine="yes"]') is not None
    assert 'U. Ganz 2000 / F. Peter 2018' in [
        x.text for x in xml.findall('./credit[@page="2"]/credit-words')]


def test_waggis_composer_copyright_and_numbered_section():
    page=(draw('(Waggis) Seite #/ 3',8,98.625,1.375)
          +draw('Waggis',18,55.5625,3.625)
          +draw('Karl Scherrer',12,104.21875,3.125)
          +draw('© Märtplatz-Clique',7,99.81,4.875))
    lead=chord(draw('1',18,-4.9,-6.75))+chord()
    last=chord()+chord(draw('U. Ganz 2000 / F. Peter 2011',7,-10.4,4.7))
    xml,report=output(score(page,lead,last))
    assert report['title']=='Waggis'
    assert report['composer']=='Karl Scherrer'
    assert report['variation_numbers']==['1']
    assert xml.findtext('./identification/rights')=='© Märtplatz-Clique'
    assert 'U. Ganz 2000 / F. Peter 2011' in [
        x.text for x in xml.findall('./credit[@page="3"]/credit-words')]


def test_z_basel_4th_voice_byline_and_six_point_reviser():
    page=(draw("(z'Basel) Seite # / 3",8,98.75,1.75)
          +draw("z'Basel am mym Rhy",18,39.47,3.5)
          +draw('Karl Roth',12,106,3.375)
          +draw('4. Schtimm René Brielmann',10,89.84375,5.875))
    lead=chord(draw('1',18,-2.7,-7.18))+chord()
    last=chord()+chord(draw('U. Ganz 2000 / F. Peter 2014',
                            6,-12.875,4.75,face='Arial'))
    xml,report=output(score(page,lead,last,count=4))
    assert report['title']=="z'Basel am mym Rhy"
    assert report['composer']=='Karl Roth'
    assert '4. Schtimm René Brielmann' in report['subtitle']
    assert report['variation_numbers']==['1']
    assert 'U. Ganz 2000 / F. Peter 2014' in [
        x.text for x in xml.findall('./credit[@page="3"]/credit-words')]


def test_uncle_sam_embedded_rtf_navigation_is_not_silently_lost():
    page=draw('Uncle Sam',18,47,4.15)
    attach=('<drawObj><richText file="obj____1.rtf" x="-5.5" y="-14" '
            'width="22.21875"/></drawObj>')
    original=score(page,lead=chord()+chord(),last=chord()+chord(attach))
    rtf=(r'{\rtf1\ansi{\fonttbl{\f0 capella3;}{\f1 Arial;}}'
         r'\pard\f0 e\f1  al \f0 $\f1  con rep. et poi Coda\par}')
    file=io.BytesIO()
    with zipfile.ZipFile(original) as previous, zipfile.ZipFile(file,'w') as updated:
        updated.writestr('score.xml',previous.read('score.xml'))
        updated.writestr('obj____1.rtf',rtf.encode('cp1252'))
    xml,report=output(file.getvalue())
    caption='D.S. al Segno con rep. et poi Coda'
    assert caption in report['printed_directions']
    directions=xml.xpath('.//direction[sound[@dalsegno="segno"]]')
    assert len(directions)==1
    assert directions[0].find('./sound').get('tocoda')=='coda'
    words=directions[0].find('./direction-type/words')
    assert words.text==caption and words.get('color')=='#FF0000'
