"""Apply portable MuseScore 3 print styles to editable MSCZ archives.

MuseScore's CLI importer may ignore MusicXML measure-numbering on a score.
Editing the *native score Style* guarantees that the exported MSCZ, PDF
and PNG previews all share the same staff/measure numbering preferences.
"""
from __future__ import annotations

import os
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

def prepare_print_ready(mscz_path: Path, metadata: dict | None = None) -> None:
    """Update score-wide MuseScore styles, preserving every ZIP member."""
    mscz_path = Path(mscz_path)
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
                if key == 'hideEmptyStaves' and metadata:
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
            # MuseScore's default text fonts cannot display Unicode
            # Musical Symbols U+1D13E / U+1D13F. When Capella encodes a
            # *graphical* rest over sounding tied notes, the MusicXML
            # direction must become a native SMuFL symbol, not a timed
            # Rest event. Native <sym> markup uses MuseScore's installed
            # musical font and survives subsequent native PDF export.
            musical_rest_symbols={
                '\U0001D13E':'rest8th',
                '\U0001D13F':'rest16th',
            }
            for staff_text in score.iter():
                if staff_text.tag not in ('StaffText','SystemText','Text'):
                    continue
                content=staff_text.find('text')
                if content is None:
                    continue
                source=''.join(content.itertext()).strip()
                if source in musical_rest_symbols:
                    content.clear()
                    content.text='<sym>'+musical_rest_symbols[source]+'</sym>'
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
