const {test} = require('node:test');
const assert = require('node:assert/strict');
const {page,fixture} = require('./webui_harness.cjs');

const ready=()=>({acceptance:{ready:true,reason:'mock'},models:['mock-model'],busy:false,active:null});
function fetcher(p,del=async()=>({ok:true})){
  const calls=[];
  p.context.fetch=async(url,options={})=>{
    calls.push({url,method:options.method||'GET'});
    if(options.method==='DELETE')return del(url);
    return {ok:true,json:async()=>url==='/api/status'?ready():[]};
  };
  return calls;
}
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});

test('deletion requires a loaded conversation and no pending processing',()=>{
  const p=page();p.evaluate('renderControls()');
  assert.equal(p.elements.get('conversation-delete').disabled,true);
  p.load(fixture([]));assert.equal(p.elements.get('conversation-delete').disabled,false);
  p.load(fixture(['completed','cancelled','failed','timed_out']));
  assert.equal(p.elements.get('conversation-delete').disabled,false);
  for(const flag of ['submitting','deleting','quotaBusy','fileBusy','status.busy']){
    p.evaluate(flag+'=true;renderControls()');assert.equal(p.elements.get('conversation-delete').disabled,true,flag);
    p.evaluate(flag+'=false;renderControls()');
  }
  p.load(fixture(['running']));assert.equal(p.elements.get('conversation-delete').disabled,true);
  p.load(fixture());p.evaluate("current='loading';renderControls()");
  assert.equal(p.elements.get('conversation-delete').disabled,true);
});

test('cancelling confirmation keeps the conversation and makes no API request',async()=>{
  const p=page();p.load(fixture());const calls=fetcher(p);
  let message;
  p.context.confirm=text=>{message=text;return false;};
  await p.elements.get('conversation-delete').click();
  assert.match(message,/日本語の会話 📄/);assert.match(message,/添付ファイル/);assert.match(message,/元に戻せません/);
  assert.equal(calls.length,0);assert.equal(p.evaluate('current'),'conversation-1');
});

test('deletion locks navigation then clears only target browser state and shows a new conversation',async()=>{
  const p=page();p.load(fixture());
  p.evaluate("sessionStorage.setItem('draft-conversation-1','下書き');sessionStorage.setItem('draft-other','残す');sessionStorage.setItem('last-conversation',current);sessionStorage.setItem('agweb-owner-run-1','secret');streamAbort=new AbortController();");
  const abort=p.evaluate('streamAbort');let finish;
  fetcher(p,()=>new Promise(resolve=>{finish=resolve;}));
  const deleting=p.elements.get('conversation-delete').click();
  assert.equal(p.elements.get('conversation-delete').disabled,true);
  assert.equal(p.elements.get('send').disabled,true);assert.equal(p.elements.get('new').disabled,true);
  await p.evaluate("select('other')");assert.equal(p.evaluate('current'),'conversation-1');
  finish(response({ok:true}));await deleting;
  assert.equal(p.evaluate('current'),null);assert.equal(p.evaluate('detail'),null);
  assert.equal(p.elements.get('title').textContent,'新しい会話');assert.equal(p.answers().length,0);
  assert.equal(p.context.sessionStorage.getItem('draft-conversation-1'),null);
  assert.equal(p.context.sessionStorage.getItem('agweb-owner-run-1'),null);
  assert.equal(p.context.sessionStorage.getItem('last-conversation'),null);
  assert.equal(p.context.sessionStorage.getItem('draft-other'),'残す');assert.equal(abort.signal.aborted,true);
  assert.equal(p.elements.get('conversation-delete').disabled,true);
});

test('delete failure preserves the view and draft while displaying the API error',async()=>{
  const p=page();p.load(fixture());p.elements.get('prompt').value='下書き';
  fetcher(p,async()=>response({detail:'会話を削除できませんでした。'},500));
  await p.elements.get('conversation-delete').click();
  assert.equal(p.evaluate('current'),'conversation-1');assert.equal(p.answers().length,1);
  assert.equal(p.elements.get('prompt').value,'下書き');
  assert.equal(p.elements.get('conversation-delete').disabled,false);
  assert.match(p.elements.get('error').textContent,/削除できません/);
});

test('file cleanup warning remains visible after successful DB deletion',async()=>{
  const p=page();p.load(fixture());
  fetcher(p,async()=>response({ok:true,warning:'ファイルの完全削除が未完了です。'}));
  await p.elements.get('conversation-delete').click();
  assert.equal(p.evaluate('current'),null);
  assert.equal(p.elements.get('error').hidden,false);assert.match(p.elements.get('error').textContent,/未完了/);
});

test('reloading a deleted selection returns safely to the new conversation view',async()=>{
  const p=page();p.load(fixture());
  p.context.fetch=async url=>url==='/api/conversations'?response([]):response({detail:'対象が見つかりません。'},404);
  await p.evaluate("select('conversation-1')");
  assert.equal(p.evaluate('current'),null);assert.equal(p.elements.get('conversation-delete').disabled,true);
  assert.equal(p.context.sessionStorage.getItem('last-conversation'),null);
});

for(const stage of ['events','detail'])test('deletion in another screen during '+stage+' refresh clears the stale view',async()=>{
  const p=page();p.load(fixture());p.evaluate("active={id:'run-1',conversation_id:current};");
  p.context.fetch=async url=>{
    if(url.includes('/events'))return stage==='events'?response({detail:'missing'},404):new Response('');
    return url==='/api/conversations'?response([]):response({detail:'missing'},404);
  };
  await p.evaluate("watch('run-1')");
  assert.equal(p.evaluate('current'),null);assert.equal(p.answers().length,0);
});

test('an older list request cannot put a deleted conversation back in the sidebar',async()=>{
  const p=page(),value=fixture();p.load(value);let finish,reads=0;
  p.context.fetch=async(url,options={})=>{
    if(options.method==='DELETE')return response({ok:true});
    if(url==='/api/status')return response(ready());
    if(++reads===1)return new Promise(resolve=>{finish=resolve;});
    return response([]);
  };
  const stale=p.evaluate('list()');
  await p.elements.get('conversation-delete').click();
  finish(response([value.conversation]));await stale;
  assert.equal(p.elements.get('conversations').querySelectorAll('button').length,0);
  assert.equal(p.elements.get('conversations').children[0].textContent,'会話はまだありません');
});
