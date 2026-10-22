"""Musical duration and note-count regression on the sample CAPX."""
import os
from fractions import Fraction
from pathlib import Path
from lxml import etree as ET
import pytest
from app.converter import serialize_musicxml


def test_all_brandywine_measures_have_consistent_rhythm():
    path=os.getenv('BRANDYWINE_CAPX')
    if not path:pytest.skip('Set BRANDYWINE_CAPX')
    score,report=serialize_musicxml(Path(path).read_bytes())
    root=ET.fromstring(score)
    # One 1/8 pickup, then 101 full 2/4 measures.
    for part in root.findall('part'):
        total=0
        for n,measure in enumerate(part.findall('measure')):
            rhythms=sum(int(note.findtext('duration','0')) for note in measure.findall('note'))
            assert rhythms==(5040 if n==0 else 20160),f'{part.get("id")}, measure {n+1}: {rhythms}'
            total+=rhythms
        assert total==5040+101*20160
    groups=root.findall('./part-list/part-group')
    assert len(groups)==2
    assert groups[0].findtext('group-symbol')=='bracket'
