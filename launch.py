import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import webbrowser
import uvicorn
from agweb.config import APP_ROOT, Config
from agweb.server import create_app
from agweb.windows import Mutex

async def serve(config,sock,no_browser):
    app=create_app(config)
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    runtime=config.data/'runtime.json'
    async def ready():
        while not server.started:
            await asyncio.sleep(.05)
        temporary=runtime.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pid':os.getpid(),'origin':config.origin,'token':config.token,'mode':'mock' if config.mock else 'real','startup_id':config.startup_id}),encoding='utf-8')
        temporary.replace(runtime)
        print('WebUIを起動しました。模擬CLIモード。' if config.mock else 'WebUIを起動しました。初回設定が必要な場合は画面で確認してください。',flush=True)
        if not no_browser:webbrowser.open(config.origin+'/#'+config.token)
        while not app.state.shutdown_requested:
            await asyncio.sleep(.2)
        server.should_exit=True
    ready_task=asyncio.create_task(ready())
    try:await server.serve(sockets=[sock])
    finally:
        ready_task.cancel()
        if runtime.exists():
            try:
                if json.loads(runtime.read_text(encoding='utf-8'))['pid']==os.getpid():runtime.unlink()
            except Exception:pass

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='停止制御付きAntigravityローカルWebUI')
    parser.add_argument('--mock',action='store_true',help='開発・受入用の模擬CLI。実モデルは呼びません。')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--mock-scenario',default='normal',choices=['normal','slow','trees','hang'],help='模擬検証専用の動作')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--startup-id',default=None)
    parser.add_argument('--root',type=Path,default=APP_ROOT,help='テスト用データルート')
    args=parser.parse_args()
    config=Config(args.root.resolve(),secrets.token_urlsafe(32),args.port,args.mock)
    config.startup_id=args.startup_id
    if args.mock:config.mock_scenario=args.mock_scenario
    mutex=Mutex(config.mutex_prefix+'_server')
    if not mutex.acquire():
        print('このWebUIはすでに起動しています。',flush=True);mutex.close();return 2
    sock=socket.socket()
    try:
        sock.bind(('127.0.0.1',args.port));sock.listen(128)
        config.port=sock.getsockname()[1]
        config.data.mkdir(parents=True,exist_ok=True)
        asyncio.run(serve(config,sock,args.no_browser))
    finally:
        sock.close();mutex.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
