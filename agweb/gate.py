import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from .config import APP_ROOT, CLI_ENV_OVERRIDES, sha

REQUIRED_CASES={'normal','resume','web','deny','hook_failure','manual_stop','timeout','backend_cleanup'}
REQUIRED_TOOLS={'search_web','read_url_content'}

def snapshot(config):
    paths=set()
    paths.add(config.root/'webui.local.json')
    profile=Path(os.environ.get('USERPROFILE',''))
    gemini=profile/'.gemini'
    for name in ('AGENTS.md','GEMINI.md','settings.json','config/config.json','config/hooks.json','config/plugins.json','config/mcp_config.json','config/skills.json','config/sidecars.json','antigravity-cli/settings.json'):
        paths.add(gemini/name)
    # Fingerprint discovered configuration/customization files, not conversation state or credentials.
    roots=[gemini/'config',profile/'.agents',profile/'.agent',config.cli.parent]
    roots.extend(gemini/'antigravity-cli'/name for name in ('.agents','.agent','builtin','rules','skills','plugins'))
    for ancestor in config.data.resolve().parents:
        roots.extend(ancestor/name for name in ('.agents','.agent'))
        for name in ('AGENTS.md','GEMINI.md'):
            paths.add(ancestor/name)
    review_path=config.data/'configuration-review.json'
    if review_path.exists():
        review=json.loads(review_path.read_text(encoding='utf-8'))
        roots.extend(Path(p) for p in review.get('watch_roots',[]))
    for root in roots:
        if root.exists():
            if root.is_file():paths.add(root)
            else:
                for p in root.rglob('*'):
                    if p.is_file() and not p.is_relative_to(config.data.resolve()) and not any(
                            part in {'.git','.venv','validation-results','releases','__pycache__','.pytest_cache'} for part in p.relative_to(root).parts):paths.add(p)
    files={str(p):sha(p) if p.is_file() else None for p in sorted(paths)}
    code={str(p.relative_to(APP_ROOT)):sha(p) for p in sorted((APP_ROOT/'agweb').glob('*.py'))}
    code.update({str(p.relative_to(APP_ROOT)):sha(p) for p in sorted((APP_ROOT/'static').rglob('*')) if p.is_file()})
    for name in ('launch.py','requirements.lock.txt'):code[name]=sha(APP_ROOT/name)
    overrides={k:hashlib.sha256(v.encode()).hexdigest() for k,v in os.environ.items() if any(x in k.upper() for x in ('PROXY','AGY','ANTIGRAVITY','GEMINI','GOOGLE')) and not k.startswith('AGWEB_')}
    runtime={'python':sys.version,'python_exe':sha(Path(sys.executable)),'dependencies':{name:version(name) for name in ('fastapi','uvicorn','pywin32','python-multipart','pyte','wcwidth')}}
    return {'cli':str(config.cli),'cli_sha256':sha(config.cli) if config.cli.is_file() else None,'files':files,'code':code,'overrides':overrides,'cli_environment':CLI_ENV_OVERRIDES,'runtime':runtime}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

def operation_status(config,current,signature):
    # An explicit human decision to operate with incomplete acceptance is
    # separate from full live acceptance. Runtime guards are not bypassed.
    proof=json.loads((config.data/'operation-approval.json').read_text(encoding='utf-8'))
    review_path=config.data/'configuration-review.json'
    review=json.loads(review_path.read_text(encoding='utf-8'))
    if proof.get('cli_sha256')!=current['cli_sha256']:
        return {'ready':False,'mode':'real','block_code':'cli_changed','fingerprint':signature,
            'reason':'Antigravity CLIの更新を検出したため送信を無効化しました。更新後の構成を確認してから運用を再開してください。'}
    if proof.get('fingerprint')!=signature:
        return {'ready':False,'mode':'real','block_code':'configuration_changed','fingerprint':signature,
            'reason':'アプリまたは関連設定の変更を検出したため送信を無効化しました。構成の確認が必要です。'}
    if (not proof.get('approval_reference') or proof.get('review_sha256')!=sha(review_path) or
            review.get('fingerprint')!=signature or review.get('executable_customizations')!=[] or
            not review.get('loading_sources_verified') or not review.get('auth_method') or not review.get('endpoint')):
        raise ValueError('operation configuration changed')
    pairs=proof.get('tested_pairs',[])
    if not pairs or not proof.get('receipts') or not proof.get('baseline_fingerprint'):
        raise ValueError('operation evidence missing')
    receipts=[]
    import uuid
    for id,expected in proof['receipts'].items():
        uuid.UUID(id)
        path=config.data/'validation'/(id+'.json')
        if sha(path)!=expected:raise ValueError('operation receipt changed')
        r=json.loads(path.read_text(encoding='utf-8'))
        terminal=r.get('terminal') or {}
        if (r.get('fingerprint')!=proof['baseline_fingerprint'] or r.get('passed') is not True or
                terminal.get('state')!='completed' or terminal.get('active')!=0 or terminal.get('exit_code')!=0):
            raise ValueError('invalid operation receipt')
        receipts.append(r)
    for pair in pairs:
        matching=[r for r in receipts if r.get('actual_model')==pair['model'] and r.get('actual_effort')==pair['effort']]
        if not {'normal','resume'}<={r['case'] for r in matching}:raise ValueError('unconfirmed operation pair')
    allowed=proof.get('operating_pairs',pairs)
    if allowed!=pairs:
        if not proof.get('model_selection_reference'):raise ValueError('model selection not approved')
    if not allowed or any(set(p)!={'model','effort'} or not isinstance(p['model'],str) or not p['model'] or
            p['effort'] not in {'low','medium','high'} for p in allowed):
        raise ValueError('invalid operating model selection')
    cache=json.loads((config.data/'models.json').read_text(encoding='utf-8'))
    if cache.get('fingerprint')!=signature:raise ValueError('model source changed')
    # Keep approval and historical receipts intact; availability can shrink independently.
    allowed=[p for p in allowed if p['model'] in cache.get('models',[])]
    if not allowed:
        return {'ready':False,'mode':'real','operational':True,'block_code':'no_operating_models',
            'models':[],'allowed_pairs':[],'tested_pairs':pairs,'fingerprint':signature,
            'reason':'承認済みのモデルが最新一覧にありません。モデル一覧を更新するか、利用可能なモデルの運用設定を確認してください。'}
    models=sorted({p['model'] for p in allowed})
    updated=proof.get('cli_update_without_live_test') is True
    reason=('運用モード：CLI更新を了承済み。更新版での実モデル動作は未確認です。' if updated else
        '運用モード：追加試験を省略。Web操作は実運用で確認します。')
    return {'ready':True,'mode':'real','operational':True,'reason':reason,'cli_update_without_live_test':updated,
        'models':models,'allowed_pairs':allowed,'tested_pairs':pairs,'unverified_cases':proof.get('unverified_cases',[]),'fingerprint':signature}

def status(config):
    if config.mock:
        return {'ready':True,'mode':'mock','reason':'模擬CLI検証モード（実モデルは使用しません）','models':['mock-model']}
    try:
        current=snapshot(config)
        signature=fingerprint(current)
        if (config.data/'local-setup.json').exists():
            from .onboarding import local_status
            return local_status(config,current,signature)
        if (config.data/'operation-approval.json').exists():
            return operation_status(config,current,signature)
        proof_path=config.data/'live-acceptance.json'
        if not proof_path.exists():
            return {'ready':False,'mode':'real','setup_required':True,'reason':'初回設定で構成を確認し、利用に同意してください。','fingerprint':signature}
        proof=json.loads(proof_path.read_text(encoding='utf-8'))
        if proof.get('fingerprint')!=signature or not REQUIRED_CASES <= set(proof.get('passed_cases',[])):
            return {'ready':False,'mode':'real','reason':'構成変更または受入不足のため送信を無効化しています。','fingerprint':signature}
        if not proof.get('models') or not proof.get('customizations_verified'):
            raise ValueError('incomplete acceptance')
        if not proof.get('receipts') or not proof.get('tested_pairs'):raise ValueError('missing evidence')
        if proof.get('review_sha256')!=sha(config.data/'configuration-review.json'):raise ValueError('review changed')
        receipts=[]
        for id,expected in proof['receipts'].items():
            import uuid
            uuid.UUID(id)
            path=config.data/'validation'/(id+'.json')
            if sha(path)!=expected:raise ValueError('receipt changed')
            receipt=json.loads(path.read_text(encoding='utf-8'))
            if receipt.get('fingerprint')!=signature or receipt.get('passed') is not True or receipt.get('terminal',{}).get('active')!=0:
                raise ValueError('invalid receipt')
            receipts.append(receipt)
        for pair in proof['tested_pairs']:
            matching=[r for r in receipts if r.get('model')==pair['model'] and r.get('effort')==pair['effort']]
            if not REQUIRED_CASES-{'backend_cleanup'} <= {r.get('case') for r in matching}:raise ValueError('missing pair acceptance')
            tools={g.get('tool') for r in matching for g in r.get('events',[]) if g.get('kind')=='guard' and g.get('stage')=='tool'}
            if not REQUIRED_TOOLS<=tools:raise ValueError('web adapter unverified')
        return {'ready':True,'mode':'real','reason':'受入済み構成','models':proof['models'],'tested_pairs':proof['tested_pairs'],'fingerprint':signature}
    except Exception:
        return {'ready':False,'mode':'real','reason':'構成を確認できないため送信を無効化しています。'}
