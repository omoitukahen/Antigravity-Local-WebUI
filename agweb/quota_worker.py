"""Owns a single quota CLI and its Job; parent exit and deadline are independent of I/O."""
import json
import os
import queue
import re
from pathlib import Path
import sys
import threading
import time
import win32api
import win32con
import win32event
import win32file
from .config import APP_ROOT
from .conpty import ConPTYProcess
from .quota import UsageScreen
from .windows import Mutex


def execute(spec,timeout=30):
    lock=Mutex(spec['mutex']+'_run')
    if not lock.acquire():
        lock.close();return {'ok':False,'reason':'別の処理が実行中です。'}
    parent=None;job=None;monitor_thread=None;screen=UsageScreen()
    result={'ok':False,'reason':'クォータ画面を取得できません。認証状態またはCLIの対応形式を確認してください。'}
    stop=threading.Event();pending=queue.Queue(maxsize=128);sent=False
    try:
        parent=win32api.OpenProcess(win32con.SYNCHRONIZE,False,spec['parent_pid'])
        argv=([sys.executable,str(APP_ROOT/'tests'/'mock_usage.py')] if spec['mock'] else [spec['cli']])
        started=time.monotonic();job=ConPTYProcess(argv,spec['cwd'],dict(os.environ))
        def monitor():
            while not stop.wait(.025):
                if time.monotonic()-started>=timeout or win32event.WaitForSingleObject(parent,0)==win32event.WAIT_OBJECT_0:
                    stop.set();job.kill();break
        monitor_thread=threading.Thread(target=monitor,daemon=True)
        monitor_thread.start()
        def drain():
            try:
                while True:
                    _,b=win32file.ReadFile(job.stdout,8192)
                    if not b:break
                    # Keep draining even if the consumer has stopped.
                    if not stop.is_set():
                        try:pending.put_nowait(b)
                        except queue.Full:stop.set();job.kill()
            except Exception:pass
        threading.Thread(target=drain,daemon=True).start()
        candidate=None;stable=0
        while not stop.is_set() and not job.exited():
            try:
                data=pending.get(timeout=.05);screen.feed(data)
            except queue.Empty:pass
            blocked=screen.blocked_reason()
            if blocked:
                result={'ok':False,'reason':blocked};break
            if not sent and screen.ready():
                # No arbitrary terminal input; exactly one built-in slash command.
                job.write(b'/usage\r');sent=True
            if sent:
                panel=screen.panel()
                # Countdown/percentage updates do not make a complete table
                # incomplete. Require continuous completeness, not frozen text.
                if panel and not candidate:stable=time.monotonic()
                candidate=panel
                if candidate and time.monotonic()-stable>=.6:
                    result={'ok':True,'text':candidate};break
        if stop.is_set():result={'ok':False,'reason':'クォータ確認を時間上限またはアプリ終了により停止しました。'}
    except Exception:
        result={'ok':False,'reason':'クォータ出力の形式または取得処理に問題があり停止しました。'}
    finally:
        stop.set()
        # The monitor owns reads of parent/job handles until it has stopped.
        if monitor_thread:monitor_thread.join()
        result['command_submitted']=sent
        result['diagnostics']={'phase':'complete' if result.get('ok') else 'usage_panel' if sent else 'startup',
            'panel_state':screen.panel_state,'bytes_read':screen.size}
        if spec.get('capture'):
            # Local acceptance fixture only. Never return startup screens to the browser.
            lines=[l for l in screen.lines() if not re.search(r'@|https?://|Bearer|token|password|api.key',l,re.I)]
            try:Path(spec['capture']).write_text('\n'.join(lines).rstrip(),encoding='utf-8')
            except OSError:pass
        if job:
            job.kill();end=time.monotonic()+5
            while job.active() and time.monotonic()<end:time.sleep(.02)
            result['active_processes']=job.active()
            if job.active():
                result={'ok':False,'reason':'停止確認失敗。実行ロックを保持しています。','active_processes':job.active()}
                print(json.dumps(result,ensure_ascii=False),flush=True)
                # Never release the execution lock while descendants remain.
                while job.active():
                    if win32event.WaitForSingleObject(parent,0)==win32event.WAIT_OBJECT_0:break
                    time.sleep(.05)
            job.close()
        if parent:parent.Close()
        lock.close()
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    spec=json.loads(sys.stdin.buffer.read(8192))
    print(json.dumps(execute(spec),ensure_ascii=False),flush=True)
