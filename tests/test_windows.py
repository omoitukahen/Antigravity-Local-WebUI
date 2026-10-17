import os
import subprocess
import sys
import pytest
import win32api,win32con,win32event,win32job
from agweb.windows import JobProcess

def test_updater_disabled_in_job_child_without_changing_parent(tmp_path,monkeypatch):
    import win32file
    import time
    monkeypatch.setenv('AGY_CLI_DISABLE_AUTO_UPDATE','false')
    job=JobProcess([sys.executable,'-c',"import os;print(os.environ['AGY_CLI_DISABLE_AUTO_UPDATE'])"],tmp_path,dict(os.environ))
    try:
        job.send(b'')
        _,data=win32file.ReadFile(job.stdout,1000)
        assert data.strip()==b'true'
        assert win32event.WaitForSingleObject(job.process,3000)==win32event.WAIT_OBJECT_0
        deadline=time.monotonic()+3
        while job.active() and time.monotonic()<deadline:time.sleep(.01)
        assert job.code()==0 and job.active()==0
        assert os.environ['AGY_CLI_DISABLE_AUTO_UPDATE']=='false'
    finally:job.close()

def test_job_assignment_failure_never_resumes_cli(tmp_path,monkeypatch):
    marker=tmp_path/'must-not-exist.txt'
    def denied(*args):raise RuntimeError('injected Job assignment error')
    monkeypatch.setattr(win32job,'AssignProcessToJobObject',denied)
    with pytest.raises(RuntimeError):
        JobProcess([sys.executable,'-c',f"from pathlib import Path;Path({str(marker)!r}).write_text('ran')"],tmp_path,dict(os.environ))
    assert not marker.exists()

def test_cli_inherits_only_allowlisted_stdio_handles(tmp_path):
    # An inheritable unrelated event must not appear in the CLI process.
    import pywintypes
    sa=pywintypes.SECURITY_ATTRIBUTES();sa.bInheritHandle=True
    unrelated=win32event.CreateEvent(sa,True,False,None)
    try:
        # Query an inherited event via GetHandleInformation, avoiding PID lookup.
        script="import ctypes,os;from ctypes import wintypes;f=wintypes.DWORD();ok=ctypes.windll.kernel32.GetHandleInformation(wintypes.HANDLE(int(os.environ['TEST_HANDLE'])),ctypes.byref(f));print('inherited' if ok else 'blocked')"
        job=JobProcess([sys.executable,'-c',script],tmp_path,dict(os.environ,TEST_HANDLE=str(int(unrelated))))
        try:
            job.send(b'')
            import win32file
            _,data=win32file.ReadFile(job.stdout,1000)
            assert data.strip()==b'blocked'
            assert win32event.WaitForSingleObject(job.process,3000)==win32event.WAIT_OBJECT_0
        finally:job.close()
    finally:unrelated.Close()
