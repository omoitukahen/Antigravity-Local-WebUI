"""Long Japanese output checks; no real CLI or model is invoked."""
import asyncio
import io
import json
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from agweb import gate
from agweb.config import Config
from agweb.controller import Controller
from agweb.server import create_app
from test_api import create, send


@pytest.mark.parametrize('relative',[
    '.gemini/AGENTS.md','.gemini/GEMINI.md',
    '.gemini/antigravity-cli/rules/story.md',
    '.gemini/antigravity-cli/skills/story/SKILL.md',
    '.gemini/antigravity-cli/plugins/story/rules/story.md',
    '.agents/skills/story/SKILL.md','.agent/rules/story.md',
])
def test_effective_source_add_edit_rename_delete(tmp_path,monkeypatch,relative):
    home=tmp_path/'home';home.mkdir()
    monkeypatch.setenv('USERPROFILE',str(home))
    cli=tmp_path/'bin/agy.exe';cli.parent.mkdir();cli.write_bytes(b'fake')
    config=Config(tmp_path/'app','test',0,cli=cli)
    signature=lambda:gate.fingerprint(gate.snapshot(config))
    original=signature();path=home/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('日本語の指示',encoding='utf-8');added=signature()
    assert added!=original
    path.write_text('変更した指示',encoding='utf-8');edited=signature()
    assert edited!=added
    renamed=path.with_name('renamed.md');path.rename(renamed)
    assert signature()!=edited
    renamed.unlink()
    assert signature()==original


def test_status_uses_cache_but_send_checks_fresh_configuration(tmp_path,monkeypatch):
    config=Config(tmp_path,'key',8820,True)
    app=create_app(config)
    checked=Mock(return_value={'ready':True,'mode':'mock','models':['mock-model']})
    monkeypatch.setattr(gate,'status',checked)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
        conversation=create(client)
        for _ in range(10):assert client.get('/api/status').json()['acceptance']['ready']
        assert checked.call_count==1
        checked.return_value={'ready':False,'reason':'configuration changed'}
        launch=Mock();monkeypatch.setattr(app.state.controller,'launch',launch)
        response=client.post('/api/conversations/'+conversation['id']+'/runs',json={
            'request_id':'00000000-0000-0000-0000-000000000001','revision':0,
            'text':'長文','attachment_ids':[],'timeout':30})
        assert response.status_code==409 and checked.call_count==2
        assert not launch.called
        assert not client.get('/api/status').json()['acceptance']['ready']


@pytest.mark.parametrize('length',[300,3000,10000,30000])
@pytest.mark.parametrize('finish',['completed','cancelled'])
def test_delta_batch_preserves_order_and_final_or_partial_text(length,finish):
    async def run():
        text=('日本語の長い物語。\n'*4000)[:length]
        messages=[{'kind':'delta','text':ch} for ch in text]
        final=text+'最終回答' if finish=='completed' else None
        if final:messages.append({'kind':'result','response':final})
        messages.append({'kind':'terminal','state':finish,'response':final})
        proc=SimpleNamespace(stdout=io.BytesIO(b''.join((json.dumps(m,ensure_ascii=False)+'\n').encode() for m in messages)),wait=lambda:0)
        events=[]
        controller=Controller(None,SimpleNamespace(event=lambda id,p:events.append(p)))
        controller.controls=__import__('queue').Queue()
        async def no_ping(proc):pass
        controller.ping_loop=no_ping
        await controller.pump(proc,'run')
        deltas=[e['text'] for e in events if e['kind']=='delta']
        assert ''.join(deltas)==text
        # Byte threshold plus periodic flushes, substantially fewer DB transactions.
        assert len(deltas)<max(10,length//10)
        assert events[-1]['kind']=='terminal'
        assert events[-1]['response']==final
    asyncio.run(run())


def test_owner_stream_keeps_generation_alive_without_browser_timers(tmp_path):
    config=Config(tmp_path,'key',8821,True,browser_timeout=.5,server_timeout=2,mock_scenario='slow')
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
        sent,_=send(client,create(client))
        rows=client.get('/api/runs/'+sent['run']['id']+'/events',headers={'X-Run-Owner':sent['owner']})
        events=[json.loads(line) for line in rows.text.splitlines()]
        assert events[-1]['state']=='completed'
        assert any(e['kind']=='keepalive' for e in events)


def test_observer_stream_does_not_extend_owner_lease(tmp_path):
    config=Config(tmp_path,'key',8822,True,browser_timeout=.5,server_timeout=2,mock_scenario='slow')
    with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
        sent,_=send(client,create(client))
        events=[json.loads(line) for line in client.get('/api/runs/'+sent['run']['id']+'/events').text.splitlines()]
        assert events[-1]['reason']=='browser_disconnected' and events[-1]['active']==0


@pytest.mark.parametrize('rotate',[False,True])
def test_socket_disconnect_or_rotated_owner_stops_and_reaps_cli(tmp_path,rotate):
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    config=Config(tmp_path,'key',sock.getsockname()[1],True,browser_timeout=.6,server_timeout=2,mock_scenario='slow')
    app=create_app(config)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline:time.sleep(.01)
        assert server.started
        with httpx.Client(base_url=config.origin,headers={'Authorization':'Bearer key'},trust_env=False) as client:
            sent,_=send(client,create(client));run=sent['run']
            with client.stream('GET','/api/runs/'+run['id']+'/events',headers={'X-Run-Owner':sent['owner']}) as response:
                lines=response.iter_lines()
                while json.loads(next(lines))['kind']!='keepalive':pass
                if rotate:
                    assert client.post('/api/runs/'+run['id']+'/claim').status_code==200
                    # Keep the old socket open. Rotated credentials cannot renew.
                    events=[json.loads(line) for line in lines]
                    assert events[-1]['reason']=='browser_disconnected'
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                row=client.get('/api/conversations/'+run['conversation_id']).json()['runs'][0]
                if row['state']=='cancelled':break
                time.sleep(.03)
            assert row['reason']=='browser_disconnected'
            terminal=client.get('/api/runs/'+run['id']+'/events').text.splitlines()[-1]
            assert json.loads(terminal)['active']==0
    finally:
        server.should_exit=True;thread.join(8);sock.close()
        assert not thread.is_alive()


def test_quiet_stream_flushes_partial_delta_before_next_cli_event():
    class DelayedPipe:
        def __init__(self):self.index=0
        def readline(self):
            self.index+=1
            if self.index==1:return b'{"kind":"delta","text":"partial"}\n'
            time.sleep(.2)
            return b''
        def close(self):pass
    async def run():
        persisted=[]
        controller=Controller(None,SimpleNamespace(event=lambda id,p:persisted.append((time.monotonic(),p))))
        controller.controls=__import__('queue').Queue()
        proc=SimpleNamespace(stdout=DelayedPipe(),wait=lambda:0,poll=lambda:0)
        start=time.monotonic();await controller.pump(proc,'run')
        assert persisted[0][1]=={'kind':'delta','text':'partial'}
        assert persisted[0][0]-start<.18
    asyncio.run(run())
