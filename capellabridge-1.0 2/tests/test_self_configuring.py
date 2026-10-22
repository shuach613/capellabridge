"""No environment file is needed to bootstrap and persist administrator login."""
import json
from fastapi.testclient import TestClient
from app import main


def test_first_start_creates_and_reuses_credentials(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(main,'BASE',tmp_path)
    monkeypatch.setattr(main,'IDENTITY',tmp_path/'admin.json')
    monkeypatch.setattr(main,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(main,'PASSWORD','')
    main.initialize_identity()
    credential=json.loads(main.IDENTITY.read_text())
    assert credential['username']=='admin'
    assert 'password' not in credential
    assert len(credential['salt'])==64
    assert len(credential['password_hash'])==64
    old_bytes=main.IDENTITY.read_bytes()
    main.initialize_identity()
    assert main.IDENTITY.read_bytes()==old_bytes
    emitted=' '.join(record.message for record in caplog.records)
    assert 'CAPELLABRIDGE FIRST START' in emitted
    password=next(record.message.split('Password: ')[1] for record in caplog.records if record.message.startswith('Password: '))
    assert main._valid_credentials('admin',password)
    assert not main._valid_credentials('admin','incorrect')
    assert not main._valid_credentials('other',password)

    with TestClient(main.app) as client:
        assert client.get('/').status_code==401
        assert client.get('/',auth=('admin',password)).status_code==200
        assert client.post('/api/account/password',auth=('admin',password),json={
            'current_password':password,'new_password':'A-Secure-Replacement-Passphrase'
        }).status_code==200
        assert client.get('/',auth=('admin',password)).status_code==401
        assert client.get('/',auth=('admin','A-Secure-Replacement-Passphrase')).status_code==200
