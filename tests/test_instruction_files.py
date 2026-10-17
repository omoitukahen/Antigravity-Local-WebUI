from pathlib import Path
import pytest
from test_conversation_features import session,create,patch,send
from test_api import wait


def test_saved_instructions_materialize_before_send_and_resave_repairs(session):
    client,app,config=session
    c=create(client,'日本語\r\n指示 📄')
    path=config.data/'workspaces'/c['id']/'AGENTS.md'
    assert c['instruction_file_ready'] and path.read_bytes()==c['system_prompt'].encode()
    path.write_bytes(b'changed outside')
    assert not client.get('/api/conversations/'+c['id']).json()['conversation']['instruction_file_ready']
    saved=patch(client,c,system_prompt=c['system_prompt'],revision=c['revision']).json()
    assert saved['instruction_file_ready'] and path.read_bytes()==c['system_prompt'].encode()
    other=create(client,'別の指示');other_path=config.data/'workspaces'/other['id']/'AGENTS.md'
    assert other_path.read_bytes()=='別の指示'.encode()
    assert patch(client,c,system_prompt='conflict',revision=0).status_code==409
    assert path.read_bytes()==c['system_prompt'].encode()
    cleared=patch(client,saved,system_prompt='',revision=saved['revision']).json()
    assert not cleared['instruction_file_ready'] and not path.exists() and other_path.exists()


def test_sync_failure_keeps_database_revision_and_can_retry(session,monkeypatch):
    client,app,config=session;c=create(client,'初期')
    original=Path.replace
    def fail(path,*args,**kwargs):
        if path.name.startswith('AGENTS.md.'):raise OSError('test denied')
        return original(path,*args,**kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path,'replace',fail)
        result=patch(client,c,system_prompt='更新',revision=c['revision'])
        assert result.status_code==200
        saved=result.json()
        assert saved['system_prompt']=='更新' and saved['revision']==1 and not saved['instruction_file_ready']
        assert 'DBには保存済み' in saved['instruction_file_error']
    repaired=patch(client,saved,system_prompt='更新',revision=saved['revision']).json()
    assert repaired['instruction_file_ready'] and not repaired['instruction_file_error']


def test_open_folder_apis_use_only_verified_saved_paths(session,monkeypatch):
    client,app,config=session;c=create(client,'指示')
    captured=[]
    monkeypatch.setattr('agweb.server.subprocess.Popen',lambda args,**kwargs:captured.append(args))
    route='/api/conversations/'+c['id']+'/instructions/open-folder'
    assert client.post(route,headers={'Authorization':'wrong'}).status_code==401
    assert client.post(route,json={'path':'C:/Windows'}).status_code==400
    assert client.post(route+'?path=C:/Windows').status_code==400
    assert client.post(route).status_code==200
    assert captured[-1][1:]==['/select,',str(config.data/'workspaces'/c['id']/'AGENTS.md')]
    assert client.post('/api/output-settings/open-folder').status_code==409
    folder=config.root/'novels';folder.mkdir()
    assert client.put('/api/output-settings',json={'folder':str(folder),'revision':0}).status_code==200
    assert client.post('/api/output-settings/open-folder',json={'folder':'C:/Windows'}).status_code==400
    assert client.post('/api/output-settings/open-folder?folder=C:/Windows').status_code==400
    assert client.post('/api/output-settings/open-folder').status_code==200
    assert captured[-1][1:]==[str(folder)]
    empty=create(client)
    assert client.post('/api/conversations/'+empty['id']+'/instructions/open-folder').status_code==409


def test_instruction_parent_link_is_rejected_without_losing_saved_db(session):
    import subprocess
    client,app,config=session;c=create(client)
    outside=config.root/'outside';outside.mkdir()
    (config.data/'workspaces').mkdir(exist_ok=True)
    link=config.data/'workspaces'/c['id']
    result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
    assert result.returncode==0
    try:
        result=patch(client,c,system_prompt='DBの指示',revision=c['revision']).json()
        assert result['system_prompt']=='DBの指示' and not result['instruction_file_ready']
        assert 'DBには保存済み' in result['instruction_file_error']
        assert not (outside/'AGENTS.md').exists()
        assert client.post('/api/conversations/'+c['id']+'/instructions/open-folder').status_code==409
    finally:link.rmdir()


@pytest.mark.parametrize('scenario',['chat_write_hook','chat_write_active'])
def test_normal_mode_runtime_file_request_explains_mode(session,scenario):
    client,app,config=session;config.mock_scenario=scenario;c=create(client)
    result=send(client,c,'test')
    run,_=wait(client,result['run'],result['owner'])
    assert run['state']=='failed' and run['reason']=='output_mode_mismatch'
    assert run['output'] is None
