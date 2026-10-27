"""Apply portable MuseScore 3 print styles to editable MSCZ archives.

MuseScore's CLI importer may ignore MusicXML measure-numbering on a score.
Editing the *native score Style* guarantees that the exported MSCZ, PDF
and PNG previews all share the same staff/measure numbering preferences.
"""
from __future__ import annotations

import os
import re
import tempfile
import textwrap
import zipfile
from copy import deepcopy
from pathlib import Path
from lxml import etree as ET

STYLE_VALUES = {
    'showMeasureNumber': '1',
    'showMeasureNumberOne': '0',
    'measureNumberSystem': '1',
    'measureNumberAllStaffs': '0',
    # The importer creates one Part for every Capella instrument, even
    # where the source introduces that part only in later systems.
    'hideEmptyStaves': '0',
    'dontHideStavesInFirstSystem': '0',
    'createMultiMeasureRests': '1',
    # Source credits are handled explicitly; a generated footer duplicates
    # them on every MuseScore page.
    'showFooter': '0',
}

_CHORD_ROOTS = {
    'C': 14, 'D': 16, 'E': 18, 'F': 13,
    'G': 15, 'A': 17, 'B': 19, 'H': 19,
}
_CAP_DATE = re.compile(r'\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b')
_CAP_ROOT = re.compile(
    r'^\s*([A-GH](?:(?:is|es)|[#b♯♭])?)(.*)$', re.IGNORECASE
)
_CAP_CHORD_SUFFIX = re.compile(
    r'^(?:(?:m|min|maj|dim|aug|sus|add|no|omit)|[0-9]+|'
    r'[+\-°øΔM#b()])*$', re.IGNORECASE
)
_CAP_NON_TITLE = {
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'intro', 'coda', 'solo',
    'tutti', 'fine', 'rip', 'rit.', 'rall.', 'accelerando', '1', '2',
    '3', '1.', '2.', '3.',
}
_CHORD_LAYER_NAME = re.compile(r'(?i)\b(?:gitarre|guitar|akkord|chord)\b')
_CHORD_TOKEN = re.compile(
    r'(?i)^[A-H](?:#|b|is|es)?(?:m|maj|min|dim|aug|sus|add)?\d*'
    r'(?:[-+](?:5|9))?$'
)


def _is_chord_choice_label(value: str) -> bool:
    tokens = [token.strip() for token in value.split('/') if token.strip()]
    return bool(tokens) and all(_CHORD_TOKEN.fullmatch(token) for token in tokens)


def _native_text(item) -> str:
    text = item.find('text')
    return ' '.join(''.join(text.itertext()).split()) if text is not None else ''


def _cap_title_candidate(label: str) -> bool:
    folded = label.casefold().strip()
    if len(folded) < 4 or folded in _CAP_NON_TITLE:
        return False
    # Legacy generated accompaniment staves commonly place choices such as
    # "D / D9" in the first measures. They are printed chord labels, not a
    # title, and must remain attached to their musical measures.
    if _is_chord_choice_label(label):
        return False
    if _CAP_DATE.search(label):
        return True
    if re.search(
        r'\b(arr\.?|arrangiert|arranged|bearb\.?|vom|von|by|eingerichtet|'
        r'edit|intermezzo|abspielversion)\b', folded
    ):
        return True
    if folded in {'reihenfolge', 'instrumental'}:
        return False
    # The legacy importer places the title in the opening measures.  Keep
    # short performance directions in the staff and move only ordinary title
    # text from that narrow opening window.
    return not any(word in folded for word in (
        'crescendo', 'diminuendo', 'piano', 'forte', 'refrain',
    ))


def _append_frame_text(frame, style, value) -> bool:
    value = value.strip()
    if not value:
        return False
    for item in frame.findall('Text'):
        if item.findtext('style') == style and _native_text(item) == value:
            return False
    item = ET.SubElement(frame, 'Text')
    ET.SubElement(item, 'style').text = style
    ET.SubElement(item, 'text').text = value
    return True


def _normalize_cap_opening_text(score, frame, changes: dict) -> None:
    """Move importer title/credit StaffText into a real title frame."""
    leading = score.find('Staff')
    if leading is None:
        return
    candidates = []
    for measure in leading.findall('Measure')[:3]:
        for voice in measure.findall('voice'):
            for item in list(voice.findall('StaffText')):
                label = _native_text(item)
                if label and _cap_title_candidate(label):
                    candidates.append((voice, item, label))
    if not candidates:
        return
    title = next((label for _, _, label in candidates
                  if not _CAP_DATE.search(label) and not re.search(
                      r'\b(arr\.?|arrangiert|arranged|bearb\.?|vom|von|by|'
                      r'eingerichtet|edit)\b', label, re.I)), None)
    if title is None:
        title = candidates[0][2]
    subtitles = []
    for voice, item, label in candidates:
        if label != title:
            subtitles.append(label)
        voice.remove(item)
    if _append_frame_text(frame, 'Title', title):
        changes['repairs'].append(f'Moved CAP title into title frame: {title}')
    if subtitles:
        value = '\n'.join(dict.fromkeys(subtitles))
        if _append_frame_text(frame, 'Subtitle', value):
            changes['repairs'].append('Moved CAP opening credits into title frame')
    changes['title_text'] = title


def _parse_chord_label(label: str):
    match = _CAP_ROOT.match(label)
    if not match:
        return None
    root_text, suffix = match.groups()
    root_name = root_text[0].upper()
    accidental = root_text[1:].casefold()
    root = _CHORD_ROOTS[root_name]
    if accidental in ('#', '♯', 'is'):
        root += 7
    elif accidental in ('b', '♭', 'es'):
        root -= 7
    suffix = suffix.strip()
    base = None
    slash = re.search(r'/\s*([A-GH](?:(?:is|es)|[#b♯♭])?)\s*$', suffix,
                      re.IGNORECASE)
    if slash:
        base_text = slash.group(1)
        base = _CHORD_ROOTS[base_text[0].upper()]
        accidental = base_text[1:].casefold()
        if accidental in ('#', '♯', 'is'):
            base += 7
        elif accidental in ('b', '♭', 'es'):
            base -= 7
        suffix = suffix[:slash.start()].strip()
    if suffix and not _CAP_CHORD_SUFFIX.fullmatch(suffix):
        return None
    return root, suffix, base


def _normalize_cap_chord_annotations(score, changes: dict) -> None:
    """Turn CAP-imported Lyrics chord labels into native Harmony objects."""
    for voice in score.findall('.//voice'):
        for chord in list(voice.findall('Chord')):
            labels = []
            for lyric in list(chord.findall('Lyrics')):
                label = ' '.join(''.join(lyric.itertext()).split())
                parsed = _parse_chord_label(label) if label else None
                # Lyrics can be genuine sung text. Only remove a label when
                # its root/suffix grammar proves that it is a chord symbol;
                # unknown text remains a lyric instead of being relabelled.
                if parsed is not None:
                    labels.append((lyric, label, parsed))
            if not labels:
                continue
            for lyric, _, parsed in labels:
                chord.remove(lyric)
            # One chord symbol is the meaningful annotation at this onset;
            # retain any additional parsed source labels as separate objects.
            for label_index, (_, _, parsed) in enumerate(labels):
                root, suffix, base = parsed
                annotation = ET.Element('Harmony')
                ET.SubElement(annotation, 'root').text = str(root)
                if suffix:
                    ET.SubElement(annotation, 'name').text = suffix
                if base is not None:
                    ET.SubElement(annotation, 'base').text = str(base)
                index = list(voice).index(chord) + label_index
                voice.insert(index, annotation)
                changes['chord_annotations'] += 1
    if changes['chord_annotations']:
        changes['repairs'].append(
            f'Converted {changes["chord_annotations"]} CAP chord annotation(s) '
            'to native MuseScore objects'
        )


_SAFE_EMPTY_STAFF_MEASURE_TAGS = {
    'voice', 'LayoutBreak', 'startRepeat', 'endRepeat', 'repeatCount',
}
_SAFE_EMPTY_STAFF_VOICE_TAGS = {
    'Rest', 'TimeSig', 'KeySig', 'Clef',
}


def _is_prunable_rest_only_staff(staff) -> bool:
    """Return whether an imported staff has no musical or textual meaning.

    Rest-only importer artefacts must be removed, not merely hidden: MuseScore
    still lays out their bars and staff lines in PDFs. This deliberately
    accepts only rests plus baseline clef/key/meter and system-break data. A
    staff carrying any text, direction, spanner, repeat-ending, symbol, or
    pitched event is retained untouched.
    """
    if staff.find('.//Chord') is not None or staff.find('.//Note') is not None:
        return False
    if staff.find('.//Rest') is None:
        return False
    if any(item.tag not in {'Measure', 'VBox', 'visible'} for item in staff):
        return False
    for measure in staff.findall('Measure'):
        if any(item.tag not in _SAFE_EMPTY_STAFF_MEASURE_TAGS
               for item in measure):
            return False
        for voice in measure.findall('voice'):
            if any(item.tag not in _SAFE_EMPTY_STAFF_VOICE_TAGS
                   for item in voice):
                return False
    return True


def _structural_fingerprint(item):
    """Return a whitespace-independent identity for native structure.

    Pretty-print indentation is stored in ``tail`` text by XML parsers.  It is
    not musical data, and comparing serialized XML allowed identical CAP
    clefs, keys, and meters to be copied twice when an empty staff was pruned.
    Preserve every real child and attribute while ignoring indentation.
    """
    text = (item.text or '').strip()
    return (
        item.tag,
        tuple(sorted(item.attrib.items())),
        text,
        tuple(_structural_fingerprint(child) for child in item),
    )


def _copy_if_missing(parent, item) -> None:
    """Copy a structural item only when the target does not already own it."""
    fingerprint = _structural_fingerprint(item)
    existing = {_structural_fingerprint(candidate) for candidate in parent}
    if fingerprint not in existing:
        parent.append(deepcopy(item))


def _transfer_empty_staff_structure(source, target) -> None:
    """Preserve system and score-wide structure before pruning a staff."""
    for frame in source.findall('VBox'):
        # The first surviving staff owns the title frame after an initial
        # importer artefact is removed. Insert it ahead of existing measures.
        target.insert(0, deepcopy(frame))

    source_measures = source.findall('Measure')
    target_measures = target.findall('Measure')
    for index, source_measure in enumerate(source_measures):
        if index >= len(target_measures):
            break
        target_measure = target_measures[index]
        for item in source_measure:
            if item.tag in {'LayoutBreak', 'startRepeat', 'endRepeat',
                            'repeatCount'}:
                _copy_if_missing(target_measure, item)
        source_voice = source_measure.find('voice')
        target_voice = target_measure.find('voice')
        if source_voice is None or target_voice is None:
            continue
        for item in source_voice:
            if item.tag not in {'TimeSig', 'KeySig', 'Clef'}:
                continue
            fingerprint = _structural_fingerprint(item)
            existing = {
                _structural_fingerprint(candidate)
                for candidate in target_voice
            }
            if fingerprint not in existing:
                target_voice.insert(0, deepcopy(item))


def _deduplicate_measure_structure(score, changes: dict) -> None:
    """Remove only exact duplicate clef/key/meter objects in one voice."""
    removed = 0
    for voice in score.findall('.//voice'):
        seen = set()
        for item in list(voice):
            if item.tag not in {'TimeSig', 'KeySig', 'Clef'}:
                continue
            fingerprint = _structural_fingerprint(item)
            if fingerprint in seen:
                voice.remove(item)
                removed += 1
            else:
                seen.add(fingerprint)
    if removed:
        changes['deduplicated_structure'] += removed
        changes['repairs'].append(
            f'Removed {removed} duplicate CAP clef/key/meter object(s)'
        )


def _rebuild_part_spans(score, old_part_staves, retained_ids) -> None:
    """Rebase bracket and barline spans after removing CAP artefact staves."""
    positions = {staff.get('id'): index
                 for index, staff in enumerate(old_part_staves)}
    descriptors = []
    for staff in old_part_staves:
        start = positions[staff.get('id')]
        for bracket in staff.findall('bracket'):
            try:
                span = int(bracket.get('span', '1'))
            except ValueError:
                continue
            descriptors.append(('bracket', staff.get('id'), span,
                                deepcopy(bracket)))
        bar = staff.find('barLineSpan')
        if bar is not None:
            try:
                span = int(bar.text or '0')
            except ValueError:
                continue
            descriptors.append(('barLineSpan', staff.get('id'), span,
                                deepcopy(bar)))
        for item in list(staff):
            if item.tag in {'bracket', 'barLineSpan'}:
                staff.remove(item)

    retained = set(retained_ids)
    current = {
        staff.get('id'): staff
        for part in score.findall('Part')
        for staff in part.findall('Staff')
    }
    for kind, start_id, span, item in descriptors:
        start = positions[start_id]
        covered = [old_part_staves[position].get('id')
                   for position in range(start, min(start + span,
                                                     len(old_part_staves)))]
        kept = [staff_id for staff_id in covered if staff_id in retained]
        if not kept:
            continue
        target = current.get(kept[0])
        if target is None:
            continue
        if kind == 'bracket':
            if len(kept) < 2:
                continue
            item.set('span', str(len(kept)))
        elif span > 1:
            if len(kept) < 2:
                continue
            item.text = str(len(kept))
        target.append(item)


def _prune_rest_only_staves(score, changes: dict) -> None:
    """Remove globally empty CAP-import artefact staves without losing data."""
    # Track-addressed objects require a wider remapping than is safe in this
    # targeted repair. Retain the staff in that rare form rather than risk
    # moving a direction to another playback track.
    has_track_addresses = any(
        item.tag in {'track', 'track2'} or 'track' in item.attrib
        for item in score.iter()
    )
    if has_track_addresses:
        # Removing a staff would require remapping every track-addressed
        # spanner. Hiding a provably rest-only staff is musically equivalent,
        # keeps those references stable, and prevents empty lines in PDF.
        score_staves = score.findall('Staff')
        prunable = [staff for staff in score_staves
                    if _is_prunable_rest_only_staff(staff)]
        for staff in prunable:
            staff_id = staff.get('id')
            visible = staff.find('visible')
            if visible is None:
                visible = ET.Element('visible')
                staff.insert(0, visible)
            visible.text = '0'
            for part_staff in score.findall(f'./Part/Staff[@id="{staff_id}"]'):
                part_visible = part_staff.find('visible')
                if part_visible is None:
                    part_visible = ET.SubElement(part_staff, 'visible')
                part_visible.text = '0'
            if staff_id not in changes['hidden_staves']:
                changes['hidden_staves'].append(staff_id)
        if prunable:
            changes['repairs'].append(
                'Hid globally rest-only CAP staff/staves while preserving '
                'track-addressed objects: ' + ', '.join(
                    str(staff.get('id')) for staff in prunable)
            )
        return

    score_staves = score.findall('Staff')
    prunable = [staff for staff in score_staves if _is_prunable_rest_only_staff(staff)]
    if not prunable or len(prunable) == len(score_staves):
        return

    prunable_ids = {staff.get('id') for staff in prunable}
    retained_score_staves = [staff for staff in score_staves
                             if staff.get('id') not in prunable_ids]
    for staff in prunable:
        source_index = score_staves.index(staff)
        target = next((candidate for candidate in score_staves[source_index + 1:]
                       if candidate.get('id') not in prunable_ids), None)
        if target is None:
            target = next((candidate for candidate in reversed(score_staves[:source_index])
                           if candidate.get('id') not in prunable_ids), None)
        if target is None:
            return
        _transfer_empty_staff_structure(staff, target)

    old_part_staves = [staff for part in score.findall('Part')
                       for staff in part.findall('Staff')]
    _rebuild_part_spans(score, old_part_staves,
                        {staff.get('id') for staff in retained_score_staves})

    for staff in prunable:
        score.remove(staff)
    for part in list(score.findall('Part')):
        for staff in list(part.findall('Staff')):
            if staff.get('id') in prunable_ids:
                part.remove(staff)
        if not part.findall('Staff'):
            score.remove(part)

    # MSCX staff IDs are expected to be contiguous. CAP imports in this
    # branch contain no track references (guarded above), so the ID remap is
    # safe after score/Part structures have been reduced together.
    retained_ids = [staff.get('id') for staff in retained_score_staves]
    ids = {old_id: str(index) for index, old_id in enumerate(retained_ids, 1)}
    for staff in score.findall('Staff'):
        staff.set('id', ids[staff.get('id')])
    for part in score.findall('Part'):
        for staff in part.findall('Staff'):
            staff.set('id', ids[staff.get('id')])

    changes['removed_staves'].extend(
        int(staff_id) if (staff_id or '').isdigit() else staff_id
        for staff_id in sorted(prunable_ids, key=lambda value: int(value or '0'))
    )
    changes['repairs'].append(
        'Removed globally rest-only imported CAP staff/staves: ' +
        ', '.join(map(str, changes['removed_staves']))
    )


def _hide_nonprinting_chord_layers(score, changes: dict) -> None:
    """Keep dense CAP playback voicings sounding but out of the print.

    Some legacy accordion files carry a generated guitar accompaniment on a
    staff which Capella prints as chord labels only. MuseScore's CAP importer
    loses that staff-level non-printing flag. The detection is intentionally
    narrow: an accompaniment-named part, chord labels across at least half of
    its measures, and uniformly dense four-or-more-note playback chords.
    """
    names = {}
    for part in score.findall('Part'):
        name = ' '.join((part.findtext('trackName') or '').split())
        for staff in part.findall('Staff'):
            names[staff.get('id')] = name
    for staff in score.findall('Staff'):
        name = names.get(staff.get('id'), '')
        if not _CHORD_LAYER_NAME.search(name):
            continue
        measures = staff.findall('Measure')
        if not measures:
            continue
        labelled = 0
        for measure in measures:
            labels = [
                ' '.join(''.join(text.itertext()).split())
                for text in measure.findall('.//StaffText/text')
            ]
            if any(_is_chord_choice_label(label) for label in labels):
                labelled += 1
        chords = staff.findall('.//Chord')
        if labelled * 2 < len(measures) or len(chords) < len(measures):
            continue
        note_counts = [len(chord.findall('Note')) for chord in chords]
        if not note_counts or sum(count >= 4 for count in note_counts) * 10 < len(chords) * 9:
            continue
        if staff.find('.//Articulation') is not None:
            continue
        hidden = 0
        for chord in chords:
            visible = chord.find('visible')
            if visible is None:
                visible = ET.Element('visible')
                chord.insert(0, visible)
            if visible.text != '0':
                visible.text = '0'
                hidden += 1
        if hidden:
            changes['hidden_note_layers'].append(staff.get('id'))
            changes['repairs'].append(
                f'Hid {hidden} generated playback chord(s) on non-printing '
                f'CAP accompaniment staff {staff.get("id")} ({name})'
            )


def _hide_internal_view_flags(score, changes: dict) -> None:
    """Keep editing-only objects out of delivered prints and previews.

    MuseScore's legacy CAP importer can persist ``showInvisible`` and
    ``showUnprintable`` as enabled.  That makes a staff deliberately marked
    ``visible=0`` appear in an exported PDF, defeating the rest-only-staff
    repair and potentially exposing hidden notation.  These are editor view
    switches, not source notation, so a downloadable score must leave them
    disabled.
    """
    for name in ('showInvisible', 'showUnprintable'):
        node = score.find(name)
        if node is None:
            node = ET.SubElement(score, name)
        if node.text != '0':
            node.text = '0'
            changes['view_flags'].append(name)
    if changes['view_flags']:
        changes['repairs'].append(
            'Disabled editor-only display flag(s): ' +
            ', '.join(changes['view_flags'])
        )


def _enable_native_text_autoplace(score, changes: dict) -> None:
    count = 0
    for item in score.iter():
        if item.tag not in ('StaffText', 'SystemText'):
            continue
        if item.find('offset') is not None or item.find('pos') is not None:
            continue
        autoplace = item.find('autoplace')
        if autoplace is None:
            autoplace = ET.SubElement(item, 'autoplace')
            count += 1
        elif autoplace.text != '1':
            autoplace.text = '1'
            count += 1
    if count:
        changes['repairs'].append(f'Enabled autoplace for {count} CAP text object(s)')


def prepare_print_ready(mscz_path: Path, metadata: dict | None = None,
                        source_format: str | None = None) -> dict:
    """Update score-wide MuseScore styles, preserving every ZIP member."""
    mscz_path = Path(mscz_path)
    changes = {
        'repairs': [],
        'hidden_staves': [],
        'removed_staves': [],
        'chord_annotations': 0,
        'deduplicated_structure': 0,
        'view_flags': [],
        'hidden_note_layers': [],
    }
    temp = None
    try:
        with zipfile.ZipFile(mscz_path, 'r') as archive:
            score_name = next((n for n in archive.namelist() if n.lower().endswith('.mscx')), None)
            if not score_name:
                raise ValueError('No editable MuseScore score found in MSCZ')
            score_data = archive.read(score_name)
            root = ET.fromstring(score_data, parser=ET.XMLParser(resolve_entities=False, no_network=True))
            score = root.find('Score')
            if score is None:
                raise ValueError('MSCZ does not contain a MuseScore Score element')
            style = score.find('Style')
            if style is None:
                style = ET.Element('Style')
                score.insert(0, style)
            for key, value in STYLE_VALUES.items():
                node = style.find(key)
                if node is None:
                    node = ET.SubElement(style, key)
                if key == 'hideEmptyStaves' and source_format == 'cap':
                    value = '1'
                elif key == 'hideEmptyStaves' and metadata:
                    value = '1' if metadata.get('variable_staff_systems') else '0'
                node.text = value
            # MuseScore's MusicXML importer can add subtitle and composer in
            # the wrong order, causing the two text blocks to overlap.
            # Rebuild the three known credits as normal sequential VBox texts.
            # Keep unrelated imported frame elements (e.g. copyright).
            if metadata:
                frame = score.find('./Staff/VBox')
                if frame is not None:
                    # Capella paragraphs may contain several separate lines;
                    # flattening them into a long " — " subtitle runs off the
                    # right-hand page margin. Use native MuseScore text items
                    # so the title frame grows vertically, not horizontally.
                    credits = [('Title', metadata.get('title', ''))]
                    subtitle_lines = []
                    for paragraph in metadata.get('subtitle', '').splitlines():
                        subtitle_lines.extend(textwrap.wrap(
                            paragraph.strip(), width=50, break_long_words=False
                        ))
                    # MuseScore positions separate "Subtitle" Text nodes at
                    # the SAME baseline. A single rich-text block with <br/>
                    # keeps lines distinct and prevents the observed overlap.
                    if subtitle_lines:
                        credits.append(('Subtitle','\n'.join(subtitle_lines)))
                    if metadata.get('composer'):
                        # The byline may already be included verbatim in
                        # Capella's title ("..., vom George Gruntz").
                        canonical = lambda t: ''.join(c.casefold() for c in t if c.isalnum())
                        if canonical(metadata['composer']) not in canonical(metadata.get('title','')):
                            credits.append(('Composer',metadata['composer']))
                    old_credits = [element for element in frame.findall('Text')
                                   if element.findtext('style') in ('Title','Subtitle','Composer')]
                    if old_credits and any(value for _, value in credits):
                        children_in_order = list(frame)
                        insertion_index = children_in_order.index(old_credits[0])
                        for element in old_credits:
                            frame.remove(element)
                        for kind, value in credits:
                            if not value or (kind == 'Title' and value == 'Converted score'):
                                continue
                            text = ET.Element('Text')
                            ET.SubElement(text, 'style').text = kind
                            body = ET.SubElement(text, 'text')
                            # Use a literal newline in the native MuseScore
                            # value; XML <br/> is not parsed as a paragraph.
                            body.text = value
                            frame.insert(insertion_index, text)
                            insertion_index += 1
                        height = frame.find('height')
                        if height is not None:
                            height.text = str(max(14.0, 8.0 + 3.0 * (
                                len(credits)+max(0,len(subtitle_lines)-1)),
                                float(height.text or '0')))
            # MusicXML imports source graphical rests as StaffText.
            # Unicode 1D13E/1D13F is missing from the text font, and
            # MuseScore prints literal <sym> markup in these StaffText
            # objects. Use native, non-timed MuseScore Symbol elements at
            # the same voice/tick positions instead; they use the music
            # font and do not change note durations or playback.
            musical_rest_symbols={
                '\U0001D13E':'rest8th',
                '\U0001D13F':'rest16th',
            }
            for voice in score.findall('.//voice'):
                for index,item in enumerate(list(voice)):
                    if item.tag!='StaffText':
                        continue
                    content=item.find('text')
                    if content is None:
                        continue
                    glyph=''.join(content.itertext()).strip()
                    if glyph not in musical_rest_symbols:
                        continue
                    symbol=ET.Element('Symbol')
                    ET.SubElement(symbol,'name').text=musical_rest_symbols[glyph]
                    off=item.find('offset')
                    if off is not None:
                        symbol.append(ET.fromstring(ET.tostring(off)))
                    voice.remove(item)
                    voice.insert(index,symbol)
            # Never print internal MusicXML part identifiers (P1, P2 ...)
            # as instrument labels on subsequent systems.
            for part in score.findall('Part'):
                for instrument in part.findall('Instrument'):
                    short = instrument.find('shortName')
                    if short is not None and (short.text or '').strip().upper() in {
                            f'P{i}' for i in range(1,100)}:
                        short.text = ''
            # Restore source colors after MuseScore's MusicXML importer
            # normalizes colored directions to black StaffText objects.
            # Match the *complete* displayed label, not incidental substrings.
            if metadata:
                source_colors=metadata.get('source_text_colors') or {}
                for item in score.iter():
                    if item.tag not in ('StaffText','SystemText','Text','Dynamic'):
                        continue
                    words=item.find('text')
                    if words is None:
                        continue
                    label=' '.join(''.join(words.itertext()).split())
                    raw=source_colors.get(label)
                    if raw and len(raw)==7 and raw.startswith('#'):
                        color=item.find('color')
                        if color is None:
                            color=ET.SubElement(item,'color')
                        red,green,blue=(int(raw[i:i+2],16) for i in (1,3,5))
                        color.attrib.update({'r':str(red),'g':str(green),
                                             'b':str(blue),'a':'255'})
                # MuseScore can duplicate note-attached labels while
                # expanding/condensing a Capella multi-measure rest. Only
                # deduplicate within an explicit source rest span: the same
                # label can legitimately recur in a later section.
                source_spans=metadata.get('multi_rest_labels') or []
                leading=score.find('Staff')
                if leading is not None and source_spans:
                    measures=leading.findall('Measure')
                    for span in source_spans:
                        first=int(span['start_measure'])
                        last=first+int(span['measures'])
                        label=' '.join(str(span['label']).split())
                        kept=False
                        for measure in measures[max(first,0):min(last,len(measures))]:
                            for voice in measure.findall('voice'):
                                for item in list(voice.findall('StaffText')):
                                    words=item.find('text')
                                    if words is None or ' '.join(
                                            ''.join(words.itertext()).split())!=label:
                                        continue
                                    if kept:
                                        voice.remove(item)
                                    else:
                                        kept=True
                # MusicXML page>1 credits are silently dropped on import.
                # Retain editorial attributions as ordinary source text in
                # the final measure of the top staff, not new sounding notes.
                staff=score.find('Staff')
                if staff is not None:
                    measures=staff.findall('Measure')
                    if measures:
                        final_voice=measures[-1].find('voice')
                        if final_voice is not None:
                            for label in metadata.get('closing_credits') or []:
                                if not label.strip():
                                    continue
                                if any(label == ' '.join(
                                        ''.join(t.itertext()).split())
                                       for t in final_voice.findall('.//StaffText/text')):
                                    continue
                                marker=ET.SubElement(final_voice,'StaffText')
                                txt=ET.SubElement(marker,'text')
                                txt.text=label
                                ET.SubElement(marker,'offset').set('x','0')
                                marker.find('offset').set('y','8')
            if source_format == 'cap':
                frame = score.find('./Staff/VBox')
                if frame is None:
                    leading = score.find('Staff')
                    if leading is not None:
                        frame = ET.Element('VBox')
                        leading.insert(0, frame)
                if frame is not None:
                    _normalize_cap_opening_text(score, frame, changes)
                _normalize_cap_chord_annotations(score, changes)
                _hide_nonprinting_chord_layers(score, changes)
                _prune_rest_only_staves(score, changes)
                _deduplicate_measure_structure(score, changes)
                _enable_native_text_autoplace(score, changes)
            _hide_internal_view_flags(score, changes)
            # MusicXML imports a group of four independent parts as a single
            # barLineSpan=4 on the first staff. MuseScore 3 treats staff
            # spanning as a per-adjacent-staff flag, making bars appear joined
            # between only the first two parts. Expand the span into an
            # explicit connection on EACH adjacent upper staff.
            staves = [staff for part in score.findall('Part')
                      for staff in part.findall('Staff')]
            for index, staff in enumerate(staves):
                existing = staff.find('barLineSpan')
                if existing is None:
                    continue
                try:
                    span = int(existing.text or '0')
                except ValueError:
                    continue
                if span <= 1:
                    continue
                if index + span > len(staves):
                    raise ValueError('Imported MuseScore barline group exceeds score staves')
                for next_staff in staves[index:index + span - 1]:
                    node = next_staff.find('barLineSpan')
                    if node is None:
                        node = ET.SubElement(next_staff, 'barLineSpan')
                    node.text = '1'
                last = staves[index + span - 1]
                tail = last.find('barLineSpan')
                if tail is not None:
                    last.remove(tail)
            modified = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            with tempfile.NamedTemporaryFile(prefix='.print-', suffix='.mscz', dir=mscz_path.parent, delete=False) as fd:
                temp = Path(fd.name)
            with zipfile.ZipFile(temp, 'w') as output:
                for member in archive.infolist():
                    data = modified if member.filename == score_name else archive.read(member.filename)
                    output.writestr(member, data)
        os.replace(temp, mscz_path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return changes
