import json
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient

from agweb.config import Config
from agweb.server import create_app
from agweb.policy import tool_decision
from agweb.output import read_output, OutputError
from test_api import wait


@pytest.fixture
def session(tmp_path):
    config=Config(tmp_path,'output-key',8833,True)
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer output-key'}) as client:
        folder=tmp_path/'novels';folder.mkdir()
        assert client.put('/api/output-settings',json={'folder':str(folder),'revision':0}).status_code==200
        yield client,app,config


def create(client,mode='text_file'):
    r=client.post('/api/conversations',json={'model':'mock-model','effort':'low','output_mode':mode})
    assert r.status_code==200,r.text
    return r.json()


def send(client,c,text='短い小説'):
    r=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':c['revision'],'text':text,'attachment_ids':[],'timeout':30})
    assert r.status_code==200,r.text
    result=r.json()
    return wait(client,result['run'],result['owner'])


def test_direct_file_output_resume_download_and_delete(session):
    client,app,config=session
    c=create(client)
    r,d=send(client,c)
    assert r['state']=='completed' and r['response']=='' and r['output']['state']=='completed',r
    download=client.get('/api/runs/'+r['id']+'/output')
    assert download.status_code==200 and download.content=='FILE-OUTPUT-OK\n日本語の本文 📄\n'.encode()
    assert 'attachment' in download.headers['content-disposition']
    assert client.get('/api/runs/'+r['id']+'/output',headers={'Authorization':'wrong'}).status_code==401
    stream=client.get('/api/runs/'+r['id']+'/events').text
    assert 'PRIVATE_CHAT_BODY' not in stream and '日本語の本文' not in stream
    assert client.get('/api/conversations',params={'q':'日本語の本文'}).json()==[]
    assert client.patch('/api/conversations/'+c['id'],json={'output_mode':'chat','revision':d['conversation']['revision']}).status_code==409
    second,detail=send(client,d['conversation'])
    assert second['output']['name']!=r['output']['name']
    assert r['output']['name']=='Novel.txt' and second['output']['name']=='Novel (2).txt'
    assert client.get('/api/runs/'+r['id']+'/output').content==download.content
    workspace=config.data/'workspaces'/c['id']
    assert client.delete('/api/conversations/'+c['id']).status_code==200
    assert not workspace.exists()
    assert Path(r['output']['path']).is_file() and Path(second['output']['path']).is_file()


@pytest.mark.parametrize('scenario,state,output_state',[('text_missing','failed','missing'),('text_invalid','failed','invalid'),('text_oversized','failed','invalid'),('text_partial','failed','incomplete'),('text_wrong_path','failed','missing'),('text_repeat','failed','incomplete'),('text_no_hook','failed','missing'),('text_unguarded_done','failed','missing'),('text_allow_only','failed','missing'),('text_explicit_deny','failed','missing')])
def test_file_failures_never_fall_back_to_chat(session,scenario,state,output_state):
    client,app,config=session
    config.mock_scenario=scenario
    c=create(client)
    r,d=send(client,c)
    assert r['state']==state and r['response']=='' and r['output']['state']==output_state,r
    raw=client.get('/api/runs/'+r['id']+'/output')
    assert raw.status_code==(200 if output_state=='incomplete' else 409)


def test_download_rejects_modified_file_and_mode_can_change_only_before_reservation(session):
    client,_,config=session
    c=create(client,'chat')
    changed=client.patch('/api/conversations/'+c['id'],json={'output_mode':'text_file','revision':c['revision']})
    assert changed.status_code==200
    r,d=send(client,changed.json())
    path=Path(r['output']['path'])
    path.write_text('改変',encoding='utf-8')
    assert client.get('/api/runs/'+r['id']+'/output').status_code==409


def test_write_guard_accepts_only_one_assigned_new_text_file(tmp_path):
    target=tmp_path/'outputs'/'test.txt';target.parent.mkdir()
    args={'TargetFile':str(target),'Overwrite':False,'CodeContent':'日本語\n','Description':'write text','IsArtifact':False}
    payload={'toolCall':{'name':'write_to_file','args':args}}
    assert tool_decision(payload,str(target.parent))[0]
    assert not tool_decision(payload)[0]
    assert not tool_decision(payload,str(target.parent),True)[0]
    for change in ({'TargetFile':str(tmp_path/'AGENTS.md')},{'TargetFile':'outputs/test.txt'},{'Overwrite':True},{'IsArtifact':True},{'CodeContent':''},{'CodeContent':'\x00'},{'CodeContent':'a'*(1024*1024+1)},{'unexpected':True}):
        assert not tool_decision({'toolCall':{'name':'write_to_file','args':{**args,**change}}},str(target.parent))[0],change.keys()
    target.write_text('existing',encoding='utf-8')
    assert tool_decision(payload,str(target.parent))[0] # allocated a new numbered file


def test_owned_output_rejects_links_and_invalid_utf8(tmp_path):
    target=tmp_path/'test.txt';target.write_bytes(b'\xff')
    with pytest.raises(OutputError):read_output(target,tmp_path)
    target.write_text('valid',encoding='utf-8')
    other=tmp_path/'hard.txt'
    import os
    os.link(target,other)
    with pytest.raises(OutputError):read_output(target,tmp_path)


def test_file_stop_preserves_partial_output(session):
    import time
    client,_,config=session
    config.mock_scenario='text_wait'
    c=create(client)
    r=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':c['revision'],'text':'短文','attachment_ids':[],'timeout':30}).json()
    path=config.root/'novels'/'Novel.txt'
    deadline=time.monotonic()+4
    while not path.exists() and time.monotonic()<deadline:time.sleep(.02)
    assert path.exists()
    assert client.post('/api/runs/'+r['run']['id']+'/stop',headers={'X-Run-Owner':r['owner']}).status_code==200
    run,_=wait(client,r['run'],r['owner'])
    assert run['state']=='cancelled' and run['output']['state']=='incomplete'
    assert client.get('/api/runs/'+run['id']+'/output').status_code==200


def test_tool_proposal_can_arrive_before_the_permission_hook(session):
    client,_,config=session
    config.mock_scenario='text_proposal'
    c=create(client)
    run,_=send(client,c)
    assert run['state']=='completed' and run['output']['state']=='completed',run


def test_named_japanese_output_and_settings_api(session):
    client,app,config=session
    settings=client.get('/api/output-settings').json()
    assert client.get('/api/output-settings',headers={'Authorization':'wrong'}).status_code==401
    assert client.put('/api/output-settings',json={'folder':'relative','revision':settings['revision']}).status_code==400
    assert client.put('/api/output-settings',json={'folder':settings['folder'],'revision':0}).status_code==409
    c=create(client)
    run,_=send(client,c,'テスト出力名: 日本語の小説.txt')
    assert run['output']['name']=='日本語の小説.txt'
    assert any(json.loads(line).get('decision')=='auto_approve' for line in client.get('/api/runs/'+run['id']+'/events').text.splitlines())
    download=client.get('/api/runs/'+run['id']+'/output')
    assert "filename*=UTF-8''" in download.headers['content-disposition']
    assert run['output_root']==settings['folder']


def test_active_execution_rejects_settings_change(session):
    client,_,config=session
    config.mock_scenario='text_wait'
    c=create(client)
    result=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':0,'text':'x','timeout':30}).json()
    settings=client.get('/api/output-settings').json()
    assert client.put('/api/output-settings',json=settings).status_code==409
    client.post('/api/runs/'+result['run']['id']+'/stop',headers={'X-Run-Owner':result['owner']})
    wait(client,result['run'],result['owner'])


def test_parent_junction_is_rejected_for_write_and_read(tmp_path):
    import subprocess
    workspace=tmp_path/'workspace';workspace.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    target=outside/'test.txt';target.write_text('outside',encoding='utf-8')
    # cmd is used only for Windows junction creation, never for deletion.
    result=subprocess.run(['cmd','/c','mklink','/J',str(workspace/'outputs'),str(outside)],capture_output=True)
    assert result.returncode==0
    try:
        linked=workspace/'outputs'/'test.txt'
        with pytest.raises(OutputError):read_output(linked,workspace)
        payload={'toolCall':{'name':'write_to_file','args':{'TargetFile':str(workspace/'outputs'/'new.txt'),'Overwrite':False,'CodeContent':'text','Description':'test'}}}
        assert not tool_decision(payload,str(workspace/'outputs'))[0]
    finally:(workspace/'outputs').rmdir()


def test_real_mode_requires_matching_file_acceptance_receipt(tmp_path,monkeypatch):
    from agweb import gate
    from agweb.config import sha
    config=Config(tmp_path,'real-gate-test',8834)
    config.data.mkdir()
    (config.data/'models.json').write_text(json.dumps({'models':['mock-model']}),encoding='utf-8')
    monkeypatch.setattr(gate,'status',lambda c:{'ready':True,'operational':True,'fingerprint':'current','models':['mock-model'],'allowed_pairs':[{'model':'mock-model','effort':'low'}]})
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer real-gate-test'}) as client:
        assert client.get('/api/status').json()['text_output_ready'] is False
        c=create(client)
        denied=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':c['revision'],'text':'never send','timeout':30})
        assert denied.status_code==409 and app.state.controller.proc is None
        id=str(uuid.uuid4());folder=config.data/'validation';folder.mkdir()
        path=folder/(id+'.json')
        receipt={'passed':True,'fingerprint':'current','terminal':{'state':'completed','active':0,'exit_code':0},'guard_write_observed':True,'usage':{'total_tokens':30}}
        path.write_text(json.dumps(receipt),encoding='utf-8')
        proof={'passed':True,'fingerprint':'current','receipt_id':id,'receipt_sha256':sha(path)}
        (config.data/'text-output-acceptance.json').write_text(json.dumps(proof),encoding='utf-8')
        assert client.get('/api/status').json()['text_output_ready'] is True
        path.write_text('{}',encoding='utf-8')
        assert client.get('/api/status').json()['text_output_ready'] is False
