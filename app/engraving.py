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
    # The MusicXML importer otherwise adds a copyright footer on every page
    # in addition to the explicit source copyright near the score title.
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
            source_info=metadata or {}
            preferences=dict(STYLE_VALUES)
            if source_info.get('variable_staff_systems'):
                # MuseScore 3 ignores some MusicXML staff-details visibility
                # changes; native hide-empty style is required for parts
                # that enter only in later systems (e.g. Waggis P4).
                preferences['hideEmptyStaves']='1'
                preferences['dontHidStavesInFirstSystm']='0'
            if source_info.get('source_multi_measure_rests'):
                preferences['createMultiMeasureRests']='1'
                preferences['minEmptyMeasures']='2'
            for key, value in preferences.items():
                node = style.find(key)
                if node is None:
                    node = ET.SubElement(style, key)
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
                    # When source title or subtitle already prints the
                    # composer's name, don't re-insert a duplicate native
                    # Composer frame (despite MusicXML suppressing that frame).
                    normalize=lambda x: ''.join(ch.casefold() for ch in x
                                                  if ch.isalnum())
                    person=normalize(metadata.get('composer',''))
                    already_printed=person and any(
                        person in normalize(metadata.get(field,''))
                        for field in ('title','subtitle'))
                    if metadata.get('composer') and not already_printed:
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
            # MuseScore's MusicXML importer drops many explicit per-word
            # colors. Restore only colors explicitly present in the source
            # Capella annotations, never recolor unrelated score objects.
            source_colors=(metadata or {}).get('source_text_colors') or {}
            if source_colors:
                normalized_colors={
                    ' '.join(label.split()):rgb
                    for label,rgb in source_colors.items()
                    if re.fullmatch(r'#[0-9A-Fa-f]{6}',rgb)
                }
                for text_node in score.xpath('.//StaffText | .//SystemText'):
                    rich=text_node.find('text')
                    if rich is None:
                        continue
                    label=' '.join(''.join(rich.itertext()).split())
                    rgb=normalized_colors.get(label)
                    if rgb is None:
                        continue
                    node=text_node.find('color')
                    if node is None:
                        node=ET.Element('color')
                        text_node.insert(0,node)
                    node.attrib.clear()
                    node.attrib.update({'r':str(int(rgb[1:3],16)),
                                        'g':str(int(rgb[3:5],16)),
                                        'b':str(int(rgb[5:7],16)),
                                        'a':'255'})
            # MusicXML-to-MuseScore loses RGB attributes on imported
            # dynamic symbols. Restore colors only when every source instance
            # of that particular dynamic agreed on one non-default color.
            def set_native_color(node, rgb):
                if not isinstance(rgb,str) or not re.fullmatch(
                        r'#[0-9A-Fa-f]{6}',rgb):
                    return
                color=node.find('color')
                if color is None:
                    color=ET.Element('color')
                    node.insert(0,color)
                color.attrib.clear()
                color.attrib.update({
                    'r':str(int(rgb[1:3],16)),
                    'g':str(int(rgb[3:5],16)),
                    'b':str(int(rgb[5:7],16)),
                    'a':'255'})
            for dynamic in score.xpath('.//Dynamic'):
                typ=dynamic.findtext('subtype','')
                set_native_color(dynamic,source_info.get(
                    'source_dynamic_colors',{}).get(typ))
            for wedge in score.xpath('.//HairPin'):
                set_native_color(wedge,source_info.get('source_wedge_color'))
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
