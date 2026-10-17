import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agweb.config import Config
from agweb.server import create_app
from agweb.store import Store


def conversation(store, states=('completed', 'cancelled', 'failed')):
    c = store.create('mock-model', 'low')
    attachment = store.attach(c['id'], '資料.md', '日本語の資料 📄'.encode())
    runs = []
    for state in states:
        run, _ = store.reserve(c['id'], {
            'request_id': str(uuid.uuid4()), 'revision': len(runs),
            'text': '質問 ' + str(len(runs) + 1), 'attachment_ids': [attachment['id']], 'timeout': 30,
        })
        store.event(run['id'], {'kind': 'terminal', 'state': state, 'response': '回答 📄',
                                'submitted': False, 'active': 0, 'exit_code': 0})
        runs.append(run['id'])
    workspace = store.data / 'workspaces' / c['id']
    workspace.mkdir(parents=True)
    (workspace / 'hooks.json').write_text('{}', encoding='utf-8')
    return c, runs


@pytest.fixture
def session(tmp_path):
    config = Config(tmp_path, 'delete-test-key', 8814, True)
    app = create_app(config)
    with TestClient(app, base_url=config.origin, headers={'Authorization': 'Bearer delete-test-key'}) as client:
        yield client, app.state.store, app, config


def test_delete_removes_only_selected_history_and_owned_files(session):
    client, store, app, config = session
    # Seed via the server thread: SQLite writes stay on the same connection/thread.
    target, runs = client.portal.call(conversation, store)
    other, other_runs = client.portal.call(conversation, store, ('completed',))
    path = '/api/conversations/' + target['id']
    assert client.delete(path, headers={'Authorization': 'wrong'}).status_code == 401
    assert client.delete(path, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.delete(path).json() == {'ok': True}
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    assert [c['id'] for c in client.get('/api/conversations').json()] == [other['id']]
    for run in runs:
        assert client.get('/api/runs/' + run + '/events').status_code == 404
    assert len(client.get('/api/conversations/' + other['id']).json()['runs']) == 1
    for kind in ('attachments', 'workspaces'):
        assert not (config.data / kind / target['id']).exists()
        assert (config.data / kind / other['id']).is_dir()
    with sqlite3.connect(config.data / 'history.sqlite3') as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT DISTINCT run_id FROM events').fetchall() == [(other_runs[0],)]
    assert not list((config.data / '.deleting').iterdir())


def test_delete_empty_conversation_and_busy_rejection(session):
    client, store, app, config = session
    c = client.post('/api/conversations', json={'model': 'mock-model', 'effort': 'low'}).json()
    path = '/api/conversations/' + c['id']
    app.state.diagnostic_busy = True
    assert client.delete(path).status_code == 409
    assert client.get(path).status_code == 200
    app.state.diagnostic_busy = False
    # A terminal DB record alone is insufficient while the controller still owns a process.
    app.state.controller.proc = object()
    assert client.delete(path).status_code == 409
    app.state.controller.proc = None
    assert client.delete(path).json() == {'ok': True}


def test_store_rejects_active_run(tmp_path):
    store = Store(tmp_path)
    try:
        c, _ = conversation(store, ())
        run, _ = store.reserve(c['id'], {'request_id': str(uuid.uuid4()), 'revision': 0,
                                        'text': '途中', 'attachment_ids': [], 'timeout': 30})
        from agweb.store import Conflict
        with pytest.raises(Conflict):
            store.delete_conversation(c['id'])
        assert store.run(run['id'])['state'] == 'starting'
        assert (tmp_path / 'attachments' / c['id']).is_dir()
    finally:
        store.close()


def test_db_failure_restores_files_and_transaction(tmp_path):
    store = Store(tmp_path)
    try:
        c, runs = conversation(store)
        before = store.detail(c['id'])
        store.deletion_warning = '別の会話のファイルの完全削除が未完了です。'
        store.db.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON conversations BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        from agweb.store import DeletionError
        with pytest.raises(DeletionError, match='履歴は削除していません'):
            store.delete_conversation(c['id'])
        assert store.detail(c['id']) == before
        assert store.events(runs[0], 0)
        for kind in ('attachments', 'workspaces'):
            assert (tmp_path / kind / c['id']).is_dir()
        assert not (tmp_path / '.deleting' / c['id']).exists()
    finally:
        store.close()


def test_move_failure_leaves_history_and_restores_earlier_moves(tmp_path, monkeypatch):
    store = Store(tmp_path)
    try:
        c, _ = conversation(store)
        original = Path.rename
        def rename(path, target):
            if path == tmp_path / 'workspaces' / c['id']:
                raise PermissionError('locked workspace')
            return original(path, target)
        monkeypatch.setattr(Path, 'rename', rename)
        from agweb.store import DeletionError
        with pytest.raises(DeletionError):
            store.delete_conversation(c['id'])
        assert len(store.detail(c['id'])['runs']) == 3
        assert (tmp_path / 'attachments' / c['id']).is_dir()
        assert (tmp_path / 'workspaces' / c['id']).is_dir()
    finally:
        store.close()


def test_cleanup_failure_warns_and_restart_finishes_delete(tmp_path, monkeypatch):
    import agweb.store as module
    store = Store(tmp_path)
    c, _ = conversation(store)
    with monkeypatch.context() as patch:
        patch.setattr(module.shutil, 'rmtree', lambda path: (_ for _ in ()).throw(PermissionError('locked')))
        result = store.delete_conversation(c['id'])
    assert result['ok'] and '完全削除' in result['warning']
    assert (tmp_path / '.deleting' / c['id']).is_dir()
    assert not store.conversations()
    store.close()
    reopened = Store(tmp_path)
    try:
        reopened.recover()
        assert not reopened.deletion_warning
        assert not (tmp_path / '.deleting' / c['id']).exists()
    finally:
        reopened.close()


@pytest.mark.parametrize('kinds', [('attachments',), ('attachments', 'workspaces')])
def test_restart_restores_staged_files_when_db_was_not_deleted(tmp_path, kinds):
    store = Store(tmp_path)
    c, _ = conversation(store)
    stage = tmp_path / '.deleting' / c['id']
    stage.mkdir(parents=True)
    for kind in kinds:
        (tmp_path / kind / c['id']).rename(stage / kind)
    store.close()
    reopened = Store(tmp_path)
    try:
        reopened.recover()
        assert not reopened.deletion_warning
        assert len(reopened.detail(c['id'])['runs']) == 3
        for kind in ('attachments', 'workspaces'):
            assert (tmp_path / kind / c['id']).is_dir()
        assert not stage.exists()
    finally:
        reopened.close()


def test_restart_reports_recovery_failure_without_overwriting_files(tmp_path):
    store = Store(tmp_path)
    c, _ = conversation(store)
    stage = tmp_path / '.deleting' / c['id']
    stage.mkdir(parents=True)
    (tmp_path / 'attachments' / c['id']).rename(stage / 'attachments')
    destination = tmp_path / 'attachments' / c['id']
    destination.mkdir()
    marker = destination / 'keep.txt'
    marker.write_text('keep', encoding='utf-8')
    store.recover()
    assert store.deletion_warning
    assert marker.read_text(encoding='utf-8') == 'keep'
    assert (stage / 'attachments').is_dir()
    store.close()


@pytest.mark.parametrize('location', ['root', 'conversation', 'nested', 'staging'])
def test_junction_cannot_delete_files_outside_owned_directory(tmp_path, location):
    import _winapi
    store = Store(tmp_path / 'data')
    try:
        c, _ = conversation(store)
        outside = tmp_path / 'outside'
        outside.mkdir()
        marker = outside / 'keep.txt'
        marker.write_text('must survive', encoding='utf-8')
        if location == 'root':
            original = store.data / 'attachments'
            original.rename(store.data / 'original-attachments')
            link = original
        elif location == 'conversation':
            original = store.data / 'attachments' / c['id']
            original.rename(original.with_name('original'))
            link = original
        elif location == 'nested':
            link = store.data / 'workspaces' / c['id'] / 'linked'
        else:
            link = store.data / '.deleting'
        _winapi.CreateJunction(str(outside), str(link))
        try:
            from agweb.store import DeletionError
            with pytest.raises(DeletionError):
                store.delete_conversation(c['id'])
            assert len(store.detail(c['id'])['runs']) == 3
            assert marker.read_text(encoding='utf-8') == 'must survive'
        finally:
            link.rmdir()  # Remove only the junction, never its destination.
    finally:
        store.close()


def test_deletion_during_event_replay_ends_stream_cleanly(session, monkeypatch):
    client, store, app, config = session
    c, runs = client.portal.call(conversation, store)
    original = store.events
    deleted = False
    def events(id, after):
        nonlocal deleted
        if not deleted:
            deleted = True
            store.delete_conversation(c['id'])
        return original(id, after)
    monkeypatch.setattr(store, 'events', events)
    response = client.get('/api/runs/' + runs[0] + '/events')
    assert response.status_code == 200
    assert response.content == b''


def test_api_reports_delete_and_recovery_failures(session, monkeypatch):
    import agweb.store as module
    client, store, app, config = session
    c, _ = client.portal.call(conversation, store)
    def trigger():
        store.db.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON conversations BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    client.portal.call(trigger)
    response = client.delete('/api/conversations/' + c['id'])
    assert response.status_code == 500 and '履歴は削除していません' in response.json()['detail']
    assert client.get('/api/conversations/' + c['id']).status_code == 200
    client.portal.call(lambda: store.db.execute('DROP TRIGGER fail_delete'))
    monkeypatch.setattr(module.shutil, 'rmtree', lambda path: (_ for _ in ()).throw(PermissionError('locked')))
    response = client.delete('/api/conversations/' + c['id'])
    assert response.status_code == 200 and '完全削除' in response.json()['warning']
    assert client.get('/api/status').json()['deletion_warning'] == response.json()['warning']
