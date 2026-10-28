from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_patched_legacy_decoder_is_pinned_and_built_from_source():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "MuseScore/archive/refs/tags/v2.3.2.tar.gz" in dockerfile
    assert "8d84a081ca03bdb0bbf706c2848064f27c3a6331033e313954133d44fb769aee" in dockerfile
    assert "musescore-2.3.2-cap-spanner-safety.patch" in dockerfile
    assert "/opt/musescore2/bin/mscore" in dockerfile
    assert "DOWNLOAD_SOUNDFONT=OFF" in dockerfile


def test_cap_patch_repairs_only_terminal_voltas_and_rejects_bad_spanners():
    patch = (ROOT / "patches/musescore-2.3.2-cap-spanner-safety.patch").read_text()
    assert "o->type == CapellaType::VOLTA" in patch
    assert "tick2 >= lastMeasure->endTick()" in patch
    assert "candidate->tick() <= tick2" in patch
    assert "cr1->score() != score || cr2->score() != score" in patch
    assert "cr1->track() != track || cr2->track() != track" in patch
    assert "cr2->tick() < cr1->tick()" in patch
    assert "slur anchor is not a chord" in patch
    assert "cr1 == cr2 || cr1->tick() >= cr2->tick()" in patch


def test_legacy_wrapper_executes_the_patched_binary():
    wrapper = (ROOT / "scripts/musescore2-cap").read_text()
    assert "/opt/musescore2/bin/mscore" in wrapper
    assert "/opt/musescore2/AppRun" not in wrapper
