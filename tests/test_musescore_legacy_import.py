"""Keep binary CAP importer selection isolated from the CAPX pipeline."""
from pathlib import Path

import app.musescore as musescore


def _fake_run(calls):
    def run(*argv, **kwargs):
        calls.append((argv, kwargs))
        target = Path(argv[2])
        target.write_bytes(b'x' * 128)
    return run


def test_binary_cap_uses_pinned_legacy_importer_then_current_engine(
        tmp_path, monkeypatch):
    source = tmp_path / 'source.cap'
    source.write_bytes(b'cap3-v:a')
    target = tmp_path / 'result.mscz'
    legacy = tmp_path / 'MuseScore2'
    legacy.write_bytes(b'legacy')
    calls = []
    monkeypatch.setenv('MUSESCORE_LEGACY_CAP_BIN', str(legacy))
    monkeypatch.setattr(musescore, 'musescore_path', lambda: '/usr/bin/musescore3')
    monkeypatch.setattr(musescore, 'run', _fake_run(calls))

    musescore.import_cap(source, target)

    assert calls[0][0][0] == str(legacy)
    assert calls[0][0][3] == source
    assert calls[1][0][0] == '/usr/bin/musescore3'
    assert Path(calls[1][0][3]).suffix == '.mscz'
    assert target.read_bytes() == b'x' * 128


def test_binary_cap_falls_back_to_current_engine_without_legacy_configuration(
        tmp_path, monkeypatch):
    source = tmp_path / 'source.cap'
    source.write_bytes(b'cap3-v:a')
    target = tmp_path / 'result.mscz'
    calls = []
    monkeypatch.delenv('MUSESCORE_LEGACY_CAP_BIN', raising=False)
    monkeypatch.setattr(musescore, 'musescore_path', lambda: '/usr/bin/musescore3')
    monkeypatch.setattr(musescore, 'run', _fake_run(calls))

    musescore.import_cap(source, target)

    assert len(calls) == 1
    assert calls[0][0][0] == '/usr/bin/musescore3'
    assert calls[0][0][3] == source
