"""Music-first structural repairs for the native legacy CAP path."""
import zipfile

from lxml import etree as ET

from app.legacy_cap_normalize import normalize_legacy_cap_mscz
from app.score_validation import audit_mscz


def _write(path, score_body):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(
            'score.mscx',
            f'<museScore version="3.02"><Score>{score_body}</Score></museScore>',
        )


def _voice(*items):
    voice = ET.Element('voice')
    voice.extend(items)
    return voice


def _event(tag, duration):
    item = ET.Element(tag)
    ET.SubElement(item, 'durationType').text = duration
    return item


def test_displaced_irregular_bar_boundaries_are_preserved(tmp_path):
    first = _voice(_event('Chord', 'quarter'), _event('Chord', 'eighth'),
                   _event('Rest', 'quarter'))  # 5/8
    second = _voice(_event('Chord', 'eighth'), _event('Rest', 'eighth'),
                    _event('Rest', 'eighth'))  # 3/8
    staff1 = ET.Element('Staff', id='1')
    for voice in (first, second):
        measure = ET.SubElement(staff1, 'Measure')
        measure.append(voice)
    staff2 = ET.fromstring(ET.tostring(staff1))
    staff2.set('id', '2')
    for staff in (staff1, staff2):
        opening = ET.Element('Measure')
        opening_voice = _voice(_event('Rest', 'half'))
        opening_voice.insert(0, ET.fromstring(
            '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'))
        opening.append(opening_voice)
        staff.insert(0, opening)
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    score.extend([staff1, staff2])
    path = tmp_path / 'irregular.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert result['changed']
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.find('./Score/Staff[1]/Measure[2]').get('len') == '5/8'
    assert native.find('./Score/Staff[1]/Measure[3]').get('len') == '3/8'
    assert audit_mscz(path, repair=False) == []


def test_isolated_silent_staff_gap_gets_an_invisible_rest(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    full = ET.SubElement(score, 'Staff', id='1')
    measure = ET.SubElement(full, 'Measure')
    measure.append(_voice(_event('Rest', 'whole')))
    short = ET.SubElement(score, 'Staff', id='2')
    measure = ET.SubElement(short, 'Measure')
    measure.append(_voice(_event('Rest', 'half'), _event('Rest', 'quarter')))
    path = tmp_path / 'gap.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('silent gap 1/4' in item for item in result['repairs'])
    assert audit_mscz(path, repair=False) == []
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    rest = native.find('./Score/Staff[2]/Measure/voice/Rest[3]')
    assert rest is not None
    assert rest.findtext('visible') == '0'


def test_tuplet_scope_can_cross_a_measure_without_false_unclosed_error(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    first = ET.SubElement(staff, 'Measure', len='1/2')
    tuplet = ET.Element('Tuplet')
    ET.SubElement(tuplet, 'normalNotes').text = '2'
    ET.SubElement(tuplet, 'actualNotes').text = '3'
    first.append(_voice(tuplet, _event('Chord', 'quarter'),
                        _event('Rest', 'half')))
    second = ET.SubElement(staff, 'Measure', len='1/2')
    second.append(_voice(ET.Element('endTuplet'), _event('Rest', 'half')))
    path = tmp_path / 'cross-measure-tuplet.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    assert audit_mscz(path, repair=False) == []


def test_infers_missing_shared_two_four_meter_without_changing_events(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    for staff_id in ('1', '2', '3'):
        staff = ET.SubElement(score, 'Staff', id=staff_id)
        for _ in range(4):
            measure = ET.SubElement(staff, 'Measure')
            measure.append(_voice(_event('Chord', 'half')))
    path = tmp_path / 'missing-meter.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert result['changed']
    assert sum('inferred time signature 2/4' in item
               for item in result['repairs']) == 3
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.xpath('count(./Score/Staff/Measure[1]/voice/TimeSig)') == 3
    assert audit_mscz(path, repair=False) == []


def test_propagates_a_known_meter_to_a_staff_missing_its_signature(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    signed = ET.SubElement(score, 'Staff', id='1')
    unsigned = ET.SubElement(score, 'Staff', id='2')
    for staff in (signed, unsigned):
        for measure_index in range(3):
            measure = ET.SubElement(staff, 'Measure')
            voice = _voice(_event('Chord', 'half'))
            if staff is signed and measure_index == 0:
                voice.insert(0, ET.fromstring(
                    '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'))
            measure.append(voice)
    path = tmp_path / 'shared-meter.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('shared time signature 2/4 at staff 2' in item
               for item in result['repairs'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.findtext('./Score/Staff[2]/Measure[1]/voice/TimeSig/sigN') == '2'
    assert audit_mscz(path, repair=False) == []
