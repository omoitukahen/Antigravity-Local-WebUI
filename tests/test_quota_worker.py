import os
import threading
from pathlib import Path
import pytest

from agweb.quota_worker import execute


@pytest.mark.parametrize('fixture,reset,percentage',[
    ('quota-1.3.0.txt','80h 42m','99.94%'),
    ('quota-argon-1.3.0.txt','167h 25m','98.38%'),
])
def test_complete_panel_can_keep_refreshing(monkeypatch,fixture,reset,percentage):
    panel=(Path(__file__).parent/'fixtures'/fixture).read_text(encoding='utf-8').rstrip()

    class Terminal:
        def __init__(self,*args):
            self.stdout=self
            self.sent=threading.Event()
            self.closed=threading.Event()
            self.frames=0

        def write(self,data):
            assert data==b'/usage\r'
            self.sent.set()

        def read(self):
            if self.frames==0:
                self.frames+=1
                return b'Antigravity CLI 1.3.0\r\n>\r\n? for shortcuts'
            if not self.sent.wait(1) or self.closed.wait(.1):raise OSError('closed')
            self.frames+=1
            text=panel.replace(reset,f'{reset} {self.frames}s')
            return ('\x1b[2J\x1b[H'+text.replace('\n','\r\n')+'\r\nesc Close').encode()

        def exited(self):return self.closed.is_set()
        def active(self):return 0
        def kill(self):self.closed.set()
        def close(self):self.closed.set()

    monkeypatch.setattr('agweb.quota_worker.ConPTYProcess',Terminal)
    monkeypatch.setattr('agweb.quota_worker.win32file.ReadFile',lambda handle,size:(0,handle.read()))
    result=execute({'parent_pid':os.getpid(),'mutex':'Local\\AGWeb_RefreshingTest',
        'cli':'unused','cwd':'.','mock':True},timeout=2)
    assert result['ok'],result
    assert result['command_submitted'] and result['active_processes']==0
    assert percentage in result['text']
    assert result['diagnostics']['panel_state']=='complete'
