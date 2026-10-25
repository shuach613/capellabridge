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


def _native_text(item) -> str:
    text = item.find('text')
    return ' '.join(''.join(text.itertext()).split()) if text is not None else ''


def _cap_title_candidate(label: str) -> bool:
    folded = label.casefold().strip()
    if len(folded) < 4 or folded in _CAP_NON_TITLE:
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


def _hide_rest_only_staves(score, changes: dict) -> None:
    for staff_index, staff in enumerate(score.findall('Staff'), 1):
        if staff.find('.//Chord') is not None or staff.find('.//Note') is not None:
            continue
        if staff.find('.//Rest') is None:
            continue
        visible = staff.find('visible')
        if visible is None:
            visible = ET.SubElement(staff, 'visible')
        if visible.text != '0':
            visible.text = '0'
            changes['hidden_staves'].append(staff_index)
            changes['repairs'].append(
                f'Hidden rest-only imported CAP staff {staff_index}'
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
    changes = {'repairs': [], 'hidden_staves': [], 'chord_annotations': 0}
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
                _hide_rest_only_staves(score, changes)
                _enable_native_text_autoplace(score, changes)
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
