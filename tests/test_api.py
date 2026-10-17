import time
import uuid
from fastapi.testclient import TestClient
import pytest
from agweb.config import Config
from agweb.server import create_app

@pytest.fixture
def session(tmp_path):
    config=Config(tmp_path,'test-key',8811,True,browser_timeout=1,server_timeout=2)
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer test-key'}) as client:
        yield client,app,config

def create(client):
    response=client.post('/api/conversations',json={'model':'mock-model','effort':'low'})
    assert response.status_code==200,response.text
    return response.json()

def send(client,c,text='日本語で相談します',attachments=None):
    body={'request_id':str(uuid.uuid4()),'revision':c['revision'],'text':text,'attachment_ids':attachments or [],'timeout':30}
    response=client.post('/api/conversations/'+c['id']+'/runs',json=body)
    assert response.status_code==200,response.text
    return response.json(),body

def wait(client,run,owner,timeout=5,heartbeat=True):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if heartbeat:client.post('/api/runs/'+run['id']+'/heartbeat',headers={'X-Run-Owner':owner})
        d=client.get('/api/conversations/'+run['conversation_id']).json()
        r=next(x for x in d['runs'] if x['id']==run['id'])
        if r['state'] in {'completed','failed','cancelled','timed_out'}:
            while client.get('/api/status').json()['busy'] and time.monotonic()<deadline:time.sleep(.02)
            return r,d
        time.sleep(.03)
    pytest.fail('run did not terminate')

def test_auth_origin_host_and_real_lock(session,tmp_path):
    client,app,config=session
    assert client.get('/api/status',headers={'Authorization':'wrong'}).status_code==401
    assert client.get('/api/status',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==403
    assert client.get('/api/status').status_code==200
    real=Config(tmp_path/'real','key',8812)
    with TestClient(create_app(real),base_url=real.origin,headers={'Authorization':'Bearer key'}) as other:
        assert other.get('/api/status').json()['acceptance']['ready'] is False
        assert other.post('/api/conversations',json={'model':'invented-model','effort':'low'}).status_code==400

def test_chunked_request_body_is_bounded(session):
    client,app,config=session
    response=client.post('/api/conversations',content=(b'x'*1024*1024 for _ in range(7)),headers={'Content-Type':'application/json'})
    assert response.status_code==413

def test_operating_model_selection_does_not_require_new_live_tests(tmp_path,monkeypatch):
    from agweb import gate
    from agweb.validation import save
    config=Config(tmp_path,'test-key',8813)
    save(config.data/'models.json',{'models':['previous-model','another-model']})
    acceptance={'ready':True,'operational':True,'reason':'operation',
        'tested_pairs':[{'model':'previous-model','effort':'medium'}],
        'allowed_pairs':[{'model':'previous-model','effort':'medium'},{'model':'another-model','effort':'high'}]}
    monkeypatch.setattr(gate,'status',lambda c:acceptance)
    app=create_app(config)
    launches=[]
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer test-key'}) as client:
        # Replace the process boundary: this test must never start a real CLI.
        def launch(run,prompt,conversation):
            launches.append((conversation['model'],conversation['effort']))
            return 'test-owner'
        monkeypatch.setattr(app.state.controller,'launch',launch)
        wrong=client.post('/api/conversations',json={'model':'another-model','effort':'low'}).json()
        body={'request_id':str(uuid.uuid4()),'revision':0,'text':'相談','attachment_ids':[],'timeout':30}
        denied=client.post('/api/conversations/'+wrong['id']+'/runs',json=body)
        assert denied.status_code==409 and not launches
        chosen=client.post('/api/conversations',json={'model':'another-model','effort':'high'}).json()
        sent=client.post('/api/conversations/'+chosen['id']+'/runs',json={**body,'request_id':str(uuid.uuid4())})
        assert sent.status_code==200,sent.text
        assert launches==[('another-model','high')]
        assert client.get('/api/conversations/'+chosen['id']).json()['conversation']['model']=='another-model'

def test_model_refresh_updates_acceptance_without_launching_or_changing_history(tmp_path,monkeypatch):
    import json
    from agweb import gate,diagnostics
    from agweb.validation import save
    config=Config(tmp_path,'test-key',8814)
    cache=config.data/'models.json'
    save(cache,{'models':['retired-model','available-model']})
    def acceptance(c):
        models=json.loads(cache.read_text(encoding='utf-8'))['models']
        return {'ready':True,'operational':True,'reason':'operation','models':models,
            'allowed_pairs':[{'model':m,'effort':'medium'} for m in models]}
    monkeypatch.setattr(gate,'status',acceptance)
    def refresh(c):
        save(cache,{'models':['available-model']})
        return {'ok':True,'models':['available-model']}
    monkeypatch.setattr(diagnostics,'collect_models',refresh)
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer test-key'}) as client:
        launches=[]
        monkeypatch.setattr(app.state.controller,'launch',lambda *args:launches.append(True))
        old=client.post('/api/conversations',json={'model':'retired-model','effort':'medium'}).json()
        assert client.post('/api/models/refresh').json()['ok']
        status=client.get('/api/status').json()
        assert status['acceptance']['ready'] and status['acceptance']['models']==['available-model']
        assert client.get('/api/conversations/'+old['id']).json()['conversation']==old
        body={'request_id':str(uuid.uuid4()),'revision':0,'text':'相談','attachment_ids':[],'timeout':30}
        assert client.post('/api/conversations/'+old['id']+'/runs',json=body).status_code==409
        assert not launches

def test_resume_idempotency_and_events(session):
    client,app,config=session
    c=create(client)
    result,body=send(client,c)
    duplicate=client.post('/api/conversations/'+c['id']+'/runs',json=body)
    assert duplicate.json()['run']['id']==result['run']['id']
    assert duplicate.json()['owner'] is None
    conflict=client.post('/api/conversations/'+c['id']+'/runs',json={**body,'text':'changed'})
    assert conflict.status_code==409
    r,d=wait(client,result['run'],result['owner'])
    assert r['state']=='completed',r
    assert r['usage']['total_tokens']==30
    assert r['response'].count('こんにちは。')==1
    events=[__import__('json').loads(v) for v in client.get('/api/runs/'+r['id']+'/events').text.splitlines()]
    assert events[-1]['kind']=='terminal'
    replay=client.get('/api/runs/'+r['id']+'/events?after='+str(events[-2]['seq'])).text.splitlines()
    assert len(replay)==1
    c=d['conversation']
    second,_=send(client,c,'続きを相談')
    r2,d2=wait(client,second['run'],second['owner'])
    assert r2['state']=='completed'
    assert r2['cli_id']==r['cli_id']
    assert client.post('/api/conversations/'+c['id']+'/runs',json={**body,'request_id':str(uuid.uuid4())}).status_code==409

def test_attachments_and_limits(session):
    client,app,config=session
    c=create(client);path='/api/conversations/'+c['id']+'/attachments'
    a=client.post(path,files={'file':('資料.md','資料の日本語'.encode(),'text/plain')})
    assert a.status_code==200,a.text
    a=a.json()
    b=client.post(path,files={'file':('資料.txt','UTF16資料'.encode('utf-16'),'text/plain')})
    assert b.status_code==200
    assert client.post(path,files={'file':('unsupported.pdf',b'%PDF','application/pdf')}).status_code==400
    assert client.post(path,files={'file':('binary.txt',b'\x00\x01','text/plain')}).status_code==400
    assert client.post(path,files={'file':('cp932.txt','日本語'.encode('cp932'),'text/plain')}).status_code==400
    assert client.post(path,files={'file':('large.txt',b'a'*(1024*1024+1),'text/plain')}).status_code==400
    body={'request_id':str(uuid.uuid4()),'revision':0,'text':'a'*19999,'attachment_ids':[a['id']],'timeout':30}
    assert client.post('/api/conversations/'+c['id']+'/runs',json=body).status_code==400
    for limit in (0,29,601):
        assert client.post('/api/conversations/'+c['id']+'/runs',json={**body,'text':'a','timeout':limit}).status_code==422
    r,_=send(client,c,'添付について',[a['id'],b.json()['id']])
    finished,_=wait(client,r['run'],r['owner'])
    assert finished['state']=='completed'
    assert not list((config.data/'workspaces'/c['id']).glob('*.txt'))

def test_ownership_stop_and_readonly(session):
    client,app,config=session
    config.mock_scenario='hang'
    c=create(client);r,_=send(client,c)
    id=r['run']['id']
    assert client.post('/api/runs/'+id+'/stop').status_code==403
    lease=client.post('/api/runs/'+id+'/claim').json()['owner']
    assert lease!=r['owner']
    assert client.post('/api/runs/'+id+'/heartbeat',headers={'X-Run-Owner':r['owner']}).status_code==403
    assert client.post('/api/runs/'+id+'/stop',headers={'X-Run-Owner':lease}).status_code==200
    finished,d=wait(client,r['run'],lease)
    assert finished['state']=='cancelled'
    assert d['conversation']['resumable']==0
    assert client.post('/api/runs/'+id+'/stop').status_code==200
    assert client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':1,'text':'new','timeout':30}).status_code==409

def test_browser_disconnect_and_single_execution(session):
    client,app,config=session
    config.mock_scenario='hang'
    c=create(client);r,_=send(client,c)
    c2=create(client)
    assert client.post('/api/conversations/'+c2['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':0,'text':'new','timeout':30}).status_code==409
    finished,_=wait(client,r['run'],r['owner'],heartbeat=False)
    assert finished['reason']=='browser_disconnected'

def test_history_survives_restart(tmp_path):
    config=Config(tmp_path,'test-key',8811,True)
    with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer test-key'}) as client:
        c=create(client);r,_=send(client,c);finished,d=wait(client,r['run'],r['owner'])
        assert finished['state']=='completed'
    with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer test-key'}) as client:
        saved=client.get('/api/conversations/'+c['id']).json()
        assert saved['runs'][0]['response']==finished['response']
        assert saved['conversation']['cli_id']==d['conversation']['cli_id']

def test_final_answer_stop_race_keeps_answer_and_one_terminal(session):
    client,app,config=session;config.mock_scenario='exit_hang'
    c=create(client);r,_=send(client,c);id=r['run']['id']
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        client.post('/api/runs/'+id+'/heartbeat',headers={'X-Run-Owner':r['owner']})
        detail=client.get('/api/conversations/'+c['id']).json()
        if detail['runs'][0]['usage']:break
        time.sleep(.02)
    client.post('/api/runs/'+id+'/stop',headers={'X-Run-Owner':r['owner']})
    final,detail=wait(client,r['run'],r['owner'])
    assert final['state']=='cancelled' and final['reason']=='manual'
    assert 'こんにちは。' in final['response']
    import json
    events=[json.loads(line) for line in client.get('/api/runs/'+id+'/events').text.splitlines()]
    assert sum(e['kind']=='terminal' for e in events)==1
    assert detail['conversation']['resumable']==0
