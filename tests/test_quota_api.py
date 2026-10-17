import threading
import time
import sqlite3
from contextlib import closing
from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest
from agweb.config import Config
from agweb.server import create_app


@pytest.fixture
def local(tmp_path):
    c=Config(tmp_path,'test-key',8820,True)
    app=create_app(c)
    with TestClient(app,base_url=c.origin,headers={'Authorization':'Bearer test-key'}) as client:
        yield client,app,c


def test_utilities_auth_and_no_arguments(local):
    client,app,c=local
    for path in ('/api/quota/refresh','/api/history/open-folder'):
        assert client.post(path,headers={'Authorization':'wrong'}).status_code==401
        assert client.post(path,headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post(path,headers={'Host':'evil.example'}).status_code==403
        assert client.post(path,json={'path':'C:\\'}).status_code==400


def test_quota_api_real_worker_mock_cli_no_history_mutation(local):
    client,app,c=local
    before=client.get('/api/conversations').json()
    def history_counts():
        with closing(sqlite3.connect('file:'+(c.data/'history.sqlite3').as_posix()+'?mode=ro',uri=True)) as db:
            return {table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                for table in ('conversations','runs','events','attachments')}
    counts_before=history_counts()
    response=client.post('/api/quota/refresh')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['ok'] and data['fetched_at']
    assert all(value in data['text'] for value in ('99.99%','99.97%','100.00%','GEMINI 4 ARGON','98.38%','Refreshes in 167h 25m'))
    assert data['text'].count('Five Hour Limit Remaining')==2
    assert data['active_processes']==0
    assert '@' not in data['text'] and 'Token usage information' not in data['text']
    import json
    diagnostic=json.loads((c.data/'quota-last-result.json').read_text(encoding='utf-8'))
    assert diagnostic['diagnostics']['panel_state']=='complete'
    assert diagnostic['command_submitted'] and diagnostic['active_processes']==0
    assert 'text' not in diagnostic and '%' not in json.dumps(diagnostic)
    assert client.get('/api/conversations').json()==before
    assert history_counts()==counts_before
    assert not client.get('/api/status').json()['busy']


def test_competing_requests_and_failure_release(local):
    client,app,c=local
    entered=threading.Event();release=threading.Event();responses=[]
    def collect(config):
        entered.set();release.wait(5);return {'ok':False,'reason':'模擬失敗'}
    with patch('agweb.quota.collect_quota',collect):
        t=threading.Thread(target=lambda:responses.append(client.post('/api/quota/refresh')))
        t.start();assert entered.wait(3)
        try:
            assert client.post('/api/quota/refresh').status_code==409
            assert client.post('/api/models/refresh').status_code==409
            c0=client.post('/api/conversations',json={'model':'mock-model','effort':'low'}).json()
            assert client.post('/api/conversations/'+c0['id']+'/runs',json={'request_id':'test','revision':0,'text':'preserved','timeout':30}).status_code==409
        finally:release.set();t.join(5)
    assert responses[0].json()['ok'] is False
    assert not client.get('/api/status').json()['busy']


def test_explorer_fixed_database_and_missing_file(local):
    client,app,c=local
    with patch('agweb.server.subprocess.Popen') as launch:
        app.state.diagnostic_busy=True
        r=client.post('/api/history/open-folder')
        assert r.status_code==200
        args,kwargs=launch.call_args
        assert args[0][1:]==['/select,',str((c.data/'history.sqlite3').resolve())]
        assert kwargs['shell'] is False
        with patch('agweb.server.Path.is_file',return_value=False):
            assert client.post('/api/history/open-folder').status_code==404
        assert launch.call_count==1
