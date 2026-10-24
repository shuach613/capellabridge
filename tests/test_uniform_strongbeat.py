"""Explicit source accents must survive regardless of their frequency."""
import io
import zipfile

from lxml import etree as ET
from app.converter import serialize_musicxml


def capx(xml: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('score.xml', xml.encode())
    return output.getvalue()


def full_score(articulation_for_index):
    """40 quarter notes, ten 4/4 measures, one part; deliberately generic."""
    notes = []
    for index in range(40):
        articulations = articulation_for_index(index)
        attr = f'<articulation type="{articulations}"/>' if articulations else ''
        notes.append(
            '<chord><duration base="1/4"/>'
            + attr + '<heads><head pitch="C5"/></heads></chord>'
        )
    return capx(
        '<score xmlns="http://www.capella.de/CapXML/2.0">'
        '<layout><staves><staffLayout description="Flute"/></staves></layout>'
        '<systems><system><staves><staff layout="Flute"><voices><voice>'
        '<noteObjects><timeSign time="4/4"/>'
        + ''.join(notes) + '</noteObjects></voice></voices></staff>'
        '</staves></system></systems></score>'
    )


def annotations(xml):
    return ET.fromstring(xml)


def test_uniform_strongbeat_preserves_all_printed_marcato_accents():
    xml, report = serialize_musicxml(full_score(
        lambda n: 'strongBeat staccato' if n == 20 else 'strongBeat'
    ))
    root = annotations(xml)
    assert report['measures'] == 10
    assert report['normalized_uniform_strongbeat'] == 0
    assert len(root.findall('.//articulations/strong-accent')) == 40
    assert len(root.findall('.//articulations/staccato')) == 1
    assert len(root.findall('./part/measure[1]/note')) == 4
    assert len(root.findall('.//part/measure/note')) == 40
    assert all('strongBeat marks omitted' not in w for w in report['warnings'])


def test_sparse_strongbeat_is_still_an_explicit_marcato():
    xml, report = serialize_musicxml(
        full_score(lambda n: 'strongBeat' if n in (7, 24) else '')
    )
    root = annotations(xml)
    assert report['normalized_uniform_strongbeat'] == 0
    assert len(root.findall('.//articulations/strong-accent')) == 2


def test_standard_strong_accent_is_preserved_even_if_all_strongbeat():
    xml, report = serialize_musicxml(
        full_score(lambda n: 'strongBeat strongAccent' if n == 3 else 'strongBeat')
    )
    root = annotations(xml)
    assert report['normalized_uniform_strongbeat'] == 0
    assert len(root.findall('.//articulations/strong-accent')) == 40


def test_uniform_nineteen_note_passage_is_not_silently_normalized():
    # A short deliberately articulated musical passage must stay intentional.
    score = full_score(lambda n: 'strongBeat')
    # 40 source notes are enough to qualify; a short score should not.
    with zipfile.ZipFile(io.BytesIO(score)) as z:
        xml = z.read('score.xml').decode()
    first = xml.index('<chord>')
    last = xml.rfind('</chord>')+len('</chord>')
    passage = xml[first:last]
    # Keep the first eight chords, and use a time signature that fits 2 measures.
    end_marker = '</chord>'
    cursor = 0
    for _ in range(8):
        cursor = passage.index(end_marker, cursor) + len(end_marker)
    trimmed = xml[:first] + passage[:cursor] + xml[last:]
    musicxml, report = serialize_musicxml(capx(trimmed))
    root = annotations(musicxml)
    assert report['normalized_uniform_strongbeat'] == 0
    assert len(root.findall('.//articulations/strong-accent')) == 8
