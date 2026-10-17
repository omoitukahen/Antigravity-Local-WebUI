import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import secrets
import os
import subprocess
import uuid
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from .config import APP_ROOT, TERMINAL, sha
from .controller import Controller
from .store import Store, Conflict, DeletionError
from .output import registered_folder
from .windows import Mutex
from . import gate

class ConversationBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    model:str=Field(min_length=1,max_length=200)
    effort:str
    system_prompt:str=Field(default='',max_length=20000)
    output_mode:Literal['chat','text_file']='chat'

class ConversationUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    title:str=Field(default=None,max_length=1000)
    is_favorite:bool=Field(default=None)
    system_prompt:str=Field(default=None,max_length=20000)
    revision:int=Field(default=None,ge=0)
    output_mode:Literal['chat','text_file']=Field(default=None)

class RunBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    request_id:str=Field(min_length=1,max_length=100)
    revision:int=Field(ge=0)
    text:str=Field(max_length=20000)
    attachment_ids:list[str]=Field(default_factory=list,max_length=10)
    timeout:int=Field(default=180,ge=30,le=600)

class OutputSettingsBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    folder:str=Field(min_length=1,max_length=2000)
    revision:int=Field(ge=0)

class SetupReview(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    auth_method:str=Field(min_length=1,max_length=200)
    endpoint:str=Field(min_length=1,max_length=300)
    proxy_reviewed:bool
    loading_sources_verified:bool
    no_external_customizations:bool

class SetupCheckBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    review:SetupReview

class ModelPair(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    model:str=Field(min_length=1,max_length=200)
    effort:Literal['low','medium','high']

class SetupBody(SetupCheckBody):
    fingerprint:str=Field(min_length=64,max_length=64)
    allowed_pairs:list[ModelPair]=Field(min_length=1,max_length=200)
    chat_consent:bool
    text_output_consent:bool

class ApiBodyLimit:
    """Bound chunked bodies too, before JSON or multipart parsing writes files."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope['path'].startswith('/api/') or scope['method'] not in {'POST','PUT','PATCH'}:
            return await self.app(scope,receive,send)
        parts=[];size=0
        while True:
            item=await receive()
            if item['type']=='http.disconnect':return
            size+=len(item.get('body',b''))
            if size>6*1024*1024:
                return await JSONResponse({'detail':'リクエストが大きすぎます。'},status_code=413)(scope,receive,send)
            parts.append(item)
            if not item.get('more_body',False):break
        index=0
        async def bounded_receive():
            nonlocal index
            if index<len(parts):
                item=parts[index];index+=1;return item
            return await receive()
        await self.app(scope,bounded_receive,send)

def create_app(config):
    @asynccontextmanager
    async def lifespan(app):
        store=Store(config.data)
        execution=Mutex(config.mutex_prefix+'_run')
        free=execution.acquire()
        if free:store.recover()
        execution.close()
        app.state.store=store
        app.state.controller=Controller(config,store)
        app.state.acceptance=await asyncio.to_thread(gate.status,config)
        app.state.external_busy=not free
        app.state.diagnostic_busy=False
        app.state.models=[] if not config.mock else ['mock-model']
        model_cache=config.data/'models.json'
        if not config.mock and model_cache.exists():
            try:
                value=json.loads(model_cache.read_text(encoding='utf-8'))
                app.state.models=value['models']
            except Exception:pass
        yield
        await app.state.controller.shutdown()
        store.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(ApiBodyLimit)
    app.state.shutdown_requested=False

    @app.middleware('http')
    async def boundary(request,call_next):
        expected=f'127.0.0.1:{config.port}'
        if request.headers.get('host')!=expected:
            return JSONResponse({'detail':'許可されていないHostです。'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin!=config.origin:
            return JSONResponse({'detail':'許可されていないOriginです。'},status_code=403)
        if request.url.path.startswith('/api/'):
            supplied=request.headers.get('authorization','')
            if not hmac.compare_digest(supplied,'Bearer '+config.token):
                return JSONResponse({'detail':'この起動用リンクから開き直してください。'},status_code=401)
            length=request.headers.get('content-length')
            if length:
                try:
                    if int(length)<0:raise ValueError()
                    if int(length)>6*1024*1024:return JSONResponse({'detail':'リクエストが大きすぎます。'},status_code=413)
                except ValueError:return JSONResponse({'detail':'Content-Lengthが無効です。'},status_code=400)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'none'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request,exc):return JSONResponse({'detail':'対象が見つかりません。'},status_code=404)
    @app.exception_handler(Conflict)
    async def conflict(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)

    @app.get('/')
    async def index():return FileResponse(APP_ROOT/'static'/'index.html')
    app.mount('/static',StaticFiles(directory=APP_ROOT/'static'),name='static')

    def busy():
        if app.state.controller.busy or app.state.diagnostic_busy:return True
        probe=Mutex(config.mutex_prefix+'_run')
        try:
            free=probe.acquire()
            if free and app.state.external_busy:
                app.state.store.recover()
                app.state.external_busy=False
            return not free
        finally:probe.close()

    @app.get('/api/status')
    async def status():
        # Display the last checked state. Sending still performs a fresh gate check.
        return {'acceptance':app.state.acceptance,'active':app.state.store.active(),'busy':busy(),
            'models':app.state.models,'persistence_failed':app.state.controller.save_failed,
            'deletion_warning':app.state.store.deletion_warning,'default_timeout':180,
            'text_output_ready':text_output_ready(),'output_settings':app.state.store.output_settings(),
            'setup_required':app.state.acceptance.get('setup_required',not app.state.acceptance['ready'])}

    @app.get('/api/setup')
    async def setup_info():
        from .onboarding import setup_status
        result=await asyncio.to_thread(setup_status,config)
        app.state.acceptance=await asyncio.to_thread(gate.status,config)
        return result

    @app.post('/api/setup/check')
    async def setup_check(body:SetupCheckBody):
        if busy():raise Conflict('実行中は初回設定を確認できません。')
        if config.mock:raise ValueError('模擬モードでは初回設定を保存しません。通常モードで確認してください。')
        from .onboarding import connection_check
        app.state.diagnostic_busy=True
        try:
            result=await asyncio.to_thread(connection_check,config,body.review.model_dump())
            if result.get('ok'):app.state.models=result['models']
            return result
        finally:app.state.diagnostic_busy=False

    @app.put('/api/setup')
    async def setup_save(body:SetupBody):
        if busy():raise Conflict('実行中は初回設定を保存できません。')
        if config.mock:raise ValueError('模擬モードでは初回設定を保存しません。')
        from .onboarding import approve
        # All CLI starts use this mutex too; never approve while an external worker runs.
        execution=Mutex(config.mutex_prefix+'_run')
        if not execution.acquire():
            execution.close();raise Conflict('別の処理が実行中です。')
        app.state.diagnostic_busy=True
        try:
            result=await asyncio.to_thread(approve,config,body.model_dump())
            app.state.acceptance=result
            return result
        finally:
            app.state.diagnostic_busy=False;execution.close()

    def text_output_ready():
        if config.mock:return True
        if (config.data/'local-setup.json').exists():
            return app.state.acceptance.get('ready',False) and app.state.acceptance.get('text_output_ready',False)
        try:
            proof=json.loads((config.data/'text-output-acceptance.json').read_text(encoding='utf-8'))
            id=proof['receipt_id']
            if str(uuid.UUID(id))!=id:return False
            path=config.data/'validation'/(id+'.json')
            if sha(path)!=proof['receipt_sha256']:return False
            receipt=json.loads(path.read_text(encoding='utf-8'))
            terminal=receipt.get('terminal',{})
            inherited=receipt.get('fingerprint')==proof['fingerprint']
            if not inherited:
                review_id=proof['review_id']
                if str(uuid.UUID(review_id))!=review_id:return False
                review_path=config.data/'validation'/(review_id+'.json')
                if sha(review_path)!=proof['review_sha256']:return False
                review=json.loads(review_path.read_text(encoding='utf-8'))
                inherited=(review.get('purpose')=='text_output_configuration_review' and review.get('passed') is True
                    and review.get('fingerprint')==proof['fingerprint']
                    and review.get('baseline_fingerprint')==receipt.get('fingerprint')
                    and review.get('receipt_id')==id and review.get('receipt_sha256')==proof['receipt_sha256']
                    and bool(review.get('approval_reference')) and review.get('additional_model_sends')==0)
            return (proof.get('passed') is True and proof.get('fingerprint')==app.state.acceptance.get('fingerprint')
                and receipt.get('passed') is True and inherited
                and terminal.get('state')=='completed' and terminal.get('active')==0 and terminal.get('exit_code')==0
                and receipt.get('guard_write_observed') is True and bool(receipt.get('usage')))
        except (OSError,ValueError,KeyError,TypeError):return False

    @app.post('/api/models/refresh')
    async def models():
        if busy():raise Conflict('実行中はモデル一覧を更新できません。')
        if config.mock:return {'models':['mock-model']}
        current=await asyncio.to_thread(gate.status,config)
        legacy=not (config.data/'local-setup.json').exists() and any(
            (config.data/name).exists() for name in ('operation-approval.json','live-acceptance.json'))
        if not legacy and not current['ready'] and current.get('block_code')!='no_operating_models':
            raise Conflict('先に初回設定で読込元と構成を確認し、「接続を確認」を実行してください。')
        if busy():raise Conflict('別の処理が実行中です。')
        # A model-list query starts a backend too; its diagnostic helper uses a Job and timeout.
        from .diagnostics import collect_models
        app.state.diagnostic_busy=True
        try:
            value=await asyncio.to_thread(collect_models,config)
            if value['ok']:
                app.state.models=value['models']
                app.state.acceptance=await asyncio.to_thread(gate.status,config)
        finally:app.state.diagnostic_busy=False
        return value

    @app.get('/api/conversations')
    async def conversations(q:str=''):return app.state.store.conversations(q)

    @app.get('/api/output-settings')
    async def output_settings():return app.state.store.output_settings()

    @app.put('/api/output-settings')
    async def save_output_settings(body:OutputSettingsBody):
        if busy():raise Conflict('実行中は保存先を変更できません。')
        execution=Mutex(config.mutex_prefix+'_run')
        try:
            if not execution.acquire():raise Conflict('実行中は保存先を変更できません。')
            return app.state.store.update_output_settings(body.folder,body.revision)
        finally:execution.close()

    def show_in_explorer(path,select=False):
        args=[str(Path(os.environ['SystemRoot'])/'explorer.exe')]
        args+=['/select,',str(path)] if select else [str(path)]
        try:subprocess.Popen(args,shell=False)
        except OSError:raise HTTPException(500,'エクスプローラーを開けません。')
        return {'ok':True}

    @app.post('/api/output-settings/open-folder')
    async def output_settings_folder(request:Request):
        if request.query_params or await request.body():raise ValueError('この操作に引数は指定できません。')
        if busy():raise Conflict('実行中は保存先を開けません。')
        folder=app.state.store.output_settings()['folder']
        if not folder:raise Conflict('テキストの保存先を登録してください。')
        return show_in_explorer(registered_folder(folder))

    @app.post('/api/conversations/{id}/instructions/open-folder')
    async def instructions_folder(id:str,request:Request):
        if request.query_params or await request.body():raise ValueError('この操作に引数は指定できません。')
        if busy():raise Conflict('実行中は指示の保存先を開けません。')
        conversation=app.state.store.conversation(id)
        if not conversation['instruction_file_ready']:raise Conflict('会話の指示を入力し、「指示を保存」でAGENTS.mdに反映してください。')
        return show_in_explorer(app.state.store.instruction_path(id),True)

    @app.post('/api/quota/refresh')
    async def quota(request:Request):
        if await request.body():raise ValueError('この操作に引数は指定できません。')
        if busy():raise Conflict('実行中はクォータを確認できません。')
        from .quota import collect_quota,verified_configuration
        if not await asyncio.to_thread(verified_configuration,config):
            raise Conflict('CLIまたは関連設定を確認できないため、クォータ取得を停止しています。')
        # No await between the final busy check and reserving the diagnostic slot.
        if busy():raise Conflict('別の処理が実行中です。')
        app.state.diagnostic_busy=True
        try:return await asyncio.to_thread(collect_quota,config)
        finally:app.state.diagnostic_busy=False

    @app.post('/api/history/open-folder')
    async def history_folder(request:Request):
        if await request.body():raise ValueError('この操作にパスは指定できません。')
        path=(config.data/'history.sqlite3').resolve()
        if not path.is_file():raise HTTPException(404,'会話の保存先が見つかりません。')
        try:
            explorer=Path(os.environ['SystemRoot'])/'explorer.exe'
            subprocess.Popen([str(explorer),'/select,',str(path)],shell=False)
        except Exception:raise HTTPException(500,'エクスプローラーを開けません。')
        return {'ok':True,'message':'全会話をまとめた history.sqlite3 の保存先を開きました。'}

    @app.post('/api/conversations')
    async def create(body:ConversationBody):
        if body.model not in app.state.models:raise ValueError('取得できたモデルを選択してください。')
        if body.effort not in {'low','medium','high'}:raise ValueError('effortが無効です。')
        return app.state.store.create(body.model,body.effort,body.system_prompt,body.output_mode)

    @app.patch('/api/conversations/{id}')
    async def update_conversation(id:str,body:ConversationUpdate):
        return app.state.store.update(id,body.model_dump(exclude_unset=True))

    @app.get('/api/conversations/{id}')
    async def detail(id:str):return app.state.store.detail(id)

    @app.delete('/api/conversations/{id}')
    async def delete_conversation(id:str):
        app.state.store.conversation(id)
        if busy():raise Conflict('実行中は会話を削除できません。')
        execution=Mutex(config.mutex_prefix+'_run')
        try:
            if not execution.acquire():raise Conflict('実行中は会話を削除できません。')
            return app.state.store.delete_conversation(id)
        except DeletionError as exc:raise HTTPException(500,str(exc))
        finally:execution.close()

    @app.post('/api/conversations/{id}/attachments')
    async def attachment(id:str,file:UploadFile=File(...)):
        if busy():raise Conflict('実行中は添付を追加できません。')
        raw=await file.read(1024*1024+1)
        return app.state.store.attach(id,file.filename or '',raw)

    @app.post('/api/conversations/{id}/runs')
    async def run(id:str,body:RunBody):
        data=body.model_dump()
        # Idempotent replay does not need a fresh lease or launch.
        previous=app.state.store.db.execute('SELECT id FROM runs WHERE request_id=?',(body.request_id,)).fetchone()
        if previous:
            existing,_=app.state.store.reserve(id,data)
            return {'run':existing,'owner':None,'replayed':True}
        if busy():raise Conflict('別の処理が実行中です。')
        if app.state.controller.save_failed:raise Conflict('保存失敗のため新規実行を停止しています。再起動して履歴を確認してください。')
        current=await asyncio.to_thread(gate.status,config)
        app.state.acceptance=current
        if not current['ready']:raise Conflict(current['reason'])
        conversation=app.state.store.conversation(id)
        if conversation['output_mode']=='text_file' and not text_output_ready():
            raise Conflict('テキストファイル出力は未有効化です。初回設定の同意と保存先を確認してください。')
        allowed=current.get('allowed_pairs',current.get('tested_pairs',[]))
        if not config.mock and {'model':conversation['model'],'effort':conversation['effort']} not in allowed:
            raise Conflict('このモデル・考える量は運用対象に含まれていません。新しい会話で選び直してください。')
        prompt=app.state.store.prompt(id,body.text,body.attachment_ids)
        reserved,_=app.state.store.reserve(id,data)
        try:owner=app.state.controller.launch(reserved,prompt,conversation)
        except Exception:
            app.state.store.event(reserved['id'],{'kind':'terminal','state':'failed','reason':'launch_failed','submitted':False,'active':0})
            raise HTTPException(500,'CLIを起動できませんでした。入力は保存されています。')
        return {'run':reserved,'owner':owner,'replayed':False}

    @app.get('/api/runs/{id}/output')
    async def output_file(id:str):
        raw,metadata=app.state.store.output_bytes(id)
        from urllib.parse import quote
        return Response(raw,media_type='text/plain; charset=utf-8',headers={'Content-Disposition':"attachment; filename=output.txt; filename*=UTF-8''"+quote(metadata['name'],safe='')})

    @app.post('/api/runs/{id}/output/open-folder')
    async def output_folder(id:str,request:Request):
        if await request.body():raise ValueError('この操作にパスは指定できません。')
        app.state.store.output_bytes(id)
        path=app.state.store.output_path(app.state.store.run(id)).resolve()
        try:subprocess.Popen([str(Path(os.environ['SystemRoot'])/'explorer.exe'),'/select,',str(path)],shell=False)
        except OSError:raise HTTPException(500,'エクスプローラーを開けません。')
        return {'ok':True}

    @app.get('/api/runs/{id}/events')
    async def events(id:str,request:Request,after:int=0):
        app.state.store.run(id)
        async def stream():
            seq=max(after,0)
            last_keepalive=0
            while not await request.is_disconnected():
                rows=app.state.store.events(id,seq)
                for row in rows:
                    seq=row['seq']
                    yield (json.dumps(row,ensure_ascii=False)+'\n').encode()
                    if row['kind']=='terminal':return
                if not rows:
                    try:run=app.state.store.run(id)
                    except KeyError:return  # Deleted in another browser while replaying terminal events.
                    if run['state'] in TERMINAL:return
                now=asyncio.get_running_loop().time()
                if now-last_keepalive>=min(1,config.browser_timeout/3):
                    # Only the current owner of this run can renew its browser lease.
                    # Emit traffic even while the CLI is thinking; this does not rely
                    # on background-tab JavaScript timers and carries no persisted seq.
                    if owning(request,id):app.state.controller.command('browser_ping')
                    yield b'{"kind":"keepalive"}\n'
                    last_keepalive=now
                await asyncio.sleep(.1)
        return StreamingResponse(stream(),media_type='application/x-ndjson')

    def owning(request,id):
        controller=app.state.controller
        if controller.id!=id:return False
        secret=request.headers.get('x-run-owner','')
        return bool(controller.owner) and hmac.compare_digest(secret,controller.owner)

    @app.post('/api/runs/{id}/heartbeat')
    async def heartbeat(id:str,request:Request):
        if not owning(request,id):raise HTTPException(403,'操作権がありません。')
        app.state.controller.command('browser_ping')
        return {'ok':True}

    @app.post('/api/runs/{id}/claim')
    async def claim(id:str):
        if app.state.controller.id!=id:raise Conflict('対象は実行中ではありません。')
        app.state.controller.owner=secrets.token_urlsafe(32)
        return {'owner':app.state.controller.owner}

    @app.post('/api/runs/{id}/stop')
    async def stop(id:str,request:Request):
        run=app.state.store.run(id)
        if run['state'] in TERMINAL:return {'ok':True}
        if not owning(request,id):raise HTTPException(403,'停止には操作権が必要です。')
        app.state.controller.command('stop',reason='manual')
        return {'ok':True}

    @app.post('/api/shutdown')
    async def shutdown():
        app.state.controller.command('stop',reason='app_shutdown')
        app.state.shutdown_requested=True
        return {'ok':True}
    return app
