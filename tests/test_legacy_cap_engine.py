import hashlib
import zipfile
from pathlib import Path

import pytest

from app.legacy_cap import CAP_SIGNATURE, LegacyCapError, inspect_cap, inspect_native_mscz


def write_native_score(path: Path, body: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "score.mscx",
            f'<museScore version="3.02"><Score>{body}</Score></museScore>',
        )


def test_legacy_cap_signature_and_provenance(tmp_path):
    source = tmp_path / "score.cap"
    source.write_bytes(CAP_SIGNATURE + b"\x00\x01payload")

    result = inspect_cap(source)

    assert result["format"] == "cap"
    assert result["signature"] == "cap3-v:a"
    assert result["size_bytes"] == source.stat().st_size
    assert result["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_unknown_legacy_cap_signature_is_rejected(tmp_path):
    source = tmp_path / "unknown.cap"
    source.write_bytes(b"not-a-cap-file")

    with pytest.raises(LegacyCapError, match="Unsupported legacy CAP signature"):
        inspect_cap(source)


def test_native_inventory_records_music_and_review_risks(tmp_path):
    score = tmp_path / "score.mscz"
    write_native_score(
        score,
        """
        <Staff id="1">
          <Measure len="1/1">
            <voice>
              <Chord><Note><pitch><octave>4</octave></pitch><visible>0</visible></Note>
              </Chord>
              <Rest><durationType>whole</durationType></Rest>
              <Slur/><HairPin/><Dynamic/><Tempo/><TimeSig/><KeySig/>
              <Volta/><Jump/><Marker/><Articulation/><Symbol/><Lyrics/>
            </voice>
          </Measure>
        </Staff>
        """,
    )

    result = inspect_native_mscz(score)
    counts = result["counts"]

    assert counts["staves"] == 1
    assert counts["measures"] == 1
    assert counts["chords"] == 1
    assert counts["notes"] == 1
    assert counts["rests"] == 1
    assert counts["hidden_pitched_events"] == 1
    assert counts["slurs"] == 1
    assert counts["hairpins"] == 1
    assert counts["dynamics"] == 1
    assert counts["tempos"] == 1
    assert counts["time_signatures"] == 1
    assert counts["key_signatures"] == 1
    assert counts["voltas"] == 1
    assert counts["jumps"] == 1
    assert counts["markers"] == 1
    assert counts["articulations"] == 1
    assert counts["ornaments"] == 1
    assert counts["lyrics"] == 1
    assert result["warnings"]
    assert "hidden pitched" in result["warnings"][0]


def test_cap_processes_native_first_and_exports_secondarily(monkeypatch, tmp_path):
    from app import main

    source = CAP_SIGNATURE + b"\x00\x01payload"
    calls = []

    def fake_import(source_path, target_path):
        calls.append(("import", Path(source_path).suffix, Path(target_path).suffix))
        write_native_score(
            Path(target_path),
            '<Staff id="1"><Measure len="1/1"><voice>'
            '<Rest><durationType>whole</durationType></Rest>'
            '</voice></Measure></Staff>',
        )

    def fake_musicxml(source_path, target_path):
        calls.append(("musicxml", Path(source_path).suffix, Path(target_path).suffix))
        Path(target_path).write_text("<score-partwise/>", encoding="utf-8")

    def fake_pdf(source_path, target_path):
        calls.append(("pdf", Path(source_path).suffix, Path(target_path).suffix))
        Path(target_path).write_bytes(b"%PDF-1.4\n%test")

    def fake_preview(_pdf, png):
        Path(png).write_bytes(b"PNG")

    monkeypatch.setattr(main, "import_cap", fake_import)
    monkeypatch.setattr(main, "export_musicxml", fake_musicxml)
    monkeypatch.setattr(main, "export_pdf", fake_pdf)
    monkeypatch.setattr(main, "create_preview", fake_preview)
    monkeypatch.setattr(main, "musescore_path", lambda: "/usr/bin/musescore3")

    result = main.process_score(
        "Legacy score.cap", ".cap", source, tmp_path / "work", "balanced"
    )

    assert result["source_format"] == "cap"
    assert result["import_strategy"] == "native-cap-to-mscz"
    assert result["cap_source"]["signature"] == "cap3-v:a"
    assert result["certification"] == "native-valid"
    assert result["verification"] == "native-valid"
    assert result["verification_scope"] == "native-structural-only"
    assert result["source_audit"]["status"] == "unverified"
    assert result["files"]["mscz"].endswith(".mscz")
    assert result["files"]["musicxml"].endswith(".musicxml")
    assert result["files"]["pdf"].endswith(".pdf")
    assert calls == [
        ("import", ".cap", ".mscz"),
        ("musicxml", ".mscz", ".musicxml"),
        ("pdf", ".mscz", ".pdf"),
    ]
