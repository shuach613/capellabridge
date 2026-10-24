"""Native MuseScore style regressions for printing previously failing scores."""
from io import BytesIO
from pathlib import Path
import zipfile
from lxml import etree as ET
from app.engraving import prepare_print_ready


def test_native_style_hides_late_entering_part_and_restores_printed_colors(tmp_path):
    score=tmp_path/'sample.mscz'
    native=('<?xml version="1.0"?><museScore version="3.01"><Score>'
            '<Style><showFooter>1</showFooter></Style>'
            '<Part><Staff id="1"/></Part>'
            '<Staff id="1"><Measure><voice>'
            '<StaffText><text>1 Solo</text></StaffText>'
            '</voice></Measure></Staff></Score></museScore>')
    with zipfile.ZipFile(score,'w') as archive:
        archive.writestr('score.mscx',native)
    metadata={
        'variable_staff_systems':True,
        'source_multi_measure_rests':2,
        'source_text_colors':{'1 Solo':'#0000FF'},
    }
    prepare_print_ready(score,metadata)
    with zipfile.ZipFile(score) as archive:
        root=ET.fromstring(archive.read('score.mscx'))
    style=root.find('./Score/Style')
    assert style.findtext('hideEmptyStaves')=='1'
    assert style.findtext('dontHidStavesInFirstSystm')=='0'
    assert style.findtext('createMultiMeasureRests')=='1'
    assert style.findtext('showFooter')=='0'
    color=root.find('./Score/Staff/Measure/voice/StaffText/color')
    assert color is not None
    assert color.attrib=={'r':'0','g':'0','b':'255','a':'255'}


def test_unchanging_staff_layout_does_not_hide_empty_staves_globally(tmp_path):
    score=tmp_path/'fixed.mscz'
    with zipfile.ZipFile(score,'w') as archive:
        archive.writestr('score.mscx',
            '<museScore version="3.01"><Score><Style/></Score></museScore>')
    prepare_print_ready(score,{'variable_staff_systems':False})
    with zipfile.ZipFile(score) as archive:
        root=ET.fromstring(archive.read('score.mscx'))
    assert root.find('./Score/Style/hideEmptyStaves') is None
    assert root.findtext('./Score/Style/showFooter')=='0'
