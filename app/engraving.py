"""Apply portable MuseScore 3 print styles to editable MSCZ archives.

MuseScore's CLI importer may ignore MusicXML measure-numbering on a score.
Editing the *native score Style* guarantees that the exported MSCZ, PDF
and PNG previews all share the same staff/measure numbering preferences.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from lxml import etree as ET

STYLE_VALUES = {
    'showMeasureNumber': '1',
    'showMeasureNumberOne': '0',
    'measureNumberSystem': '1',
    'measureNumberAllStaffs': '0',
}

def prepare_print_ready(mscz_path: Path) -> None:
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
                node.text = value
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
