"""Only the fixed /usage TUI command is supported; never a chat prompt."""
import codecs
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import pyte
from .config import APP_ROOT
from . import gate


class UsageScreen:
    def __init__(self,limit=1024*1024):
        self.screen=pyte.Screen(140,100);self.stream=pyte.Stream(self.screen)
        self.decoder=codecs.getincrementaldecoder('utf-8')('strict')
        self.size=0;self.limit=limit
        self.panel_state='not_checked'

    def feed(self,data):
        self.size+=len(data)
        if self.size>self.limit:raise ValueError('output_limit')
        self.stream.feed(self.decoder.decode(data,final=False))

    def lines(self):
        return [line.rstrip() for line in self.screen.display]

    def ready(self):
        lines=self.lines()
        return any(re.search(r'\bAntigravity CLI \d+\.\d+\.\d+(?:\b|-)',line) for line in lines) and any(
            re.fullmatch(r'\s*[>❯]\s*',line) for line in lines) and any('for shortcuts' in line for line in lines)

    def blocked_reason(self):
        text='\n'.join(self.lines())
        if 'Do you trust the contents of this project?' in text:
            return '診断用フォルダーの信頼確認が必要です。自動承認せず、クォータ取得を停止しました。'
        if re.search(r'Sign in to|Log in to|Choose.*authentication',text,re.I):
            return 'CLIの認証確認が必要です。通常ターミナルで認証状態を確認してください。'
        return None

    def panel(self):
        lines=self.lines()
        self.panel_state='title_missing'
        start=next((i for i,l in enumerate(lines) if re.fullmatch(r'\s*(?:└\s*)?Models & Quota\s*',l)),None)
        if start is None:return None
        self.panel_state='footer_missing'
        end=next((i for i in range(start+1,len(lines)) if re.search(r'Esc.*close',lines[i],re.I)),None)
        if end is None:return None
        rows=[line.strip() for line in lines[start+1:end] if line.strip()]
        shared_limits=('Weekly Limit Remaining','Five Hour Limit Remaining')
        groups=[('GEMINI MODELS',shared_limits),('CLAUDE AND GPT MODELS',shared_limits)]
        if 'GEMINI 4 ARGON' in rows:
            groups.append(('GEMINI 4 ARGON',('Gemini 4 Argon Weekly Limit',)))
        self.panel_state='incomplete_groups'
        if any(rows.count(group)!=1 for group,_ in groups):return None
        positions=[rows.index(group) for group,_ in groups]+[len(rows)]
        if positions!=sorted(positions):return None
        result=['Models & Quota']
        for i,(group,limits) in enumerate(groups):
            section=rows[positions[i]+1:positions[i+1]]
            result+=['',group]
            self.panel_state='incomplete_limits'
            if any(section.count(label)!=1 for label in limits):return None
            for label in limits:
                index=section.index(label)
                self.panel_state='incomplete_values'
                if index+2>=len(section):return None
                bar,reset=section[index+1:index+3]
                match=re.fullmatch(r'\[[█░]+\]\s+(\d+(?:\.\d+)?)%',bar)
                if not match or not 0<=float(match[1])<=100:return None
                if not re.fullmatch(r'Quota available|Refreshes in \d+[hms](?:\s+\d+[hms])*',reset):return None
                # Only these strictly recognized quota fields leave the worker.
                # Account banners and unrelated explanations are never returned.
                result+=['  '+label,'    '+bar,'    '+reset]
        self.panel_state='complete'
        return '\n'.join(result)


def verified_configuration(config):
    if config.mock:return True
    if (config.data/'local-setup.json').exists():
        return gate.status(config).get('ready',False)
    try:
        reviewed=json.loads((config.data/'validation/quota-buttons-baseline.json').read_text(encoding='utf-8'))
        before=reviewed['snapshot'];current=gate.snapshot(config)
        review=json.loads((config.data/'configuration-review.json').read_text(encoding='utf-8'))
        # Relocation changes the ancestor paths recorded as absent. Every existing
        # configuration file must still match, including additions and removals.
        before_files={p:h for p,h in before['files'].items() if h is not None}
        current_files={p:h for p,h in current['files'].items() if h is not None}
        return (current['cli_sha256']==before['cli_sha256'] and current_files==before_files
            and current['overrides']==before['overrides'] and current['cli_environment']==before['cli_environment']
            and review['loading_sources_verified'] and review['executable_customizations']==[])
    except Exception:return False


def quota_workdir(config):
    if config.mock:return config.data/'diagnostics'/'quota'
    dedicated=(config.data/'diagnostics'/'quota').resolve()
    local=(config.data/'local-setup.json').exists()
    # The TUI trusts exact workspace paths; a new diagnostics subfolder prompts.
    # Reuse only the already reviewed profile root, never grant trust implicitly.
    try:
        profile=Path.home().resolve()
        settings=json.loads((profile/'.gemini/antigravity-cli/settings.json').read_text(encoding='utf-8'))
        trusted=settings.get('trustedWorkspaces')
        if isinstance(trusted,str):trusted=[trusted]
        if isinstance(trusted,list) and all(isinstance(p,str) for p in trusted) and any(
                Path(p).is_absolute() and Path(p).resolve()==(dedicated if local else profile) for p in trusted):
            if local:dedicated.mkdir(parents=True,exist_ok=True)
            return dedicated if local else profile
    except (OSError,ValueError):pass
    raise ValueError('CLIの信頼済みフォルダーを確認できません。初回設定に表示するクォータ専用フォルダーで、通常の agy を開いて信頼確認だけを行ってください。')


def collect_quota(config):
    if not config.mock and not (config.data/'local-setup.json').exists():
        try:
            adapter=json.loads((config.data/'quota-adapter.json').read_text(encoding='utf-8'))
            if not adapter.get('ready'):
                return {'ok':False,'reason':adapter.get('reason','クォータ取得は実CLI接続確認待ちです。')}
        except Exception:return {'ok':False,'reason':'クォータ取得は実CLI接続確認待ちです。'}
    try:cwd=quota_workdir(config)
    except ValueError as e:return {'ok':False,'reason':str(e)}
    if config.mock:cwd.mkdir(parents=True,exist_ok=True)
    spec={'parent_pid':os.getpid(),'mutex':config.mutex_prefix,'cli':str(config.cli),
        'cwd':str(cwd),'mock':config.mock}
    try:
        proc=subprocess.Popen([sys.executable,'-m','agweb.quota_worker'],cwd=APP_ROOT,
            env=dict(os.environ,PYTHONUTF8='1'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:out,_=proc.communicate(json.dumps(spec).encode(),timeout=38)
        except subprocess.TimeoutExpired as e:
            # A failed zero-process confirmation keeps its worker and mutex alive.
            try:
                value=json.loads(e.output or b'')
                if value.get('active_processes',0)>0:return value
            except Exception:pass
            proc.kill();proc.communicate(timeout=5)
            return {'ok':False,'reason':'クォータ確認の終了を確認できません。'}
        if len(out)>64*1024:raise ValueError('worker output')
        result=json.loads(out)
        diagnostic={k:result[k] for k in ('ok','reason','command_submitted','active_processes','diagnostics') if k in result}
        diagnostic['checked_at']=datetime.now(timezone.utc).isoformat()
        try:(config.data/'quota-last-result.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:pass
        if result.get('ok'):result['fetched_at']=datetime.now(timezone.utc).isoformat()
        return result
    except Exception:return {'ok':False,'reason':'クォータ確認を起動できません。'}
