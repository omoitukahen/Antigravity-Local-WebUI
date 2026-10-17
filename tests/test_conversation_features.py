import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agweb.config import Config
from agweb.server import create_app
from agweb.store import Store
from test_api import wait


@pytest.fixture
def session(tmp_path):
    config = Config(tmp_path, 'features-key', 8823, True)
    app = create_app(config)
    with TestClient(app, base_url=config.origin, headers={'Authorization': 'Bearer features-key'}) as client:
        yield client, app, config


def create(client, instructions=''):
    r = client.post('/api/conversations', json={'model': 'mock-model', 'effort': 'low', 'system_prompt': instructions})
    assert r.status_code == 200, r.text
    return r.json()


def patch(client, c, **fields):
    return client.patch('/api/conversations/' + c['id'], json=fields)


def send(client, c, text='質問'):
    r = client.post('/api/conversations/' + c['id'] + '/runs', json={
        'request_id': str(uuid.uuid4()), 'revision': c['revision'], 'text': text, 'attachment_ids': [], 'timeout': 30})
    assert r.status_code == 200, r.text
    return r.json()


def test_migration_keeps_old_history_and_is_repeatable(tmp_path):
    db = sqlite3.connect(tmp_path / 'history.sqlite3')
    db.execute('CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,model TEXT NOT NULL,effort TEXT NOT NULL,cli_id TEXT,revision INTEGER NOT NULL DEFAULT 0,resumable INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL)')
    db.execute("INSERT INTO conversations(id,title,model,effort,created) VALUES('old','旧会話','mock-model','low',1)")
    db.commit(); db.close()
    for _ in range(2):
        store = Store(tmp_path)
        c = store.conversation('old')
        assert c['title'] == '旧会話' and c['system_prompt'] == '' and not c['is_favorite']
        store.close()


def test_search_rename_favorites_and_restart(session):
    client, app, config = session
    a = create(client, 'INSTRUCTION_ONLY')
    b = create(client)
    assert patch(client, a, title='  手動名 %_ 📄  ', is_favorite=True).status_code == 200
    a = client.get('/api/conversations/' + a['id']).json()['conversation']
    result = send(client, a, '日本語 STRASSE %_ 途中')
    run, detail = wait(client, result['run'], result['owner'])
    assert detail['conversation']['title'] == '手動名 %_ 📄'
    for term in ['日本語', 'straße', '%_', 'こんにちは']:
        found = client.get('/api/conversations', params={'q': term}).json()
        assert [c['id'] for c in found] == [a['id']], term
        assert all('system_prompt' not in c and 'response' not in c and 'body' not in c for c in found)
    assert client.get('/api/conversations', params={'q': 'INSTRUCTION_ONLY'}).json() == []
    assert client.get('/api/conversations', params={'q': '%_ missing'}).json() == []
    assert [c['id'] for c in client.get('/api/conversations').json()] == [a['id'], b['id']]
    # Reopen the real persistence boundary in the API server's thread.
    def reopen():
        app.state.store.close()
        app.state.store = Store(config.data)
    client.portal.call(reopen)
    persisted = client.get('/api/conversations/' + a['id']).json()
    assert persisted['conversation']['is_favorite'] and persisted['conversation']['system_prompt'] == 'INSTRUCTION_ONLY'
    assert persisted['runs'][0]['response'] == run['response']


@pytest.mark.parametrize('fields', [{'title': ''}, {'title': ' \n '}, {'title': 'a\nb'}, {'title': 'a' * 101}, {'is_favorite': 1}, {'unknown': True}, {'system_prompt': '@[secret](C:/secret.txt)', 'revision': 0}, {'system_prompt': '日' * 8001, 'revision': 0}, {'system_prompt': 'changed'}])
def test_invalid_metadata_does_not_change_conversation(session, fields):
    client, _, _ = session
    c = create(client)
    assert patch(client, c, **fields).status_code in {400, 422}
    assert client.get('/api/conversations/' + c['id']).json()['conversation'] == c


def test_instruction_revision_lock_and_workspace_isolation(session):
    client, app, config = session
    c = create(client)
    updated = patch(client, c, system_prompt='日本語で簡潔に。\n改行を保持 📄', revision=c['revision'])
    assert updated.status_code == 200, updated.text
    c = updated.json()
    assert patch(client, c, system_prompt='競合', revision=0).status_code == 409
    result = send(client, c)
    assert patch(client, c, system_prompt='送信後', revision=c['revision']).status_code == 409
    run, detail = wait(client, result['run'], result['owner'])
    path = config.data / 'workspaces' / c['id'] / 'AGENTS.md'
    assert path.read_bytes() == c['system_prompt'].encode('utf-8')
    again = send(client, detail['conversation'], '続き')
    _, detail = wait(client, again['run'], again['owner'])
    assert path.read_bytes() == c['system_prompt'].encode('utf-8')
    other = create(client)
    result = send(client, other)
    wait(client, result['run'], result['owner'])
    assert not (config.data / 'workspaces' / other['id'] / 'AGENTS.md').exists()
    assert patch(client, detail['conversation'], system_prompt='', revision=detail['conversation']['revision']).status_code == 409


def test_instruction_counts_toward_input_limit_and_write_failure_never_spawns(session, monkeypatch):
    client, app, config = session
    c = create(client, '指示')
    body = {'request_id': str(uuid.uuid4()), 'revision': c['revision'], 'text': 'a' * 19999, 'attachment_ids': [], 'timeout': 30}
    assert client.post('/api/conversations/' + c['id'] + '/runs', json=body).status_code == 400
    original = Path.replace
    def fail(self, *args, **kwargs):
        if self.name.startswith('AGENTS.md'): raise OSError('blocked')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'replace', fail)
    r = client.post('/api/conversations/' + c['id'] + '/runs', json={**body, 'text': '質問'})
    assert r.status_code == 500
    detail = client.get('/api/conversations/' + c['id']).json()
    assert detail['runs'][0]['reason'] == 'launch_failed' and not detail['runs'][0]['submitted']
    assert app.state.controller.proc is None
