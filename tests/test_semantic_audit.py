"""Playback-level source/native equivalence tests."""
from pathlib import Path
import zipfile

import pytest
from lxml import etree as ET

from app.semantic_audit import (
    SemanticIntegrityError,
    _native_volta_spans,
    audit_cap_musicxml_export,
    audit_musicxml_against_mscz,
)


def native_zip(path: Path, xml: str):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("score.mscx", xml.encode())


SOURCE = """<?xml version="1.0"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piccolo</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>480</divisions><time><beats>2</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>480</duration><voice>1</voice>
        <notations><articulations><accent/></articulations><slur type="start" number="1"/></notations>
      </note>
      <note print-object="no"><grace/><rest/><voice>1</voice></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>480</duration><voice>1</voice>
        <notations><articulations><strong-accent/></articulations><slur type="stop" number="1"/></notations>
      </note>
      <barline location="right"><repeat direction="backward"/></barline>
    </measure>
  </part>
</score-partwise>"""


def native(score_note="<pitch>84</pitch>"):
    return f"""<museScore version="3.02"><Score>
      <Part><Staff id="1"/><Instrument><transposeChromatic>12</transposeChromatic></Instrument></Part>
      <Staff id="1"><Measure len="1/4"><voice>
        <TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>
        <Chord><durationType>quarter</durationType><Articulation><subtype>articAccentAbove</subtype></Articulation>
          <Spanner type="Slur"><Slur/><next/></Spanner><Note>{score_note}</Note></Chord>
        <Chord><durationType>quarter</durationType><Articulation><subtype>articMarcatoAbove</subtype></Articulation>
          <Spanner type="Slur"><prev/></Spanner><Note><pitch>86</pitch></Note></Chord>
      </voice><endRepeat/></Measure></Staff>
    </Score></museScore>"""


def test_native_aliases_and_transposition_compare_as_music(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native())
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_events"] == result["native_events"] == 2
    assert result["ignored_grace_rests"] == 1
    assert result["source_repeats"] == result["native_repeats"] == 1


def test_semantic_audit_rejects_pitch_changes(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native("<pitch>73</pitch>"))
    with pytest.raises(SemanticIntegrityError, match="Musical event changed"):
        audit_musicxml_against_mscz(source, output)


def test_semantic_audit_rejects_unmatched_source_rest(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        "</measure>",
        '<note print-object="no"><rest/><duration>30</duration><voice>1</voice></note></measure>',
        1,
    ), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native())
    # The additional source rest has no native counterpart; the audit must
    # remain strict rather than treating arbitrary silence as harmless.
    with pytest.raises(SemanticIntegrityError):
        audit_musicxml_against_mscz(source, output)


def test_fermata_is_a_playback_relevant_native_mark(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<articulations><accent/></articulations>',
        '<fermata type="upright"/>',
    ), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_xml = native().replace(
        '<subtype>articAccentAbove</subtype>',
        '<subtype>articFermataAbove</subtype>',
    )
    native_zip(output, native_xml)
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_events"] == result["native_events"] == 2


def test_standalone_native_fermata_is_attached_to_the_next_event(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<articulations><accent/></articulations>',
        '<fermata type="upright"/>',
    ), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_xml = native().replace(
        '<Chord><durationType>quarter</durationType><Articulation><subtype>articAccentAbove</subtype></Articulation>',
        '<Fermata><subtype>fermataAbove</subtype></Fermata><Chord><durationType>quarter</durationType>',
    )
    native_zip(output, native_xml)
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_events"] == result["native_events"] == 2


def test_musescore3_inverted_native_name_means_musicxml_mordent(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<articulations><accent/></articulations>',
        '<ornaments><mordent/></ornaments>',
    ), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_xml = native().replace(
        '<subtype>articAccentAbove</subtype>',
        '<subtype>ornamentMordentInverted</subtype>',
    )
    native_zip(output, native_xml)
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_events"] == result["native_events"] == 2

    source.write_text(SOURCE.replace(
        '<articulations><accent/></articulations>',
        '<ornaments><inverted-mordent/></ornaments>',
    ), encoding="utf-8")
    native_zip(output, native().replace(
        '<subtype>articAccentAbove</subtype>',
        '<subtype>ornamentMordent</subtype>',
    ))
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_events"] == result["native_events"] == 2


def test_dynamics_hairpins_and_tempo_are_compared_at_exact_onsets(tmp_path):
    source = tmp_path / "source.musicxml"
    direction = (
        '<direction><direction-type><dynamics><f/></dynamics>'
        '<wedge type="crescendo"/></direction-type><voice>1</voice>'
        '<sound tempo="90"/></direction>'
    )
    source.write_text(SOURCE.replace('<note>', direction + '<note>', 1),
                      encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_xml = native().replace(
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>',
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'
        '<Dynamic><subtype>f</subtype></Dynamic>'
        '<Tempo><tempo>1.5</tempo></Tempo>'
        '<Spanner type="HairPin"><HairPin><subtype>0</subtype></HairPin></Spanner>',
    )
    native_zip(output, native_xml)
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_dynamics"] == result["native_dynamics"] == 1
    assert result["source_hairpins"] == result["native_hairpins"] == 1
    assert result["source_tempos"] == result["native_tempos"] == 1

    # Native MuseScore stores BPM as quarter notes per second. Harmless
    # floating-point serialization noise must not reject the score.
    native_zip(output, native_xml.replace(
        '<Tempo><tempo>1.5</tempo></Tempo>',
        '<Tempo><tempo>1.4999966667</tempo></Tempo>'))
    audit_musicxml_against_mscz(source, output)

    native_zip(output, native_xml.replace(
        '<Dynamic><subtype>f</subtype></Dynamic>', ''))
    with pytest.raises(SemanticIntegrityError, match="Dynamics changed"):
        audit_musicxml_against_mscz(source, output)


def test_hairpin_voice_reassignment_is_staff_equivalent(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<note>',
        '<direction><direction-type><wedge type="crescendo"/>'
        '</direction-type><voice>2</voice></direction><note>', 1),
        encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native().replace(
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>',
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'
        '<Spanner type="HairPin"><HairPin><subtype>0</subtype>'
        '</HairPin></Spanner>'))
    audit_musicxml_against_mscz(source, output)


def test_native_trill_spanner_counts_as_trill_mark(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<articulations><accent/></articulations>',
        '<ornaments><trill-mark/></ornaments>'), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native().replace(
        '<Articulation><subtype>articAccentAbove</subtype></Articulation>',
        '<Spanner type="Trill"><Trill><subtype>trill</subtype></Trill>'
        '<next/></Spanner>'))
    audit_musicxml_against_mscz(source, output)


def test_score_global_tempo_can_move_between_parts_with_small_rounding(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<note>', '<direction><sound tempo="100"/></direction><note>', 1),
        encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, native().replace(
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>',
        '<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'
        '<Tempo><tempo>1.6665</tempo></Tempo>'))
    audit_musicxml_against_mscz(source, output)


def test_volta_starts_are_checked_as_semantic_boundaries(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<barline location="right"><repeat direction="backward"/></barline>',
        '<barline location="left"><ending number="1" type="start">1.</ending></barline>'
        '<barline location="right"><ending number="1" type="stop"/><repeat direction="backward"/></barline>',
    ), encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_xml = native().replace(
        '</voice><endRepeat/>',
        '<Spanner type="Volta"><Volta><endings>1</endings></Volta></Spanner></voice><endRepeat/>',
    )
    native_zip(output, native_xml)
    result = audit_musicxml_against_mscz(source, output)
    assert result["source_volta_starts"] == result["native_volta_starts"] == 1
    assert result["source_volta_spans"] == result["native_volta_spans"] == 1

    native_zip(output, native_xml.replace(
        '</Volta></Spanner>',
        '</Volta><next><location><measures>2</measures></location></next></Spanner>'))
    with pytest.raises(SemanticIntegrityError, match="Volta spans changed"):
        audit_musicxml_against_mscz(source, output)


def test_terminal_volta_connector_does_not_extend_single_measure_ending():
    staff = ET.fromstring("""
      <Staff id="1">
        <Measure><voice>
          <Spanner type="Volta"><Volta><endings>2</endings></Volta>
            <next><location><measures>1</measures></location></next>
          </Spanner>
        </voice></Measure>
        <Measure><voice>
          <Spanner type="Volta"><prev><location><measures>-1</measures>
          </location></prev></Spanner>
        </voice></Measure>
      </Staff>
    """)

    assert _native_volta_spans(staff) == {(1, 1, '2')}


def test_measure_level_native_navigation_is_audited(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text(SOURCE.replace(
        '<note>',
        '<direction><direction-type><coda/></direction-type>'
        '<sound coda="coda"/></direction><note>',1),encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_xml = native().replace(
        '<Staff id="1"><Measure len="1/4">',
        '<Staff id="1"><Measure len="1/4"><Marker>'
        '<text>&lt;sym&gt;coda&lt;/sym&gt;</text><label>codab</label>'
        '</Marker>',1)
    native_zip(output,native_xml)
    result=audit_musicxml_against_mscz(source,output)
    assert result["source_navigation"]==result["native_navigation"]==1


def test_semantic_audit_carries_tuplets_across_native_barlines(tmp_path):
    source = tmp_path / "source.musicxml"
    source.write_text("""<?xml version="1.0"?>
    <score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Piccolo</part-name></score-part></part-list>
      <part id="P1">
        <measure number="1">
          <attributes><divisions>6</divisions><time><beats>1</beats><beat-type>6</beat-type></time></attributes>
          <note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice>
            <notations><tuplet type="start" number="1"/></notations>
          </note>
          <note><rest/><duration>2</duration><voice>1</voice></note>
        </measure>
        <measure number="2">
          <note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice>
            <notations><tuplet type="stop" number="1"/></notations>
          </note>
          <note><rest/><duration>2</duration><voice>1</voice></note>
        </measure>
      </part>
    </score-partwise>""", encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, """<museScore version="3.02"><Score>
      <Staff id="1">
        <Measure len="1/6"><voice><TimeSig><sigN>1</sigN><sigD>6</sigD></TimeSig>
          <Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>
          <Chord><duration>1/8</duration><durationType>eighth</durationType><Note><pitch>60</pitch></Note></Chord>
          <Rest><duration>1/8</duration><durationType>eighth</durationType></Rest>
        </voice></Measure>
        <Measure len="1/6"><voice>
          <Chord><duration>1/8</duration><durationType>eighth</durationType><Note><pitch>62</pitch></Note></Chord>
          <endTuplet/>
          <Rest><duration>1/12</duration><durationType>eighth</durationType></Rest>
        </voice></Measure>
      </Staff>
    </Score></museScore>"""
    )

    result = audit_musicxml_against_mscz(source, output)

    assert result["source_events"] == result["native_events"] == 4
    assert result["native_open_tuplets"] == 0


def test_cap_export_audit_ignores_voice_renumbering_but_rejects_lost_events(tmp_path):
    source = tmp_path / "export.musicxml"
    source.write_text("""<?xml version="1.0"?>
    <score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Piccolo</part-name></score-part></part-list>
      <part id="P1"><measure number="1">
        <attributes><divisions>1</divisions><time><beats>2</beats><beat-type>4</beat-type></time></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><voice>5</voice></note>
        <note><rest/><duration>1</duration><voice>5</voice></note>
      </measure></part>
    </score-partwise>""", encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, """<museScore version="3.02"><Score>
      <Staff id="1"><Measure len="1/2"><voice>
        <TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>
        <Chord><durationType>quarter</durationType><Note><pitch>60</pitch></Note></Chord>
        <Rest><durationType>quarter</durationType></Rest>
      </voice></Measure></Staff>
    </Score></museScore>""")

    result = audit_cap_musicxml_export(source, output)

    assert result["source_events"] == result["native_events"] == 2

    native_zip(output, """<museScore version="3.02"><Score>
      <Staff id="1"><Measure len="1/2"><voice>
        <TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>
      </voice></Measure></Staff>
    </Score></museScore>""")
    with pytest.raises(SemanticIntegrityError, match="sounding events changed"):
        audit_cap_musicxml_export(source, output)


def test_cap_export_audit_allows_coalesced_silent_rests(tmp_path):
    source = tmp_path / "export.musicxml"
    source.write_text("""<?xml version="1.0"?>
    <score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Piccolo</part-name></score-part></part-list>
      <part id="P1"><measure number="1">
        <attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
        <note><rest/><duration>4</duration><voice>1</voice></note>
        <note><rest/><duration>8</duration><voice>1</voice></note>
      </measure></part>
    </score-partwise>""", encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, """<museScore version="3.02"><Score>
      <Staff id="1"><Measure len="1/1"><voice>
        <Chord><duration>1/4</duration><durationType>quarter</durationType><Note><pitch>60</pitch></Note></Chord>
        <Rest><duration>3/4</duration><durationType>half</durationType></Rest>
      </voice></Measure></Staff>
    </Score></museScore>""")

    result = audit_cap_musicxml_export(source, output)

    assert result["source_chords"] == result["native_chords"] == 1
    assert result["source_rests"] == 2
    assert result["native_rests"] == 1
    assert result["rest_representation_normalized"] is True


def test_cap_export_audit_treats_print_visibility_as_engraving_only(tmp_path):
    source = tmp_path / "export.musicxml"
    source.write_text("""<?xml version="1.0"?>
    <score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Gitarre</part-name></score-part></part-list>
      <part id="P1"><measure number="1">
        <attributes><divisions>1</divisions></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice></note>
      </measure></part>
    </score-partwise>""", encoding="utf-8")
    output = tmp_path / "score.mscz"
    native_zip(output, """<museScore version="3.02"><Score>
      <Staff id="1"><Measure len="1/4"><voice>
        <Chord><durationType>quarter</durationType><visible>0</visible>
          <Note><pitch>60</pitch><visible>0</visible></Note></Chord>
      </voice></Measure></Staff>
    </Score></museScore>""")

    result = audit_cap_musicxml_export(source, output)

    assert result["source_chords"] == result["native_chords"] == 1
    assert result["hidden_representation_normalized"] is True


def test_cap_export_audit_maps_multistaff_part_to_native_staffs(tmp_path):
    """A CAP export may use one MusicXML part for a four-staff ensemble."""
    source = tmp_path / "multistaff.musicxml"
    source.write_text("""<?xml version="1.0"?>
    <score-partwise version="4.0">
      <part-list><score-part id="P1"><part-name>Ensemble</part-name></score-part></part-list>
      <part id="P1"><measure number="1">
        <attributes><divisions>1</divisions><staves>2</staves><transpose>
          <chromatic>12</chromatic><octave-change>1</octave-change>
        </transpose></attributes>
        <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><staff>1</staff></note>
        <backup><duration>1</duration></backup>
        <note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><voice>5</voice><staff>2</staff></note>
      </measure></part>
    </score-partwise>""", encoding="utf-8")
    output = tmp_path / "multistaff.mscz"
    native_zip(output, """<museScore version="3.02"><Score>
      <Part><Staff id="1"/><Staff id="2"/><Instrument><transposeChromatic>12</transposeChromatic></Instrument></Part>
      <Staff id="1"><Measure len="1/4"><voice><Chord><durationType>quarter</durationType><Note><pitch>72</pitch></Note></Chord></voice></Measure></Staff>
      <Staff id="2"><Measure len="1/4"><voice><Chord><durationType>quarter</durationType><Note><pitch>76</pitch></Note></Chord></voice></Measure></Staff>
    </Score></museScore>""")

    result = audit_cap_musicxml_export(source, output)

    assert result["source_chords"] == result["native_chords"] == 2
