import json, os, re, subprocess, sys, time, uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agweb.hook import main as unused
from multiprocessing.connection import Client

def output(event):
    print(json.dumps(event,ensure_ascii=False),flush=True)

def hook(kind,payload):
    # Exercise the generated hook config and Windows command launcher, instead
    # of bypassing both by starting the Python guard directly.
    config=json.loads((Path.cwd()/'.agents'/'hooks.json').read_text(encoding='utf-8'))['agweb-guard']
    if kind=='pre_tool':command=config['PreToolUse'][0]['hooks'][0]['command']
    else:
        command=config['PreInvocation'][0]['command']
        if kind!='pre_invocation':command=command.rsplit(' ',1)[0]+' '+kind
    # The actual CLI hook did not resolve a basename from the run workspace.
    # Execute from another directory so tests require an absolute entry point.
    process=subprocess.Popen(command,shell=True,cwd=Path(__file__).resolve().parents[1],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    result,_=process.communicate(json.dumps(payload).encode(),timeout=5)
    return json.loads(result)

if '--descendant' in sys.argv:
    if '--grandchild' not in sys.argv:
        subprocess.Popen([sys.executable,__file__,'--descendant','--grandchild'])
    time.sleep(300)
    sys.exit()

scenario = os.getenv('AGWEB_MOCK_SCENARIO','normal')
args=sys.argv
cid=args[args.index('--conversation')+1] if '--conversation' in args else str(uuid.uuid4())
model=args[args.index('--model')+1] if '--model' in args else 'mock-model'
if scenario=='stdin_hang':time.sleep(300)
message=json.loads(sys.stdin.readline())['message']['content']
if scenario == 'trees':
    child=subprocess.Popen([sys.executable,__file__,'--descendant'])
    output({'event':'init','conversation_id':cid,'init':{}})
    time.sleep(300)
if scenario == 'hang':
    time.sleep(300)
if scenario == 'invalid':
    print('{broken',flush=True); time.sleep(300)
if scenario == 'oversized':
    sys.stdout.write('x'*(1024*1024+1)); sys.stdout.flush(); time.sleep(300)
if scenario == 'stderr':
    sys.stderr.write('diagnostic '*100000); sys.stderr.flush()
output({'event':'init','conversation_id':cid,'init':{'model':model,'permission_mode':'request-review'}})
if scenario != 'missing_guard':
    if scenario=='hook_exception':os.environ['AGWEB_HOOK_FAULT']='exception'
    hook('pre_invocation',{'modelName':model,'conversationId':cid,'invocationNum':0})
if scenario=='total_limit':
    for _ in range(40):output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'agent_response','text_delta':'x'*(512*1024)}})
    time.sleep(300)
denied={'deny':('run_command',{'CommandLine':'echo forbidden'}),'file_denied':('view_file',{'AbsolutePath':'C:/Windows/win.ini'}),
    'delegate_denied':('task',{'prompt':'test'}),'unknown_denied':('unrecognized',{}),'mcp_denied':('mcp_server_search',{'query':'test'}),
    'url_denied':('read_url_content',{'Url':'file:///C:/Windows/win.ini'}),'url_credentials':('read_url_content',{'Url':'https://user:pass@example.com/'}),
    'bad_arguments':('search_web',{'query':'test','domain':['example.com']}),
    'chunk_denied':('view_content_chunk',{'document_id':'web-document-1','position':0})}
if scenario in denied:
    name,arguments=denied[scenario]
    hook('pre_tool',{'stepIdx':1,'conversationId':cid,'toolCall':{'name':name,'args':arguments}})
    time.sleep(300)
if scenario=='web':
    calls=[('search_web',{'query':'test'}),('read_url_content',{'Url':'https://example.com/'})]
    for index,(name,arguments) in enumerate(calls,1):
        hook('pre_tool',{'stepIdx':index,'conversationId':cid,'toolCall':{'name':name,'args':arguments}})
        output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'tool','tool_name':name,'step_index':index,'state':'DONE'}})
if scenario in {'chat_write_hook','chat_write_active'}:
    if scenario=='chat_write_active':
        output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'tool','tool_name':'write_to_file','step_index':1,'state':'ACTIVE'}})
    else:
        hook('pre_tool',{'stepIdx':1,'conversationId':cid,'toolCall':{'name':'write_to_file','args':{'TargetFile':str(Path.cwd()/'Novel.txt'),'Overwrite':False,'CodeContent':'test','Description':'test'}}})
    time.sleep(300)
if scenario=='missing_tool_guard':
    output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'tool','tool_name':'search_web','step_index':1,'state':'DONE'}})
if scenario == 'hook_failure':
    hook('unexpected',{})
    time.sleep(300)
if scenario == 'slow':
    time.sleep(8)
target=os.getenv('AGWEB_TEXT_OUTPUT_TARGET')
if target and scenario!='text_missing':
    named=re.search(r'(?m)^テスト出力名: (.+\.txt)$',message)
    if named:target=str(Path(target).parent/named[1])
    content='FILE-OUTPUT-OK\n日本語の本文 📄\n'
    arguments={'TargetFile':target,'Overwrite':False,'CodeContent':content,'Description':'write test text','IsArtifact':False}
    if scenario=='text_wrong_path':arguments['TargetFile']=str(Path.cwd()/'AGENTS.md')
    if scenario in {'text_no_hook','text_unguarded_done'}:
        output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'tool','tool_name':'write_to_file','step_index':1,'state':'ACTIVE' if scenario=='text_no_hook' else 'DONE'}})
        time.sleep(300)
    if scenario=='text_proposal':
        arguments.update(toolAction='Writing output file',toolSummary='Write output text file')
        output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'tool','tool_name':'write_to_file','step_index':1,'state':'ACTIVE'}})
    reply=hook('pre_tool',{'stepIdx':1,'conversationId':cid,'toolCall':{'name':'write_to_file','args':arguments}})
    # Mirror headless default Ask: allow delegates; auto_approve can authorize
    # only in the absence of an explicit user deny rule.
    if reply.get('decision') not in {'allow','auto_approve'}:time.sleep(300)
    if scenario=='text_allow_only':reply['decision']='allow'
    target=reply.get('overwrite',{}).get('TargetFile',target)
    if reply.get('decision')=='auto_approve' and scenario!='text_explicit_deny':
        with Path(target).open('xb') as file:
            file.write(b'\xff' if scenario=='text_invalid' else (b'x'*(1024*1024+1) if scenario=='text_oversized' else content.encode('utf-8')))
    else:sys.stderr.write('permission check failed: user denied permission for write_file\n')
    output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'tool','tool_name':'write_to_file','step_index':1,'state':'DONE'}})
    if scenario=='text_repeat':
        hook('pre_tool',{'stepIdx':2,'conversationId':cid,'toolCall':{'name':'write_to_file','args':arguments}})
        time.sleep(300)
    if scenario=='text_wait':time.sleep(300)
long_text=('## 日本語の長文\n\n桜の下で物語を書き続ける。改行と句読点を保つ。\n\n'*1000)[:30000]
deltas=[long_text[i:i+128] for i in range(0,len(long_text),128)] if scenario in {'long','long_wait'} else ['こんにちは。','停止制御の確認です。']
for delta in deltas:
    output({'event':'step_update','step_update':{'conversation_id':cid,'step_type':'agent_response','state':'ACTIVE','text_delta':delta}})
    time.sleep(.01 if scenario in {'long','long_wait'} else .04)
if scenario=='long_wait':time.sleep(130)
response='こんにちは。停止制御の確認です。\n\n```text\n日本語テスト\n```\n<script>alert(1)</script>\n![外部画像](https://example.com/private.png)'
if scenario in {'long','long_wait'}:response=long_text
output({'event':'result','result':{'conversation_id':cid,'status':'ERROR' if scenario=='text_partial' else 'SUCCESS','response':'PRIVATE_CHAT_BODY' if target else response,'usage':{'input_tokens':20,'output_tokens':10,'total_tokens':30}}})
if scenario == 'exit_hang':
    time.sleep(300)
