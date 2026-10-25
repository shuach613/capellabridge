"""Playback-level source/native equivalence tests."""
from pathlib import Path
import zipfile

import pytest

from app.semantic_audit import (
    SemanticIntegrityError,
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

    native_zip(output, native_xml.replace(
        '<Dynamic><subtype>f</subtype></Dynamic>', ''))
    with pytest.raises(SemanticIntegrityError, match="Dynamics changed"):
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
