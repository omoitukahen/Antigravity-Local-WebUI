import os
import sys
import subprocess
import json
import threading
import time
import pytest
import win32api
import win32con
import win32event
import win32file
import win32job
from agweb.conpty import ConPTYProcess
from agweb.quota_worker import execute
from agweb.config import APP_ROOT


def test_assignment_failure_never_resumes_conpty_client(tmp_path,monkeypatch):
    marker=tmp_path/'must-not-run.txt'
    def denied(*args):raise RuntimeError('assignment denied')
    monkeypatch.setattr(win32job,'AssignProcessToJobObject',denied)
    with pytest.raises(RuntimeError):
        ConPTYProcess([sys.executable,'-c',f'from pathlib import Path;Path({str(marker)!r}).touch()'],tmp_path,dict(os.environ))
    assert not marker.exists()


def test_worker_deadline_stops_mock_cli(tmp_path):
    result=execute({'parent_pid':os.getpid(),'mutex':'Local\\AGWeb_deadline_'+str(os.getpid()),
        'cli':'unused','cwd':str(tmp_path),'mock':True},timeout=.2)
    assert not result['ok'] and result['active_processes']==0


def test_job_close_kills_client_and_descendants(tmp_path):
    child_code='import time;time.sleep(60)'
    parent_code=f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child_code!r}]);print("ready",flush=True);time.sleep(60)'
    job=ConPTYProcess([sys.executable,'-c',parent_code],tmp_path,dict(os.environ))
    drain=threading.Thread(target=lambda:_drain(job.stdout),daemon=True);drain.start()
    deadline=time.monotonic()+3
    while job.active()<2 and time.monotonic()<deadline:time.sleep(.02)
    assert job.active()>=2
    pids=win32job.QueryInformationJobObject(job.job,win32job.JobObjectBasicProcessIdList)
    handles=[win32api.OpenProcess(win32con.SYNCHRONIZE,False,pid) for pid in pids]
    try:
        job.close()
        assert all(win32event.WaitForSingleObject(h,3000)==win32event.WAIT_OBJECT_0 for h in handles)
    finally:
        for h in handles:h.Close()


def _drain(handle):
    try:
        while win32file.ReadFile(handle,8192)[1]:pass
    except Exception:pass


@pytest.mark.parametrize('kill_worker',[False,True])
def test_parent_or_worker_exit_leaves_no_cli(tmp_path,kill_worker):
    parent=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
    worker=subprocess.Popen([sys.executable,'-m','agweb.quota_worker'],cwd=APP_ROOT,env=dict(os.environ,PYTHONUTF8='1'),
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    handle=None
    try:
        spec={'parent_pid':parent.pid,'mutex':'Local\\AGWeb_parent_'+str(worker.pid),'cli':'unused','cwd':str(tmp_path),'mock':True}
        worker.stdin.write(json.dumps(spec).encode());worker.stdin.close();worker.stdin=None
        marker=tmp_path/'mock-usage.pid';deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert marker.exists()
        pid=int(marker.read_text(encoding='utf-8'))
        handle=win32api.OpenProcess(win32con.SYNCHRONIZE,False,pid)
        if kill_worker:worker.kill()
        else:parent.kill();parent.wait(3)
        output,_=worker.communicate(timeout=5)
        assert win32event.WaitForSingleObject(handle,3000)==win32event.WAIT_OBJECT_0
        if not kill_worker:assert json.loads(output)['active_processes']==0
    finally:
        if handle:handle.Close()
        if worker.poll() is None:worker.kill();worker.wait(3)
        if parent.poll() is None:parent.kill();parent.wait(3)
