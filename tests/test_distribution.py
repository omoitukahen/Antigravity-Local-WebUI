"""Distribution boundaries: no real CLI or model is launched by these tests."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agweb.config import Config, resolve_cli
from agweb import gate, onboarding
from agweb.server import create_app


def test_cli_resolution_is_per_install_and_explicit_never_falls_back(tmp_path, monkeypatch):
    local = tmp_path / 'Local'
    standard = local / 'agy/bin/agy.exe'
    standard.parent.mkdir(parents=True)
    standard.write_bytes(b'cli')
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr('agweb.config.shutil.which', lambda name: str(tmp_path/'path/agy.exe'))
    assert resolve_cli(tmp_path) == standard
    (tmp_path/'webui.local.json').write_text(json.dumps({'cli_path':str(tmp_path/'missing.exe')}), encoding='utf-8')
    assert resolve_cli(tmp_path) == tmp_path/'missing.exe'
    (tmp_path/'webui.local.json').write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError, match='webui.local.json'):
        resolve_cli(tmp_path)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    cli=tmp_path/'agy.exe';cli.write_bytes(b'not executable: never run')
    config=Config(tmp_path,'setup-key',8912,cli=cli)
    current={'cli':str(cli),'cli_sha256':'binary','files':{},'code':{'gate.py':'v1'},
             'overrides':{},'runtime':{'python':'test'},'cli_environment':{}}
    monkeypatch.setattr(gate,'snapshot',lambda c:current)
    monkeypatch.setattr(onboarding,'preflight',lambda c:[])
    monkeypatch.setattr(onboarding,'customization_findings',lambda c,s:[])
    launches=[]
    def connection(c,debug=False):
        launches.append('models')
        c.data.mkdir(parents=True,exist_ok=True)
        value={'models':['example-model-medium','second-model-high'],'fingerprint':gate.fingerprint(current)}
        (c.data/'models.json').write_text(json.dumps(value),encoding='utf-8')
        return {'ok':True,**value}
    monkeypatch.setattr('agweb.diagnostics.collect_models',connection)
    monkeypatch.setattr(onboarding,'collect_version',lambda c:{'ok':True,'version':'1.3.0'})
    with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer setup-key'}) as client:
        yield client,config,current,launches


def checked(client):
    review={'auth_method':'account','endpoint':'official account service','proxy_reviewed':True,
            'loading_sources_verified':True,'no_external_customizations':True}
    response=client.post('/api/setup/check',json={'review':review})
    assert response.status_code==200,response.text
    assert response.json()['ok'],response.text
    return response.json(),review


def approve(client,check,base_review,**updates):
    body={'fingerprint':check['fingerprint'],'review':base_review,'allowed_pairs':[{'model':'example-model-medium','effort':'medium'}],
          'chat_consent':True,'text_output_consent':True}
    body.update(updates)
    return client.put('/api/setup',json=body)


def test_page_and_status_never_launch_cli_and_api_is_authenticated(installation):
    client,config,current,launches=installation
    assert client.get('/api/setup').status_code==200
    assert client.get('/api/status').json()['setup_required']
    assert not launches
    assert client.get('/api/setup',headers={'Authorization':'wrong'}).status_code==401
    assert client.put('/api/setup',json={}).status_code==422
    assert client.post('/api/setup/check',json={'review':{}}).status_code==422
    assert client.post('/api/models/refresh').status_code==409
    assert not launches


def test_confirmed_installation_keeps_live_acceptance_separate_and_filters_models(installation):
    client,config,current,launches=installation
    check,review=checked(client)
    assert approve(client,check,review).status_code==200
    status=client.get('/api/status').json()
    assert status['acceptance']['ready'] and status['text_output_ready']
    assert status['acceptance']['tested_pairs']==[]
    assert status['acceptance']['models']==['example-model-medium']
    assert not (config.data/'live-acceptance.json').exists()
    proof=config.data/'local-setup.json';before=proof.read_bytes()
    cache=config.data/'models.json'
    value=json.loads(cache.read_text(encoding='utf-8'));value['models']=['second-model-high']
    cache.write_text(json.dumps(value),encoding='utf-8')
    assert not gate.status(config)['ready']
    assert proof.read_bytes()==before


def test_stale_or_missing_check_and_incomplete_consent_are_rejected(installation):
    client,config,current,launches=installation
    check,review=checked(client)
    assert approve(client,check,review,chat_consent=False).status_code==400
    assert approve(client,check,review,allowed_pairs=[{'model':'unknown','effort':'low'}]).status_code==400
    assert approve(client,check,review,review={**review,'endpoint':'changed'}).status_code==409
    current['code']['gate.py']='v2'
    assert approve(client,check,review).status_code==409
    assert not (config.data/'local-setup.json').exists()


def test_missing_connection_check_and_unapproved_send_are_rejected(installation,monkeypatch):
    client,config,current,launches=installation
    check,review=checked(client)
    (config.data/'setup-check.json').unlink()
    assert approve(client,check,review).status_code==409
    check,review=checked(client);assert approve(client,check,review).status_code==200
    def forbidden(*args):raise AssertionError('must not launch a model')
    monkeypatch.setattr(client.app.state.controller,'launch',forbidden)
    conversation=client.post('/api/conversations',json={'model':'second-model-high','effort':'high'}).json()
    response=client.post('/api/conversations/'+conversation['id']+'/runs',json={
        'request_id':'unauthorized','revision':0,'text':'test','timeout':30})
    assert response.status_code==409


def test_preflight_missing_cli_unsafe_path_and_write_failure(tmp_path,monkeypatch):
    config=Config(tmp_path,'x',0,cli=tmp_path/'missing.exe')
    def unsafe(cwd):raise ValueError('unsafe')
    monkeypatch.setattr('agweb.controller.write_hooks',unsafe)
    assert len(onboarding.preflight(config))==2
    blocked=tmp_path/'not-directory';blocked.write_text('file',encoding='utf-8')
    config=Config(blocked,'x',0,cli=tmp_path/'missing.exe')
    assert len(onboarding.preflight(config))==2


def test_corrupt_or_stale_setup_never_falls_back_to_legacy(installation):
    client,config,current,launches=installation
    check,review=checked(client);assert approve(client,check,review).status_code==200
    current['cli_sha256']='changed'
    assert not gate.status(config)['ready']
    assert gate.status(config)['block_code']=='cli_changed'
    (config.data/'local-setup.json').write_text('{}',encoding='utf-8')
    assert not gate.status(config)['ready']
    assert client.get('/api/setup').status_code==200
    assert not client.get('/api/status').json()['text_output_ready']


def test_setup_and_check_reject_busy_state(installation):
    client,config,current,launches=installation
    check,review=checked(client)
    client.app.state.diagnostic_busy=True
    assert approve(client,check,review).status_code==409
    assert client.post('/api/setup/check',json={'review':review}).status_code==409
    assert launches==['models']


def test_quota_new_install_only_uses_explicitly_trusted_dedicated_folder(installation,monkeypatch,tmp_path):
    from agweb.quota import quota_workdir,verified_configuration
    client,config,current,launches=installation
    check,review=checked(client);assert approve(client,check,review).status_code==200
    profile=tmp_path/'profile';monkeypatch.setattr(Path,'home',classmethod(lambda cls:profile))
    settings=profile/'.gemini/antigravity-cli/settings.json';settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'trustedWorkspaces':[str(profile)]}),encoding='utf-8')
    with pytest.raises(ValueError,match='信頼'):
        quota_workdir(config)
    target=config.data/'diagnostics/quota';target.mkdir(parents=True)
    settings.write_text(json.dumps({'trustedWorkspaces':[str(target)]}),encoding='utf-8')
    before=settings.read_bytes()
    assert quota_workdir(config)==target.resolve()
    assert verified_configuration(config)
    assert settings.read_bytes()==before


def test_quota_ready_accepts_version_change_but_not_unknown_screen():
    from agweb.quota import UsageScreen
    screen=UsageScreen();screen.feed(b'Antigravity CLI 1.3.6\r\n> \r\n? for shortcuts')
    assert screen.ready()
    screen=UsageScreen();screen.feed(b'Another program 1.3.6\r\n> \r\n? for shortcuts')
    assert not screen.ready()


def test_customization_inspection_does_not_leak_values(tmp_path):
    path=tmp_path/'hooks.json';path.write_text(json.dumps({'foreign':{'SessionStart':[{'command':'secret-command'}]}}),encoding='utf-8')
    # A user may place agy.exe beside other files; that does not trust their hooks.
    findings=onboarding.customization_findings(Config(tmp_path,'x',0,cli=tmp_path/'agy.exe'),{'files':{str(path):'hash'}})
    assert findings and 'secret-command' not in json.dumps(findings)
    path.write_text('{broken',encoding='utf-8')
    assert onboarding.customization_findings(Config(tmp_path,'x',0),{'files':{str(path):'hash'}})


def test_real_snapshot_stays_valid_after_consent_write(tmp_path,monkeypatch):
    profile=tmp_path/'profile';profile.mkdir()
    monkeypatch.setenv('USERPROFILE',str(profile))
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:profile))
    root=tmp_path/'application';root.mkdir()
    # CLI may be beside the app: generated data must not enter its fingerprint.
    cli=root/'agy.exe';cli.write_bytes(b'fake binary, never executed')
    config=Config(root,'snapshot-test',8913,cli=cli)
    monkeypatch.setattr(onboarding,'collect_version',lambda c:{'ok':True,'version':'1.3.0'})
    def models(c,debug=False):
        value={'models':['example-medium'],'fingerprint':gate.fingerprint(gate.snapshot(c))}
        onboarding.write_json(c.data/'models.json',value)
        return {'ok':True,**value}
    monkeypatch.setattr('agweb.diagnostics.collect_models',models)
    review={'auth_method':'account','endpoint':'official','proxy_reviewed':True,
            'loading_sources_verified':True,'no_external_customizations':True}
    result=onboarding.connection_check(config,review)
    assert result['ok'],result
    body={'fingerprint':result['fingerprint'],'review':review,'allowed_pairs':[{'model':'example-medium','effort':'medium'}],
          'chat_consent':True,'text_output_consent':False}
    assert onboarding.approve(config,body)['ready']
    assert gate.status(config)['ready']
    (root/'webui.local.json').write_text(json.dumps({'cli_path':str(cli)}),encoding='utf-8')
    assert not gate.status(config)['ready']
