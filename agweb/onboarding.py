"""Local human consent is not a live-model acceptance receipt."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

from . import gate


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def preflight(config):
    issues=[]
    if not config.cli.is_file():issues.append('Antigravity CLIが見つかりません。公式CLIを導入するか Setup.ps1 -CliPath で指定してください。')
    try:
        config.data.mkdir(parents=True,exist_ok=True)
        from .controller import write_hooks
        with tempfile.TemporaryDirectory(prefix='preflight-',dir=config.data) as folder:
            write_hooks(Path(folder))
    except (OSError,ValueError):
        issues.append('データ書込またはHookの起動パスを確認できません。書込可能で空白・特殊文字のない場所（例 C:\\AI\\AntigravityWebUI）へ展開してください。')
    return issues


def _active(value):
    if isinstance(value,dict):
        if value.get('enabled') is False:return False
        return any(_active(v) for v in value.values())
    if isinstance(value,list):return any(_active(v) for v in value)
    return bool(value)


def customization_findings(config, snapshot):
    """Inspect known executable sections. Never return their contents/commands."""
    findings=[]
    names={'hooks.json','plugins.json','mcp_config.json','sidecars.json'}
    executable={'hooks','plugins','mcpservers','mcp_servers','mcpserversconfig','sidecars'}
    builtin=Path.home()/'.gemini/antigravity-cli/builtin'
    for name,digest in snapshot['files'].items():
        path=Path(name)
        if digest is None or path.suffix.lower()!='.json':continue
        # Only the known builtin tree is exempt, not an arbitrary CLI folder.
        if path.is_relative_to(builtin):continue
        if path.name not in names|{'settings.json','config.json'}:continue
        try:
            value=read_json(path)
            if not isinstance(value,dict):raise ValueError()
            def contains(node):
                if isinstance(node,dict):
                    return any((key.lower() in executable and _active(item)) or contains(item)
                               for key,item in node.items())
                if isinstance(node,list):return any(contains(item) for item in node)
                return False
            if (path.name in names and _active(value)) or contains(value):
                findings.append({'path':name,'reason':'外部の実行可能カスタマイズが設定されています。CLI側で無効化して再確認してください。'})
        except (OSError,ValueError,TypeError):
            findings.append({'path':name,'reason':'設定を解析できません。CLI側で内容と読込元を確認してください。'})
    return findings


def validate_review(review):
    if (not review.get('auth_method','').strip() or not review.get('endpoint','').strip() or
            any(review.get(key) is not True for key in ('proxy_reviewed','loading_sources_verified','no_external_customizations'))):
        raise ValueError('認証・接続先・プロキシ・読込元と、外部カスタマイズが無効であることの確認が必要です。')


def collect_version(config):
    from .diagnostics import collect_cli_text
    result=collect_cli_text(config,['--version'])
    match=re.search(r'(?im)^\s*(?:(?:Antigravity(?: CLI)?|agy)(?: version)?\s+)?v?(\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?)\s*$',result.pop('text',''))
    return {'ok':result['ok'] and bool(match),'version':match[1] if match else None}


def connection_check(config, review):
    validate_review(review)
    issues=preflight(config)
    before=gate.snapshot(config);signature=gate.fingerprint(before)
    findings=customization_findings(config,before)
    if issues or findings:return {'ok':False,'issues':issues,'findings':findings}
    from .diagnostics import collect_models
    version=collect_version(config)
    if not version['ok']:return {'ok':False,'issues':['CLIのバージョンを取得できません。認証とCLIの配置を通常ターミナルで確認してください。']}
    models=collect_models(config)
    after=gate.snapshot(config)
    if not models.get('ok'):return {'ok':False,'issues':[models.get('reason','モデル一覧を取得できません。')]}
    if gate.fingerprint(after)!=signature:return {'ok':False,'issues':['確認中に構成が変わりました。設定を確認してからやり直してください。']}
    result={'ok':True,'fingerprint':signature,'review':review,'version':version['version'],
            'models':models['models'],'snapshot':after}
    write_json(config.data/'setup-check.json',result)
    return {key:value for key,value in result.items() if key!='snapshot'}


def approve(config, body):
    from .store import Conflict
    review=body['review'];validate_review(review)
    if body['chat_consent'] is not True:raise ValueError('通常回答の利用への同意が必要です。')
    current=gate.snapshot(config);signature=gate.fingerprint(current)
    try:checked=read_json(config.data/'setup-check.json')
    except (OSError,ValueError):raise Conflict('先に接続を確認してください。') from None
    if (checked.get('ok') is not True or signature!=body['fingerprint'] or checked.get('fingerprint')!=signature or
            checked.get('review')!=review):raise Conflict('確認後に構成または確認内容が変わりました。接続を確認し直してください。')
    issues=preflight(config);findings=customization_findings(config,current)
    if issues or findings:raise ValueError('導入条件または外部カスタマイズを確認できません。初回設定を確認してください。')
    cache=read_json(config.data/'models.json')
    pairs=body['allowed_pairs']
    if (cache.get('fingerprint')!=signature or not pairs or len(pairs)>200 or
        any(set(pair)!={'model','effort'} or pair['model'] not in cache.get('models',[]) or
            pair['model'] not in checked['models'] or pair['effort'] not in {'low','medium','high'} for pair in pairs) or
        len({(p['model'],p['effort']) for p in pairs})!=len(pairs)):
        raise ValueError('取得済みモデルと考える量の組合せを重複なく選択してください。')
    proof={'schema_version':1,'fingerprint':signature,'snapshot':current,'review':review,'allowed_pairs':pairs,
           'chat_consent':True,'text_output_consent':body['text_output_consent'],
           'consented_at':datetime.now(timezone.utc).isoformat(),'cli_version':checked['version'],'live_tested':False}
    # Recheck after disk/preflight work, before the single atomic consent write.
    if gate.fingerprint(gate.snapshot(config))!=signature:raise Conflict('保存前に構成が変わりました。再確認してください。')
    write_json(config.data/'local-setup.json',proof)
    return local_status(config,current,signature)


def local_status(config,current,signature):
    blocked={'ready':False,'mode':'real','source':'local_setup','setup_required':True,'fingerprint':signature,
             'operational':True,'models':[],'allowed_pairs':[],'tested_pairs':[],'text_output_ready':False}
    try:
        proof=read_json(config.data/'local-setup.json')
        validate_review(proof['review'])
        if (proof['schema_version']!=1 or proof['chat_consent'] is not True or not proof['consented_at'] or
                proof['live_tested'] is not False or not isinstance(proof['text_output_consent'],bool) or
                not proof['cli_version'] or gate.fingerprint(proof['snapshot'])!=proof['fingerprint']):raise ValueError()
        if proof['fingerprint']!=signature:
            before=proof['snapshot']
            changed=[key for key in current if before.get(key)!=current[key]]
            code='cli_changed' if before.get('cli_sha256')!=current.get('cli_sha256') else 'configuration_changed'
            return {**blocked,'block_code':code,'changed_sections':changed,'reason':'CLI・アプリまたは関連設定が変更されています。初回設定で再確認してください。'}
        if not current.get('cli_sha256') or customization_findings(config,current):raise ValueError()
        cache=read_json(config.data/'models.json')
        if cache.get('fingerprint')!=signature:raise ValueError()
        pairs=proof['allowed_pairs']
        if not pairs or any(set(p)!={'model','effort'} or not isinstance(p['model'],str) or
                            p['effort'] not in {'low','medium','high'} for p in pairs):raise ValueError()
        allowed=[p for p in pairs if p['model'] in cache.get('models',[])]
        if not allowed:return {**blocked,'block_code':'no_operating_models','reason':'同意したモデルが最新一覧にありません。モデル一覧と初回設定を確認してください。'}
        return {**blocked,'ready':True,'setup_required':False,'allowed_pairs':allowed,'models':sorted({p['model'] for p in allowed}),
                'text_output_ready':proof['text_output_consent'],'cli_version':proof['cli_version'],'live_tested':False,
                'reason':'構成確認と利用同意済み。実CLIのモデル動作は未検証です。'}
    except (OSError,ValueError,KeyError,TypeError):
        return {**blocked,'reason':'初回設定の記録を確認できません。構成と利用同意を確認し直してください。'}


def setup_status(config):
    issues=preflight(config)
    try:
        current=gate.snapshot(config)
        sources=[{'path':p,'sha256':digest} for p,digest in current['files'].items()]
        findings=customization_findings(config,current)
        result={'cli_path':str(config.cli),'fingerprint':gate.fingerprint(current),'sources':sources,'findings':findings,
                'environment_names':sorted(current['overrides']),'issues':issues,'review':None,'allowed_pairs':[],
                'quota_folder':str((config.data/'diagnostics/quota').resolve()),'validation_baseline':'1.3.0'}
        try:
            proof=read_json(config.data/'local-setup.json')
            result.update(review=proof.get('review'),allowed_pairs=proof.get('allowed_pairs',[]))
        except (OSError,ValueError,AttributeError):pass
        return result
    except (OSError,ValueError,TypeError,KeyError):
        return {'cli_path':str(config.cli),'issues':issues+['関連設定を読込できません。ファイルの権限と形式を確認してください。'],
                'sources':[],'findings':[],'environment_names':[],'review':None,'allowed_pairs':[]}
