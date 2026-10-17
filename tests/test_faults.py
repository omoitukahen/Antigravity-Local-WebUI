import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from fastapi.testclient import TestClient
from agweb.config import Config
from agweb.server import create_app
from agweb.store import Store
from test_api import create,send,wait
import win32api,win32con,win32event

ROOT=Path(__file__).resolve().parents[1]

def test_save_failure_stops_cli_and_blocks_send(tmp_path):
    config=Config(tmp_path,'key',8811,True,browser_timeout=2)
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
        c=create(client)
        real_event=app.state.store.event
        def broken(id,event):
            if event['kind']=='delta':raise OSError('injected DB error')
            return real_event(id,event)
        app.state.store.event=broken
        r,_=send(client,c)
        deadline=time.monotonic()+5
        while app.state.controller.busy and time.monotonic()<deadline:time.sleep(.02)
        status=client.get('/api/status').json()
        assert status['persistence_failed'] is True
        assert status['busy'] is False
        assert app.state.controller.proc is None
        response=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':'new','revision':1,'text':'new','timeout':30})
        assert response.status_code==409

def test_restart_recovers_unfinished_run(tmp_path):
    store=Store(tmp_path/'data')
    c=store.create('mock-model','low')
    r,_=store.reserve(c['id'],{'request_id':'old','revision':0,'text':'old','attachment_ids':[],'timeout':30})
    store.close()
    config=Config(tmp_path,'key',8811,True)
    with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
        d=client.get('/api/conversations/'+c['id']).json()
        assert d['runs'][0]['state']=='cancelled'
        assert d['conversation']['resumable']==0

def test_restart_waits_for_old_execution_mutex(tmp_path):
    from agweb.windows import Mutex
    config=Config(tmp_path,'key',8811,True)
    store=Store(config.data);c=store.create('mock-model','low')
    store.reserve(c['id'],{'request_id':'old','revision':0,'text':'old','attachment_ids':[],'timeout':30});store.close()
    old=Mutex(config.mutex_prefix+'_run');assert old.acquire()
    try:
        with TestClient(create_app(config),base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
            assert client.get('/api/status').json()['busy'] is True
            assert client.get('/api/conversations/'+c['id']).json()['runs'][0]['state']=='starting'
            old.close();old=None
            assert client.get('/api/status').json()['busy'] is False
            assert client.get('/api/conversations/'+c['id']).json()['runs'][0]['state']=='cancelled'
    finally:
        if old:old.close()

def test_monitor_loss_does_not_claim_zero_or_allow_new_send(tmp_path):
    config=Config(tmp_path,'key',8811,True,mock_scenario='hang')
    app=create_app(config)
    with TestClient(app,base_url=config.origin,headers={'Authorization':'Bearer key'}) as client:
        c=create(client);r,_=send(client,c)
        time.sleep(.2)
        app.state.controller.proc.kill()
        deadline=time.monotonic()+5
        while app.state.controller.busy and time.monotonic()<deadline:time.sleep(.02)
        status=client.get('/api/status').json()
        assert not status['busy'] and status['persistence_failed']
        d=client.get('/api/conversations/'+c['id']).json()
        assert d['runs'][0]['state']=='failed' and d['runs'][0]['reason']=='supervisor_lost'
        assert d['conversation']['resumable']==0
        import uuid
        response=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':str(uuid.uuid4()),'revision':1,'text':'new','timeout':30})
        assert response.status_code==409

def test_actual_server_crash_kills_supervised_tree(tmp_path):
    # Real TCP server, actual parent death, and a long-running mock CLI tree.
    server=subprocess.Popen([sys.executable,str(ROOT/'launch.py'),'--mock','--mock-scenario','trees','--no-browser','--root',str(tmp_path)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    runtime=tmp_path/'data'/'runtime.json'
    import httpx
    handles=[]
    try:
        deadline=time.monotonic()+8
        while not runtime.exists() and time.monotonic()<deadline:time.sleep(.05)
        settings=json.loads(runtime.read_text(encoding='utf-8'))
        with httpx.Client(base_url=settings['origin'],headers={'Authorization':'Bearer '+settings['token']},trust_env=False) as client:
            c=client.post('/api/conversations',json={'model':'mock-model','effort':'low'}).json()
            r=client.post('/api/conversations/'+c['id']+'/runs',json={'request_id':'death','revision':0,'text':'test','timeout':30}).json()
            id=r['run']['id']
            events=[]
            with client.stream('GET','/api/runs/'+id+'/events') as response:
                for line in response.iter_lines():
                    event=json.loads(line)
                    if event['kind']=='spawn':
                        handles=[win32api.OpenProcess(win32con.SYNCHRONIZE,False,event[k]) for k in ('pid','supervisor_pid')]
                    if event['kind']=='init':
                        time.sleep(.2) # The mock's child has launched its grandchild by now.
                        break
        server.kill();server.wait()
        for handle in handles:assert win32event.WaitForSingleObject(handle,8000)==win32event.WAIT_OBJECT_0
        assert handles
    finally:
        if server.poll() is None:server.kill();server.wait()
        for handle in handles:handle.Close()
