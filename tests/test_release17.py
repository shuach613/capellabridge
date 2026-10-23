"""Regression tests: repeat junctions, attached ornaments and rich titles."""
import io
import zipfile
from lxml import etree as ET

from app.converter import serialize_musicxml


def as_capx(xml):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('score.xml',xml.encode('utf-8'))
    return buffer.getvalue()


def test_backward_and_forward_repeats_at_same_timestamp_survive():
    """A repEnd at the previous system and a repBegin at the next are BOTH required."""
    one='<chord><duration base="1/4"/><heads><head pitch="C5"/></heads></chord>'
    two='<chord><duration base="1/4"/><heads><head pitch="D5"/></heads></chord>'
    def system(prefix,suffix):
        return ('<system><staves><staff layout="Flute"><voices><voice><noteObjects>'
                +prefix+one+two+suffix+'</noteObjects></voice></voices></staff></staves></system>')
    xml=('<score xmlns="http://www.capella.de/CapXML/2.0"><layout><staves>'
         '<staffLayout description="Flute"/></staves></layout><systems>'
         +system('<timeSign time="2/4"/>','<barline type="repEnd"/>')
         +system('<barline type="repBegin"/>','<barline type="repEndBegin"/>')
         +system('','')
         +'</systems></score>')
    music,report=serialize_musicxml(as_capx(xml))
    doc=ET.fromstring(music)
    assert report['measures']==3
    assert report['repeat_boundaries']=={'forward':2,'backward':2}
    measures=doc.findall('./part/measure')
    assert measures[0].find('./barline[@location="right"]/repeat[@direction="backward"]') is not None
    assert measures[1].find('./barline[@location="left"]/repeat[@direction="forward"]') is not None
    assert measures[1].find('./barline[@location="right"]/repeat[@direction="backward"]') is not None
    assert measures[2].find('./barline[@location="left"]/repeat[@direction="forward"]') is not None


def test_capella_ornaments_staccato_marcato_glissando_and_trill():
    notes=(
       '<chord><duration base="1/4"/><articulation type="strongBeat"/>'
       '<drawObjects><drawObj name="Glissando">'
       '<line x1="0" y1="0" x2="1" y2="1"/>'
       '<basic noteRange="1"/></drawObj></drawObjects>'
       '<heads><head pitch="C5"/></heads></chord>'
       '<chord><duration base="1/4"/><articulation type="staccato"/>'
       '<drawObjects><drawObj><trill/></drawObj></drawObjects>'
       '<heads><head pitch="D5"/></heads></chord>'
    )
    xml=('<score xmlns="http://www.capella.de/CapXML/2.0"><layout><staves>'
         '<staffLayout description="Flute"/></staves></layout><systems>'
         '<system><staves><staff layout="Flute"><voices><voice><noteObjects>'
         '<timeSign time="2/4"/>'+notes+
         '</noteObjects></voice></voices></staff></staves></system>'
         '</systems></score>')
    music,report=serialize_musicxml(as_capx(xml))
    doc=ET.fromstring(music)
    first,second=doc.findall('./part/measure/note')
    assert first.find('.//articulations/strong-accent') is not None
    assert second.find('.//articulations/staccato') is not None
    assert first.find('.//glissando[@type="start"]') is not None
    assert second.find('.//glissando[@type="stop"]') is not None
    assert second.find('.//ornaments/trill-mark') is not None


def test_triplet_group_is_explicitly_engraved():
    triplets=''.join(
        '<chord><duration base="1/8"><tuplet count="3"/></duration>'
        '<heads><head pitch="C5"/></heads></chord>' for _ in range(3))
    xml=('<score xmlns="http://www.capella.de/CapXML/2.0"><layout><staves>'
         '<staffLayout description="Flute"/></staves></layout><systems>'
         '<system><staves><staff layout="Flute"><voices><voice><noteObjects>'
         '<timeSign time="2/4"/>'+triplets+
         '<rest><duration base="1/4"/></rest>'
         '</noteObjects></voice></voices></staff></staves></system>'
         '</systems></score>')
    music,report=serialize_musicxml(as_capx(xml))
    notes=ET.fromstring(music).findall('./part/measure/note')
    assert report['measures']==1
    assert notes[0].find('.//notations/tuplet[@type="start"]') is not None
    assert notes[2].find('.//notations/tuplet[@type="stop"]') is not None
    assert all(n.find('time-modification') is not None for n in notes[:3])
