import asyncio
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import win32api
from .config import APP_ROOT, TERMINAL
from .output import registered_folder

def stream_arguments(cwd,model,effort,timeout):
    # This CLI does not discover workspace hooks from cwd alone in headless mode.
    # Mount only the app-owned, empty conversation workspace explicitly.
    return ['--add-dir',str(cwd),'--input-format','stream-json','--output-format','stream-json',
        '--disable-slash-commands','--model',model,'--effort',effort,'--print-timeout',str(timeout)+'s']

def write_hooks(cwd):
    hooks=cwd/'.agents'/'hooks.json'
    hooks.parent.mkdir(parents=True,exist_ok=True)
    hook_script=APP_ROOT/'agweb'/'hook.py'
    # The installed Windows CLI escapes quoted executable names incorrectly.
    # Keep its command token free of quotes and independent of the hook cwd.
    # The wrapper itself quotes the fixed Python/guard paths correctly.
    wrapper=cwd/'agweb_guard.cmd'
    # Hidden cmd.exe starts with the system code page, independently of Python.
    # Switch before it reads the UTF-8 paths below (installation may be Japanese).
    wrapper.write_text('@echo off\r\nchcp 65001 >nul\r\nsetlocal DisableDelayedExpansion\r\n'
        f'"{sys.executable}" "{hook_script}" %1\r\nexit /b %errorlevel%\r\n',encoding='utf-8',newline='')
    command=str(wrapper.resolve())
    unsafe=' \t\r\n"&|<>^()%!'
    if any(char in command for char in unsafe):
        command=win32api.GetShortPathName(command)
    if any(char in command for char in unsafe):
        raise ValueError('ガードの起動パスを安全に指定できません。空白や特殊文字のない配置先が必要です。')
    hooks.write_text(json.dumps({'agweb-guard':{
        'PreInvocation':[{'type':'command','command':command+' pre_invocation','timeout':3}],
        'PreToolUse':[{'matcher':'*','hooks':[{'type':'command','command':command+' pre_tool','timeout':3}]}]
    }},indent=2),encoding='utf-8')

class Controller:
    def __init__(self,config,store):
        self.config,self.store=config,store
        self.proc=None
        self.id=None
        self.owner=None
        self.task=None
        self.controls=None
        self.save_failed=False

    @property
    def busy(self):
        return self.proc is not None

    def command(self,kind,**fields):
        if self.controls is not None:
            try:self.controls.put_nowait({'kind':kind,**fields})
            except queue.Full:self.save_failed=True

    def launch(self,run,prompt,conversation,scenario='normal'):
        if self.busy:raise RuntimeError('Already running')
        cwd=self.config.data/'workspaces'/conversation['id']
        self.store._deletion_path(cwd)
        cwd.mkdir(parents=True,exist_ok=True)
        write_hooks(cwd)
        self.store.sync_instructions(conversation['id'])
        output_target=None
        if conversation.get('output_mode')=='text_file':
            folder=registered_folder(run['output_root'])
            output_target=folder/'Novel.txt'
            prompt+='\n\nWebUIのテキストファイル出力指定:\n保存フォルダー: '+str(folder)+'\n'+(
                '上記フォルダー直下に、依頼で指定された名前の.txtを絶対パスで指定し、'
                '完成した本文をUTF-8のプレーンテキストとしてwrite_to_fileで一度だけ新規作成してください。'
                '名前の指定がない場合はNovel.txtにしてください。同名がある場合はWebUIが連番を付けます。'
                'Overwrite=false、IsArtifact=falseとし、本文全体をCodeContentに渡してください。'
                '既存ファイルの参照・編集・フォルダー作成・コマンド実行は不要です。'
                '本文をチャット回答へ繰り返さず、最後は保存完了の短い通知だけにしてください。')
        if self.config.mock:
            argv=[sys.executable,str(APP_ROOT/'tests'/'mock_cli.py')]
        else:
            argv=[str(self.config.cli)]
        argv+=stream_arguments(cwd,conversation['model'],conversation['effort'],run['timeout'])
        if conversation['cli_id']:argv+=['--conversation',conversation['cli_id']]
        spec={'parent_pid':os.getpid(),'mutex':self.config.mutex_prefix,'argv':argv,'cwd':str(cwd),'prompt':prompt,
            'timeout':run['timeout'],'conversation_id':conversation['cli_id'],'model':conversation['model'],
            'mock':self.config.mock,'scenario':self.config.mock_scenario,'browser_timeout':self.config.browser_timeout,
            'server_timeout':self.config.server_timeout,'cleanup_timeout':self.config.cleanup_timeout}
        if output_target:spec.update(output_target=str(output_target),output_root=run['output_root'])
        self.id=run['id']
        self.owner=secrets.token_urlsafe(32)
        self.controls=queue.Queue(maxsize=64)
        env=dict(os.environ,PYTHONUTF8='1')
        self.proc=subprocess.Popen([sys.executable,'-m','agweb.supervisor'],cwd=APP_ROOT,env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        proc=self.proc
        control_queue=self.controls
        def writer():
            try:
                proc.stdin.write((json.dumps(spec,ensure_ascii=False)+'\n').encode());proc.stdin.flush()
                while True:
                    item=control_queue.get()
                    if item is None:break
                    proc.stdin.write((json.dumps(item)+'\n').encode());proc.stdin.flush()
            except (OSError,ValueError):pass
            finally:
                try:proc.stdin.close()
                except OSError:pass
        threading.Thread(target=writer,daemon=True).start()
        self.task=asyncio.create_task(self.pump(proc,run['id']))
        return self.owner

    async def pump(self,proc,id):
        ping=asyncio.create_task(self.ping_loop(proc))
        terminal=False
        chunks=[];size=0;flush_at=0;reading=None
        def persist(payload):
            try:self.store.event(id,payload)
            except Exception:
                self.save_failed=True
                self.command('stop',reason='persistence_failed')
        def flush():
            nonlocal size
            if chunks:
                persist({'kind':'delta','text':''.join(chunks)})
                chunks.clear();size=0
        try:
            while True:
                if reading is None:reading=asyncio.create_task(asyncio.to_thread(proc.stdout.readline))
                if chunks:
                    # Shield the single pending read: timeout flushes must not start
                    # concurrent reads on the same pipe or discard a late line.
                    done,_=await asyncio.wait({reading},timeout=max(0,flush_at-asyncio.get_running_loop().time()))
                    if not done:
                        flush();continue
                line=await reading;reading=None
                if not line:
                    flush();break
                payload=json.loads(line)
                if payload['kind']=='delta':
                    if not chunks:flush_at=asyncio.get_running_loop().time()+.075
                    chunks.append(payload['text']);size+=len(payload['text'].encode('utf-8'))
                    if size>=8192 or asyncio.get_running_loop().time()>=flush_at:flush()
                    continue
                flush()  # Preserve ordering and partial text before stop/result/terminal.
                if payload['kind']=='terminal':terminal=True
                persist(payload)
            await asyncio.to_thread(proc.wait)
            if not terminal:
                # Job closure kills descendants, but without its owner's final receipt
                # we cannot claim an observed zero count. Block new sends until recovery.
                self.save_failed=True
                self.store.event(id,{'kind':'terminal','state':'failed','reason':'supervisor_lost','submitted':True,'active':None})
        except Exception:
            flush()
            self.save_failed=True
            self.command('stop',reason='persistence_failed')
            await asyncio.to_thread(proc.wait)
        finally:
            ping.cancel()
            proc.stdout.close()
            try:self.controls.put_nowait(None)
            except queue.Full:pass
            self.proc=None;self.controls=None;self.id=None;self.owner=None

    async def ping_loop(self,proc):
        while proc.poll() is None:
            self.command('server_ping')
            await asyncio.sleep(1)

    async def shutdown(self):
        if self.busy:
            self.command('stop',reason='app_shutdown')
            try:await asyncio.wait_for(asyncio.shield(self.task),10)
            except asyncio.TimeoutError:
                # Killing the owner closes its last Job handle; never kill unrelated CLI names.
                self.proc.kill()
                await self.task
