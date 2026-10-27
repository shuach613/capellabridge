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


def test_crashing_legacy_importer_falls_back_per_file_and_keeps_diagnostic(
        tmp_path, monkeypatch):
    source = tmp_path / 'bogey.cap'
    source.write_bytes(b'cap3-v:a')
    target = tmp_path / 'bogey.mscz'
    legacy = tmp_path / 'MuseScore2'
    legacy.write_bytes(b'legacy')
    calls = []

    def run(*argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == str(legacy):
            raise musescore.ConversionFailure(
                'MuseScore failed (exit 139): invalid slur anchor',
                returncode=139,
                diagnostic='invalid slur anchor',
            )
        Path(argv[2]).write_bytes(b'x' * 128)

    monkeypatch.setenv('MUSESCORE_LEGACY_CAP_BIN', str(legacy))
    monkeypatch.setattr(musescore, 'musescore_path', lambda: '/usr/bin/musescore3')
    monkeypatch.setattr(musescore, 'run', run)

    result = musescore.import_cap(source, target)

    assert result['strategy'] == 'musescore3-cap-fallback'
    assert result['fallback'] is True
    assert result['legacy_returncode'] == 139
    assert 'invalid slur anchor' in result['legacy_error']
    assert calls[1][0][0] == str(legacy)
    assert Path(calls[1][0][2]).suffix == '.mscx'
    assert calls[2][0][0] == '/usr/bin/musescore3'
    assert calls[2][0][3] == source
    assert target.read_bytes() == b'x' * 128


def test_crashing_compressed_writer_retries_uncompressed_legacy_import(
        tmp_path, monkeypatch):
    source = tmp_path / 'recoverable.cap'
    source.write_bytes(b'cap3-v:a')
    target = tmp_path / 'recoverable.mscz'
    legacy = tmp_path / 'MuseScore2'
    legacy.write_bytes(b'legacy')
    calls = []

    def run(*argv, **kwargs):
        calls.append((argv, kwargs))
        output = Path(argv[2])
        if argv[0] == str(legacy) and output.suffix == '.mscz':
            raise musescore.ConversionFailure(
                'compressed writer crashed', returncode=139)
        output.write_bytes(b'x' * 128)

    monkeypatch.setenv('MUSESCORE_LEGACY_CAP_BIN', str(legacy))
    monkeypatch.setattr(musescore, 'musescore_path', lambda: '/usr/bin/musescore3')
    monkeypatch.setattr(musescore, 'run', run)

    result = musescore.import_cap(source, target)

    assert result['strategy'] == 'musescore2-cap-to-mscx-to-musescore3-mscz'
    assert result['fallback'] is True
    assert Path(calls[1][0][2]).suffix == '.mscx'
    assert Path(calls[2][0][3]).suffix == '.mscx'
    assert target.read_bytes() == b'x' * 128


def test_unusable_legacy_intermediate_also_falls_back_to_current_engine(
        tmp_path, monkeypatch):
    source = tmp_path / 'source.cap'
    source.write_bytes(b'cap3-v:a')
    target = tmp_path / 'result.mscz'
    legacy = tmp_path / 'MuseScore2'
    legacy.write_bytes(b'legacy')
    calls = []

    def run(*argv, **kwargs):
        calls.append((argv, kwargs))
        output = Path(argv[2])
        output.write_bytes(b'bad' if argv[0] == str(legacy) else b'x' * 128)

    monkeypatch.setenv('MUSESCORE_LEGACY_CAP_BIN', str(legacy))
    monkeypatch.setattr(musescore, 'musescore_path', lambda: '/usr/bin/musescore3')
    monkeypatch.setattr(musescore, 'run', run)

    result = musescore.import_cap(source, target)

    assert result['strategy'] == 'musescore3-cap-fallback'
    assert 'did not create a usable MSCZ' in result['legacy_error']
    assert calls[1][0][3] == source
    assert target.read_bytes() == b'x' * 128
