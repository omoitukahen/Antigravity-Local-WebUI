import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
import pytest
import win32api
import win32con
import win32event

ROOT=Path(__file__).resolve().parents[1]

class Probe:
    def __init__(self,tmp_path,scenario='normal',timeout=4,browser_timeout=3,server_timeout=2,cleanup_timeout=1,prompt='日本語'):
        from agweb.controller import write_hooks
        write_hooks(tmp_path)
        self.proc=subprocess.Popen([sys.executable,'-m','agweb.supervisor'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(os.environ,PYTHONUTF8='1'))
        self.events=[];self.lines=queue.Queue();self.started=time.monotonic()
        self.spec={'parent_pid':os.getpid(),'mutex':'Local\\AGWebTest_'+uuid.uuid4().hex,'argv':[sys.executable,str(ROOT/'tests'/'mock_cli.py'),'--model','mock-model'],
            'cwd':str(tmp_path),'prompt':prompt,'timeout':timeout,'model':'mock-model','mock':True,'scenario':scenario,
            'browser_timeout':browser_timeout,'server_timeout':server_timeout,'cleanup_timeout':cleanup_timeout}
        self.send(self.spec)
        def read():
            for line in self.proc.stdout:self.lines.put(json.loads(line))
        threading.Thread(target=read,daemon=True).start()

    def send(self,value):
        self.proc.stdin.write((json.dumps(value)+'\n').encode());self.proc.stdin.flush()

    def collect(self,stop_after=None,heartbeat=True,browser=True,kill_supervisor=False):
        handle=None
        until=time.monotonic()+12
        next_ping=0
        stopped=False
        while time.monotonic()<until:
            if heartbeat and time.monotonic()>=next_ping and self.proc.poll() is None:
                try:
                    self.send({'kind':'server_ping'})
                    if browser:self.send({'kind':'browser_ping'})
                except OSError:pass
                next_ping=time.monotonic()+.1
            try:
                item=self.lines.get(timeout=.025);self.events.append(item)
                if item['kind']=='spawn':handle=win32api.OpenProcess(win32con.SYNCHRONIZE,False,item['pid'])
            except queue.Empty:pass
            if stop_after is not None and not stopped and time.monotonic()-self.started>=stop_after:
                stopped=True
                if kill_supervisor:self.proc.kill()
                else:self.send({'kind':'stop','reason':'manual'})
            if self.proc.poll() is not None and self.lines.empty():break
        if self.proc.poll() is None:
            self.proc.kill();self.proc.wait();pytest.fail('supervisor did not finish')
        stderr=self.proc.stderr.read().decode('utf-8','replace')
        if self.proc.returncode!=0 and not kill_supervisor:pytest.fail(stderr)
        if handle:
            assert win32event.WaitForSingleObject(handle,2000)==win32event.WAIT_OBJECT_0
            handle.Close()
        self.proc.stdin.close();self.proc.stdout.close();self.proc.stderr.close()
        return next((e for e in self.events if e['kind']=='terminal'),None)

def test_normal_and_web(tmp_path):
    for scenario in ('normal','web','stderr'):
        p=Probe(tmp_path,scenario)
        terminal=p.collect()
        assert terminal['state']=='completed',p.events
        assert terminal['active']==0
        assert terminal['response'].count('こんにちは。')==1
        assert any(e['kind']=='guard' for e in p.events)

def test_hook_command_in_workspace_with_spaces_and_japanese(tmp_path):
    p=Probe(tmp_path/'guard workspace 日本語',scenario='web')
    terminal=p.collect()
    assert terminal['state']=='completed' and terminal['active']==0,p.events
    assert {e['tool'] for e in p.events if e['kind']=='guard' and e['stage']=='tool'}=={'search_web','read_url_content'}


def test_hidden_cli_can_launch_hook_from_japanese_installation(tmp_path,monkeypatch):
    import agweb.controller as controller
    app=tmp_path/'アプリ 配置'
    (app/'agweb').mkdir(parents=True)
    (app/'agweb/hook.py').write_bytes((ROOT/'agweb/hook.py').read_bytes())
    monkeypatch.setattr(controller,'APP_ROOT',app)
    p=Probe(tmp_path/'workspace',scenario='web')
    terminal=p.collect()
    assert terminal['state']=='completed' and terminal['active']==0,p.events

@pytest.mark.parametrize('scenario,reason',[('invalid','invalid_output'),('oversized','line_limit'),('total_limit','output_limit'),('deny','guard_denied'),('missing_guard','guard_not_observed'),('missing_tool_guard','guard_not_observed'),('hook_failure','guard_error'),('hook_exception','guard_error'),('exit_hang','exit_timeout')])
def test_failure_stops(tmp_path,scenario,reason):
    p=Probe(tmp_path,scenario)
    terminal=p.collect()
    assert terminal['state']=='failed',p.events
    assert terminal['reason']==reason,p.events
    assert terminal['active']==0

def test_timeout_tree_and_unrelated_process(tmp_path):
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    try:
        p=Probe(tmp_path,'trees',timeout=.7)
        terminal=p.collect()
        assert terminal['state']=='timed_out'
        assert terminal['active']==0
        assert unrelated.poll() is None
    finally:unrelated.terminate();unrelated.wait()

def test_manual_tree_and_owner_crash(tmp_path):
    p=Probe(tmp_path,'trees')
    terminal=p.collect(stop_after=.5)
    assert terminal['reason']=='manual' and terminal['active']==0
    p=Probe(tmp_path,'trees')
    p.collect(stop_after=.5,kill_supervisor=True)

def test_disconnects(tmp_path):
    p=Probe(tmp_path,'hang',browser_timeout=.5)
    terminal=p.collect(browser=False)
    assert terminal['reason']=='browser_disconnected'
    p=Probe(tmp_path,'hang',server_timeout=.5)
    terminal=p.collect(heartbeat=False)
    assert terminal['reason']=='server_disconnected'

def test_stdin_write_cannot_block_watchdog(tmp_path):
    # Exceed the Windows pipe buffer; the input writer is blocked until Job termination.
    p=Probe(tmp_path,'stdin_hang',timeout=.7,prompt='あ'*20000)
    terminal=p.collect()
    assert terminal['state']=='timed_out' and terminal['active']==0
