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


def test_secondary_voice_gap_is_completed_when_primary_voice_fixes_bar_length(tmp_path):
    """An imported second voice may omit only its final silent beat."""
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    opening = ET.SubElement(staff, 'Measure')
    opening_voice = _voice(_event('Rest', 'half'))
    opening_voice.insert(0, ET.fromstring(
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'))
    opening.append(opening_voice)
    measure = ET.SubElement(staff, 'Measure')
    primary = _voice(_event('Chord', 'quarter'), _event('Chord', 'quarter'))
    measure.append(primary)
    measure.append(_voice(_event('Chord', 'quarter')))
    path = tmp_path / 'secondary-gap.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('silent gap 1/4 at staff 1, measure 2, voice 2' in item
               for item in result['repairs'])
    assert audit_mscz(path, repair=False) == []
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    rest = native.find('./Score/Staff/Measure[2]/voice[2]/Rest')
    assert rest is not None
    assert rest.findtext('visible') == '0'


def test_displaced_secondary_voice_large_tail_is_safely_completed(tmp_path):
    """A complete primary voice proves even a large secondary tail is silent."""
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    measure = ET.SubElement(staff, 'Measure')
    measure.append(_voice(*(_event('Chord', 'quarter') for _ in range(4))))
    secondary = _voice(_event('Chord', 'eighth'))
    location = ET.Element('location')
    ET.SubElement(location, 'fractions').text = '1/8'
    secondary.append(location)
    secondary.append(_event('Chord', 'eighth'))
    measure.append(secondary)
    path = tmp_path / 'large-secondary-gap.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('silent gap 5/8 at staff 1, measure 1, voice 2' in item
               for item in result['repairs'])
    assert audit_mscz(path, repair=False) == []
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    rest = native.find('./Score/Staff/Measure/voice[2]/Rest')
    assert rest is not None
    assert rest.findtext('duration') == '5/8'
    assert rest.findtext('visible') == '0'


def test_unknown_named_xml_entities_remain_fail_closed(tmp_path):
    path = tmp_path / 'unknown-entity.mscz'
    _write(path, '<Staff id="1"><Measure><StaffText><text>&mystery;</text></StaffText>'
           '<voice><Rest><durationType>whole</durationType></Rest></voice></Measure></Staff>')

    try:
        normalize_legacy_cap_mscz(path)
    except ET.XMLSyntaxError:
        pass
    else:
        raise AssertionError('unknown XML entity must not be guessed')


def test_html_nonbreaking_space_in_metadata_is_normalized_to_valid_xml(tmp_path):
    path = tmp_path / 'nbsp.mscz'
    _write(path, '<Staff id="1"><Measure><StaffText><text>Title&nbsp;line</text></StaffText>'
           '<voice><Rest><durationType>whole</durationType></Rest></voice></Measure></Staff>')

    result = normalize_legacy_cap_mscz(path)

    assert any('nbsp' in item for item in result['repairs'])
    assert audit_mscz(path, repair=False) == []
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.findtext('./Score/Staff/Measure/StaffText/text') == 'Title line'


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


def test_gap_padding_is_never_added_inside_a_carried_tuplet(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    full = ET.SubElement(score, 'Staff', id='1')
    full_measure = ET.SubElement(full, 'Measure')
    full_measure.append(_voice(_event('Rest', 'half')))
    open_staff = ET.SubElement(score, 'Staff', id='2')
    open_measure = ET.SubElement(open_staff, 'Measure')
    tuplet = ET.Element('Tuplet')
    ET.SubElement(tuplet, 'normalNotes').text = '2'
    ET.SubElement(tuplet, 'actualNotes').text = '3'
    open_measure.append(_voice(tuplet, _event('Chord', 'quarter'),
                               _event('Rest', 'quarter')))
    path = tmp_path / 'open-tuplet-gap.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert not any('silent gap' in item for item in result['repairs'])


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


def test_shared_irregular_active_bar_ignores_rest_only_staff(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    for staff_id in ('1', '2', '3', '4'):
        staff = ET.SubElement(score, 'Staff', id=staff_id)
        opening = ET.SubElement(staff, 'Measure')
        opening.append(_voice(
            ET.fromstring('<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'),
            _event('Rest', 'half'),
        ))
        irregular = ET.SubElement(staff, 'Measure')
        irregular.append(_voice(
            _event('Chord', 'quarter'), _event('Chord', 'eighth'),
            _event('Chord', 'eighth'), _event('Chord', '16th'),
        ))
        full = ET.SubElement(staff, 'Measure')
        full.append(_voice(_event('Rest', 'half')))

    silent = ET.SubElement(score, 'Staff', id='5')
    opening = ET.SubElement(silent, 'Measure')
    opening.append(_voice(
        ET.fromstring('<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'),
        _event('Rest', 'half'),
    ))
    irregular = ET.SubElement(silent, 'Measure')
    irregular.append(_voice(_event('Rest', 'half')))
    full = ET.SubElement(silent, 'Measure')
    full.append(_voice(_event('Rest', 'half')))

    path = tmp_path / 'active-irregular.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert result['changed']
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.find('./Score/Staff[1]/Measure[2]').get('len') == '9/16'
    assert native.find('./Score/Staff[4]/Measure[2]').get('len') == '9/16'
    assert native.find('./Score/Staff[5]/Measure[2]').get('len') is None
    assert audit_mscz(path, repair=False) == []


def test_textual_navigation_is_promoted_but_unquantified_tempo_is_reported(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    primary = ET.SubElement(score, 'Staff', id='1')
    first = ET.SubElement(primary, 'Measure')
    first.append(_voice(_event('Rest', 'whole')))
    fine = ET.SubElement(first, 'StaffText')
    ET.SubElement(fine, 'text').text = 'Fine'
    second = ET.SubElement(primary, 'Measure')
    second.append(_voice(_event('Rest', 'whole')))
    other = ET.SubElement(score, 'Staff', id='2')
    first_other = ET.SubElement(other, 'Measure')
    first_other.append(_voice(_event('Rest', 'whole')))
    second_other = ET.SubElement(other, 'Measure')
    second_other.append(_voice(_event('Rest', 'whole')))
    direction = ET.SubElement(second_other, 'StaffText')
    ET.SubElement(direction, 'text').text = 'D.S. al Fine'
    tempo = ET.SubElement(first_other, 'StaffText')
    ET.SubElement(tempo, 'text').text = 'rit.'

    path = tmp_path / 'directions.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('playback marker "fine"' in item for item in result['repairs'])
    assert any('playback jump "segno|fine"' in item for item in result['repairs'])
    assert any('rit.' in item and 'no numeric tempo map' in item
               for item in result['diagnostics'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.find('./Score/Staff[1]/Measure[1]/Marker/label').text == 'fine'
    marker = native.find('./Score/Staff[1]/Measure[1]/Marker')
    assert marker.findtext('text') == 'Fine'
    assert marker.findtext('visible') == '0'
    assert '&lt;sym&gt;' not in ET.tostring(marker, encoding='unicode')
    assert native.find('./Score/Staff[1]/Measure[2]/Jump/jumpTo').text == 'segno'
    assert native.find('./Score/Staff[1]/Measure[2]/Jump/playUntil').text == 'fine'


def test_escaped_marker_from_older_release_is_repaired_and_hidden(tmp_path):
    path = tmp_path / 'escaped-marker.mscz'
    _write(
        path,
        '<Staff id="1"><Measure><Marker>'
        '<text>&lt;sym&gt;fine&lt;/sym&gt;</text><label>fine</label>'
        '</Marker><voice><Rest><durationType>whole</durationType></Rest>'
        '</voice></Measure></Staff>',
    )

    result = normalize_legacy_cap_mscz(path)

    assert any('escaped playback marker "fine"' in item
               for item in result['repairs'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    marker = native.find('./Score/Staff/Measure/Marker')
    assert marker.findtext('text') == 'Fine'
    assert marker.findtext('visible') == '0'


def test_compound_numeric_tempo_becomes_one_native_tempo_event(tmp_path):
    """German labels such as ``Buehnentempo`` must not remain text-only."""
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    for staff_id in ('1', '2'):
        staff = ET.SubElement(score, 'Staff', id=staff_id)
        measure = ET.SubElement(staff, 'Measure')
        measure.append(_voice(_event('Rest', 'whole')))
        direction = ET.SubElement(measure, 'StaffText')
        ET.SubElement(direction, 'text').text = 'Bühnentempo = 120'

    path = tmp_path / 'compound-tempo.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('native numeric tempo 120 BPM' in item
               for item in result['repairs'])
    assert not result['diagnostics']
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    tempos = native.findall('.//Tempo')
    assert len(tempos) == 1
    assert tempos[0].findtext('tempo') == '2'


def test_a_tempo_restores_most_recent_numeric_tempo(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    first = ET.SubElement(staff, 'Measure')
    first.append(_voice(_event('Rest', 'whole')))
    tempo = ET.SubElement(first, 'Tempo')
    ET.SubElement(tempo, 'tempo').text = '3/2'
    ET.SubElement(tempo, 'text').text = '90'
    second = ET.SubElement(staff, 'Measure')
    second.append(_voice(_event('Rest', 'whole')))
    direction = ET.SubElement(second, 'StaffText')
    ET.SubElement(direction, 'text').text = 'a tempo'
    path = tmp_path / 'a-tempo.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert not result['diagnostics']
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    restored = native.find('./Score/Staff/Measure[2]/voice/Tempo')
    assert restored is not None
    assert restored.findtext('tempo') == '3/2'


def test_capella_segno_font_glyph_becomes_native_marker(tmp_path):
    path = tmp_path / 'segno.mscz'
    _write(
        path,
        '<Staff id="1"><Measure><StaffText><family>capella3</family>'
        '<text>y</text></StaffText><voice><Rest><durationType>whole'
        '</durationType></Rest></voice></Measure></Staff>',
    )

    result = normalize_legacy_cap_mscz(path)

    assert any('native segno marker' in item for item in result['repairs'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    marker = native.find('./Score/Staff/Measure/Marker')
    assert marker.findtext('label') == 'segno'
    assert marker.findtext('text/sym') == 'segno'
    assert native.find('.//StaffText') is None


def test_adjacent_zero_numbered_voltas_are_reconstructed_as_first_and_second(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    for index in range(4):
        measure = ET.SubElement(staff, 'Measure')
        voice = _voice(_event('Rest', 'whole'))
        measure.append(voice)
        if index in (1, 2):
            spanner = ET.SubElement(voice, 'Spanner', type='Volta')
            volta = ET.SubElement(spanner, 'Volta')
            ET.SubElement(volta, 'endings').text = '0'
            ET.SubElement(volta, 'beginText').text = '0.'
            next_node = ET.SubElement(spanner, 'next')
            location = ET.SubElement(next_node, 'location')
            ET.SubElement(location, 'measures').text = '1'
        if index == 1:
            ET.SubElement(measure, 'endRepeat')
    path = tmp_path / 'zero-voltas.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('first/second endings' in item for item in result['repairs'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert native.xpath('.//Volta/endings/text()') == ['1', '2']
    assert native.xpath('.//Volta/beginText/text()') == ['1.', '2.']


def test_repeated_duplicate_full_measure_tail_rests_are_removed(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    for staff_id in ('1', '2'):
        staff = ET.SubElement(score, 'Staff', id=staff_id)
        for _ in range(4):
            measure = ET.SubElement(staff, 'Measure')
            voice = _voice(
                ET.fromstring('<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'),
                _event('Chord', 'half'),
                _event('Rest', 'half'),
            )
            measure.append(voice)
    path = tmp_path / 'duplicate-tail-rests.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert sum('duplicate importer-generated' in item
               for item in result['repairs']) == 8
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert not native.findall('.//Rest')
    assert audit_mscz(path, repair=False) == []


def test_isolated_overfull_tail_rest_is_not_guessed(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    measure = ET.SubElement(staff, 'Measure')
    measure.append(_voice(
        ET.fromstring('<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'),
        _event('Chord', 'half'), _event('Rest', 'half')))
    path = tmp_path / 'isolated-overfull.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert not any('duplicate importer-generated' in item
                   for item in result['repairs'])


def test_playback_only_tempos_are_hidden_and_staff_duplicates_removed(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    for staff_id in ('1', '2'):
        staff = ET.SubElement(score, 'Staff', id=staff_id)
        measure = ET.SubElement(staff, 'Measure')
        voice = _voice()
        tempo = ET.SubElement(voice, 'Tempo')
        ET.SubElement(tempo, 'tempo').text = '5/3'
        voice.append(_event('Rest', 'whole'))
        measure.append(voice)
    path = tmp_path / 'tempo-map.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('duplicate score-global' in item for item in result['repairs'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    assert len(native.findall('.//Tempo')) == 1
    assert native.findtext('.//Tempo/visible') == '0'


def test_missing_terminal_second_ending_is_restored_from_repeated_pairs(tmp_path):
    root = ET.Element('museScore')
    score = ET.SubElement(root, 'Score')
    staff = ET.SubElement(score, 'Staff', id='1')
    for index in range(7):
        measure = ET.SubElement(staff, 'Measure')
        voice = _voice(_event('Rest', 'whole'))
        measure.append(voice)
        number = {0: '1', 1: '2', 2: '1', 3: '2', 5: '1'}.get(index)
        if number:
            spanner = ET.Element('Spanner', type='Volta')
            volta = ET.SubElement(spanner, 'Volta')
            ET.SubElement(volta, 'endings').text = number
            next_node = ET.SubElement(spanner, 'next')
            location = ET.SubElement(next_node, 'location')
            ET.SubElement(location, 'measures').text = '1'
            voice.insert(0, spanner)
    path = tmp_path / 'terminal-ending.mscz'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('score.mscx', ET.tostring(root))

    result = normalize_legacy_cap_mscz(path)

    assert any('terminal second ending' in item for item in result['repairs'])
    with zipfile.ZipFile(path) as archive:
        native = ET.fromstring(archive.read('score.mscx'))
    final = native.find('./Score/Staff/Measure[7]/voice/Spanner/Volta')
    assert final.findtext('endings') == '2'
    assert final.findtext('beginText') == '2.'
