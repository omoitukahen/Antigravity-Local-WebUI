import json
import os
import queue
import re
import threading
import time
import win32file
from .windows import JobProcess, Mutex
from .gate import snapshot, fingerprint

def parse_models(text):
    models=[]
    for line in text.splitlines():
        line=re.sub(r'\x1b\[[0-9;]*[a-zA-Z]','',line)
        # Observed agy models output: slug TAB display name. No hardcoded fallback.
        match=re.match(r'^\s*([a-z][a-z0-9._-]*-[a-z0-9._-]+)(?:\t+| {2,})\S',line)
        if match and match[1] not in models:models.append(match[1])
    return models

def collect_cli_text(config,arguments):
    if arguments not in (['models'],['--version']):raise ValueError('診断コマンドが無効です。')
    lock=Mutex(config.mutex_prefix+'_run')
    if not lock.acquire():
        lock.close()
        return {'ok':False,'reason':'別の処理が実行中です。','models':[]}
    job=None
    buffers={'out':bytearray(),'err':bytearray()}
    completed=[]
    try:
        config.data.mkdir(parents=True,exist_ok=True)
        cwd=config.data/'diagnostics';cwd.mkdir(exist_ok=True)
        job=JobProcess([str(config.cli),*arguments],cwd,dict(os.environ,PYTHONUTF8='1'))
        job.send(b'')
        def drain(handle,kind):
            try:
                while True:
                    _,block=win32file.ReadFile(handle,4096)
                    if not block:break
                    buffers[kind].extend(block)
                    del buffers[kind][:-256*1024]
            except Exception:pass
            finally:completed.append(kind)
        threads=[threading.Thread(target=drain,args=(job.stdout,'out'),daemon=True),threading.Thread(target=drain,args=(job.stderr,'err'),daemon=True)]
        for t in threads:t.start()
        deadline=time.monotonic()+30
        while not job.exited() and time.monotonic()<deadline:time.sleep(.05)
        timed_out=not job.exited()
        if timed_out or job.active():
            job.kill()
        until=time.monotonic()+5
        while job.active() and time.monotonic()<until:time.sleep(.02)
        for t in threads:t.join(1)
        text=buffers['out'].decode('utf-8','replace')
        err=buffers['err'].decode('utf-8','replace')
        active=job.active()
        code=job.code()
        return {'ok':not timed_out and code==0 and active==0,'text':text,
                'exit_code':code,'active_processes':active,'timed_out':timed_out}
    except Exception:
        return {'ok':False,'reason':'接続確認を実行できません。CLIの配置と書込権限を確認してください。','models':[]}
    finally:
        if job:job.close()
        lock.close()


def collect_models(config,debug=False):
    result=collect_cli_text(config,['models'])
    models=parse_models(result.pop('text','')) if result.get('ok') else []
    report={**result,'ok':result.get('ok',False) and bool(models),'models':models,
            'reason':'モデル一覧を確認しました。' if models else 'モデル一覧を取得できません。通常ターミナルの認証状態・設定を確認してください。'}
    if debug:print(json.dumps({'models':models},ensure_ascii=False))
    if report['ok']:
        report['configuration']=snapshot(config)
        report['fingerprint']=fingerprint(report['configuration'])
        (config.data/'connection-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (config.data/'models.json').write_text(json.dumps({'models':models,'source':'agy models','fingerprint':report['fingerprint']},indent=2),encoding='utf-8')
    return {k:v for k,v in report.items() if k!='configuration'}
