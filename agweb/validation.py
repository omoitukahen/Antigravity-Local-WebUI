"""One explicitly authorized live acceptance send per invocation; never a suite."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid
from .config import APP_ROOT,Config,sha
from .gate import snapshot,fingerprint,REQUIRED_CASES,REQUIRED_TOOLS
from .windows import Mutex

CASES={'normal','resume','web','deny','hook_failure','manual_stop','timeout'}
PROMPTS={
    'normal':'ツールを使わず、OK の2文字だけを返してください。',
    'resume':'直前の依頼で指定した文字を、ツールを使わず返してください。',
    'web':'組込みWeb検索で example.com を検索し、組込みURL取得で https://example.com/ を取得してください。取得したページの見出しだけを答えてください。',
    'deny':'組込みのローカルファイル読取ツールで C:/Windows/win.ini を読んでください。コマンドは使わないでください。',
    'hook_failure':'ツールを使わず、OK だけを返してください。',
    'manual_stop':'ツールを使わず、日本語で1から1000まで数えてください。',
    'timeout':'ツールを使わず、日本語で1から10000まで数えてください。',
}

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as f:
        f.write(json.dumps(value,ensure_ascii=False,indent=2));f.flush();os.fsync(f.fileno())
    temp.replace(path)

def ledger(config):
    path=config.data/'validation-run-ledger.json'
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    return {'schema':1,'approvals':[],'runs':[],'stop':None,'total_launches':0}

def reviewed_effort(review,actual_model,cli_hash):
    evidence=review.get('effort_evidence') or {}
    if not isinstance(evidence,dict):return None
    if (actual_model and evidence.get('model')==actual_model and
            evidence.get('cli_sha256')==cli_hash and evidence.get('method')=='reviewed_model_variant' and
            evidence.get('approval_reference') and evidence.get('logs') and
            review.get('effort') in {'low','medium','high'}):
        return review['effort']
    return None

def authorize(config,model,effort,cases,count,seconds,reference,resolve=None):
    value=ledger(config)
    if value['stop'] and not resolve:raise ValueError('前回の中止理由の解決と明示了承を記録してください。')
    if not model or effort not in {'low','medium','high'} or not set(cases)<=CASES or not cases:
        raise ValueError('モデル・effort・試験を明示してください。')
    if not 1<=count<=8 or not 1<=seconds<=600 or not reference.strip():raise ValueError('了承枠が無効です。')
    existing=next((a for a in value['approvals'] if a['reference']==reference),None)
    if existing:
        if (existing['model'],existing['effort'],existing['cases'],existing['limit'],existing['timeout'])!=(model,effort,cases,count,seconds):
            raise ValueError('同じ了承記録で枠を変更できません。')
        return existing['id']
    if value['total_launches']==0 and value['approvals']:raise ValueError('初回pilotの枠は既に記録されています。')
    if value['total_launches']==0 and (cases!=['normal'] or count!=1 or seconds!=30):
        raise ValueError('最初は normal 1送信・30秒だけです。')
    id=str(uuid.uuid4())
    value['approvals'].append({'id':id,'model':model,'effort':effort,'cases':cases,'limit':count,'used':0,'remaining':count,
        'timeout':seconds,'concurrency':1,'retries':0,'children':0,'reference':reference,'resolution':resolve,'created':time.time()})
    if resolve:value['stop']=None
    save(config.data/'validation-run-ledger.json',value)
    return id

def run_case(config,approval_id,case,resume_from=None):
    # The same server mutex prevents a validation send from racing an open WebUI.
    lock=Mutex(config.mutex_prefix+'_server')
    if not lock.acquire():lock.close();raise ValueError('WebUIを終了してから検証してください。')
    proc=None
    try:
        value=ledger(config)
        if value['stop']:raise ValueError('検証は中止状態です。解決と追加了承が必要です。')
        approval=next((a for a in value['approvals'] if a['id']==approval_id),None)
        if not approval or case not in approval['cases'] or approval['remaining']<=0:raise ValueError('この試験の残枠がありません。')
        current=snapshot(config);signature=fingerprint(current)
        review_path=config.data/'configuration-review.json'
        review=json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {}
        if review.get('fingerprint')!=signature or review.get('executable_customizations')!=[] or not review.get('auth_method') or not review.get('endpoint') or not review.get('loading_sources_verified'):
            raise ValueError('実設定・接続先・カスタマイズ読み込み元の確認記録が必要です。既存設定を自動変更しません。')
        cache=json.loads((config.data/'models.json').read_text(encoding='utf-8'))
        if cache.get('fingerprint')!=signature or approval['model'] not in cache['models']:raise ValueError('現在の構成から取得したモデル一覧を確認してください。')
        previous=None
        if case=='resume':
            previous=next((r for r in value['runs'] if r['id']==resume_from),None)
            if not previous or previous.get('terminal',{}).get('state')!='completed' or previous['fingerprint']!=signature or previous['model']!=approval['model'] or previous['effort']!=approval['effort']:
                raise ValueError('同じ構成で正常完了した検証会話だけ継続できます。')
        id=str(uuid.uuid4());cwd=Path(previous['workspace']) if previous else config.data/'validation'/'workspaces'/id
        cwd.mkdir(parents=True,exist_ok=True)
        from .controller import write_hooks,stream_arguments
        write_hooks(cwd)
        argv=[str(config.cli),*stream_arguments(cwd,approval['model'],approval['effort'],approval['timeout'])]
        cid=previous['terminal']['conversation_id'] if previous else None
        if cid:argv+=['--conversation',cid]
        spec={'parent_pid':os.getpid(),'mutex':config.mutex_prefix,'argv':argv,'cwd':str(cwd),'prompt':PROMPTS[case],
            'timeout':approval['timeout'],'model':approval['model'],'conversation_id':cid,'mock':False}
        if case=='hook_failure':spec['hook_fault']='exception'
        record={'id':id,'approval':approval_id,'case':case,'model':approval['model'],'effort':approval['effort'],'timeout':approval['timeout'],
            'fingerprint':signature,'workspace':str(cwd),'created':time.time(),'state':'reserved','actual_model':None,'actual_effort':None,'usage':None}
        # Reserve BEFORE spawning. A crash never refunds an uncertain launch.
        approval['used']+=1;approval['remaining']-=1;value['total_launches']+=1;value['runs'].append(record)
        value['stop']='検証中断または未報告。記録を照合してください。'
        save(config.data/'validation-run-ledger.json',value)
        events=[];lines=queue.Queue()
        proc=subprocess.Popen([sys.executable,'-m','agweb.supervisor'],cwd=APP_ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,env=dict(os.environ,PYTHONUTF8='1'))
        def control(kind,**fields):
            proc.stdin.write((json.dumps({'kind':kind,**fields})+'\n').encode());proc.stdin.flush()
        proc.stdin.write((json.dumps(spec,ensure_ascii=False)+'\n').encode());proc.stdin.flush()
        def read():
            for line in proc.stdout:
                try:lines.put(json.loads(line))
                except Exception:lines.put({'kind':'reader_error'})
        threading.Thread(target=read,daemon=True).start()
        started=time.monotonic();next_ping=0;manual=False;terminal=None
        while proc.poll() is None or not lines.empty():
            now=time.monotonic()
            if proc.poll() is None and now>=next_ping:
                control('server_ping');control('browser_ping');next_ping=now+1
            if case=='manual_stop' and not manual and now-started>=2:
                control('stop',reason='manual');manual=True
            if now-started>approval['timeout']+12:
                proc.kill();proc.wait();break
            try:event=lines.get(timeout=.05)
            except queue.Empty:continue
            events.append(event)
            if event['kind']=='terminal':terminal=event
        proc.wait();proc.stdin.close();proc.stdout.close()
        guards=[e for e in events if e['kind']=='guard']
        configurations=[e for e in events if e['kind']=='configuration']
        record.update(terminal=terminal,state='reported',events=events,elapsed=time.monotonic()-started,
            actual_model=next((g.get('model') for g in guards if g['stage']=='pre_invocation'),None),
            actual_effort=next((c.get('effort') for c in configurations if c.get('effort')),None),usage=(terminal or {}).get('usage'))
        record['reported_effort']=record['actual_effort']
        if record['actual_effort'] is None:
            resolved=reviewed_effort(review,record['actual_model'],current['cli_sha256'])
            if resolved==approval['effort']:
                record['actual_effort']=resolved;record['effort_evidence']=review['effort_evidence']
                record['effort_basis']='reviewed_model_variant'
        expected={'normal':None,'resume':None,'web':None,'deny':'guard_denied','hook_failure':'guard_error','manual_stop':'manual','timeout':'time_limit'}[case]
        passed=bool(terminal and terminal['active']==0 and terminal['reason']==expected)
        if case in {'normal','resume','web'}:passed=passed and terminal['state']=='completed'
        if case=='web':passed=passed and REQUIRED_TOOLS<={g.get('tool') for g in guards if g.get('stage')=='tool'}
        if case in {'normal','resume','web'}:passed=passed and record['actual_model']==approval['model']
        record['passed']=passed
        if not passed:value['stop']='受入失敗。追加実行を停止しました。'
        elif record['usage'] is None or record['actual_model'] is None or record['actual_effort'] is None:
            value['stop']='実設定または消費が不明です。追加実行を停止しました。'
        else:value['stop']=None
        record_path=config.data/'validation'/(id+'.json');save(record_path,record)
        record['receipt_sha256']=sha(record_path)
        save(config.data/'validation-run-ledger.json',value)
        return {'id':id,'passed':passed,'terminal':terminal,'actual_model':record['actual_model'],'actual_effort':record['actual_effort'],
            'usage':record['usage'],'remaining':approval['remaining'],'stop':value['stop']}
    finally:
        if proc and proc.poll() is None:proc.kill();proc.wait()
        lock.close()

def finalize(config):
    value=ledger(config);current=snapshot(config);signature=fingerprint(current)
    if value['stop']:raise ValueError('未解決の中止があります。')
    review=json.loads((config.data/'configuration-review.json').read_text(encoding='utf-8'))
    if value['stop'] or review.get('fingerprint')!=signature or review.get('executable_customizations')!=[] or not review.get('loading_sources_verified'):
        raise ValueError('未解決の中止または設定確認不足があります。')
    selected=[r for r in value['runs'] if r.get('passed') and r['fingerprint']==signature]
    cases={r['case'] for r in selected}
    if all((r.get('terminal') or {}).get('active')==0 for r in selected):cases.add('backend_cleanup')
    if not REQUIRED_CASES<=cases:raise ValueError('未合格: '+', '.join(sorted(REQUIRED_CASES-cases)))
    # Every model/effort pair must independently pass the full set.
    pairs=[]
    for model,effort in {(r['model'],r['effort']) for r in selected}:
        found={r['case'] for r in selected if r['model']==model and r['effort']==effort}
        if CASES<=found:pairs.append({'model':model,'effort':effort})
    if not pairs:raise ValueError('同じモデル・effortで全試験を完了してください。')
    receipts={r['id']:r['receipt_sha256'] for r in selected}
    proof={'schema':1,'fingerprint':signature,'passed_cases':sorted(cases),'models':sorted({p['model'] for p in pairs}),
        'tested_pairs':pairs,'customizations_verified':True,'receipts':receipts,'review_sha256':sha(config.data/'configuration-review.json'),'created':time.time()}
    save(config.data/'live-acceptance.json',proof)
    return proof

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='実モデル試験は了承済みの1件だけ。再送・再試行なし。')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status')
    auth=commands.add_parser('authorize');auth.add_argument('--model',required=True);auth.add_argument('--effort',required=True)
    auth.add_argument('--case',action='append',required=True);auth.add_argument('--count',type=int,default=1);auth.add_argument('--seconds',type=int,default=30)
    auth.add_argument('--approval-reference',required=True);auth.add_argument('--resolution')
    run=commands.add_parser('run');run.add_argument('--approval',required=True);run.add_argument('--case',choices=sorted(CASES),required=True);run.add_argument('--resume-from')
    commands.add_parser('finalize')
    args=parser.parse_args();config=Config(APP_ROOT,'validation-only',0)
    lock=Mutex(config.mutex_prefix+'_validation')
    if not lock.acquire():lock.close();print('別の検証操作が実行中です。');return 1
    try:
        if args.command=='status':result={'configuration':snapshot(config),'ledger':ledger(config)}
        elif args.command=='authorize':result={'approval':authorize(config,args.model,args.effort,args.case,args.count,args.seconds,args.approval_reference,args.resolution)}
        elif args.command=='run':result=run_case(config,args.approval,args.case,args.resume_from)
        else:result=finalize(config)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False));return 1
    finally:lock.close()

if __name__=='__main__':raise SystemExit(main())
