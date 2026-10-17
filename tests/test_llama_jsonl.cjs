const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname,'..');
const corePath = path.join(root,'static/llama-jsonl.js');

function records(){
  return [
    {type:'session',harness:'llama.app',id:'conv',name:'日本語の題名 📄',currNode:'a2'},
    {type:'message',message:{id:'root',convId:'conv',parent:null,role:'system',type:'root',content:'SYSTEM_SECRET'}},
    {type:'message',message:{id:'u1',convId:'conv',parent:'root',role:'user',type:'text',content:'最初の質問'}},
    {type:'message',message:{id:'a1',convId:'conv',parent:'u1',role:'assistant',type:'text',content:'# 最初の回答\n\n本文',model:'model.gguf',reasoningContent:'THINK_SECRET',timings:{prompt_n:987654}}},
    {type:'message',message:{id:'branch',convId:'conv',parent:'u1',role:'assistant',type:'text',content:'UNSELECTED_BRANCH'}},
    {type:'message',message:{id:'u2',convId:'conv',parent:'a1',role:'user',type:'text',content:'次の質問'}},
    {type:'message',message:{id:'a2',convId:'conv',parent:'u2',role:'assistant',type:'text',content:'次の回答 📄',model:'model.gguf'}},
  ];
}
function jsonl(value=records()){return value.map(row=>JSON.stringify(row)).join('\n');}
function core(){const ctx=vm.createContext({});vm.runInContext(fs.readFileSync(corePath,'utf8'),ctx);return ctx.LlamaJsonl;}

test('llama.app active branch follows parents, independent of record order, BOM and blank lines',()=>{
  const value=records(),c=core();
  const parsed=c.parse('\ufeff\n'+jsonl([value[0],...value.slice(1).reverse()])+'\r\n\n');
  assert.equal(parsed.title,'日本語の題名 📄');
  assert.deepEqual(Array.from(parsed.messages,m=>m.id),['u1','a1','u2','a2']);
  const answer=c.markdown(parsed,'answers');
  assert.ok(answer.indexOf('最初の回答')<answer.indexOf('次の回答'));
  for(const excluded of ['最初の質問','次の質問','SYSTEM_SECRET','THINK_SECRET','987654','UNSELECTED_BRANCH','model.gguf'])assert.ok(!answer.includes(excluded),excluded);
  const conversation=c.markdown(parsed,'conversation');
  const ordered=['日本語の題名','model\\.gguf','## 質問 1','最初の質問','## 回答 1','# 最初の回答','## 質問 2','次の質問','## 回答 2','次の回答'];
  let at=0;for(const part of ordered){const next=conversation.indexOf(part,at);assert.ok(next>=at,part);at=next+part.length;}
  for(const excluded of ['SYSTEM_SECRET','THINK_SECRET','987654','UNSELECTED_BRANCH'])assert.ok(!conversation.includes(excluded),excluded);
});

test('single answer preserves original Markdown; empty answer cannot be saved alone',()=>{
  const value=records().slice(0,4);value[0].currNode='a1';const c=core();
  assert.equal(c.markdown(c.parse(jsonl(value)),'answers'),value[3].message.content);
  value[3].message.content=' \n';const parsed=c.parse(jsonl(value));
  assert.throws(()=>c.markdown(parsed,'answers'),/回答本文/);
  assert.match(c.markdown(parsed,'conversation'),/回答本文なし/);
  assert.throws(()=>c.markdown(parsed,'bad'),/出力形式/);
});

test('malformed input reports physical line and rejects unsupported records or sessions',()=>{
  const c=core();
  assert.throws(()=>c.parse(''),/空/);
  assert.throws(()=>c.parse(jsonl()+'\n\n{broken'),/9行目/);
  for(const bad of [null,[],{}, {type:'unknown'}])assert.throws(()=>c.parse(JSON.stringify(bad)),/1行目/);
  assert.throws(()=>c.parse(jsonl(records().slice(1))),/session/);
  assert.throws(()=>c.parse(jsonl([...records(),records()[0]])),/session/);
  const value=records();value[0].harness='other';assert.throws(()=>c.parse(jsonl(value)),/llama.app/);
});

test('invalid graph never silently exports a partial or different branch',()=>{
  const c=core();
  for(const [change,pattern] of [
    [v=>v[0].currNode='missing',/currNode/],
    [v=>v[6].message.parent='missing',/親/],
    [v=>v[2].message.parent='a2',/循環/],
    [v=>v[6].message.convId='other',/convId/],
    [v=>v.push(v[3]),/重複/],
    [v=>v[6].message.content=[],/本文/],
    [v=>v[6].message.role='tool',/role/],
    [v=>v[6].message.type='image',/type/],
    [v=>v[2].message.parent=null,/root/],
  ]){const value=records();change(value);assert.throws(()=>c.parse(jsonl(value)),pattern);}
});

test('metadata is literal Markdown and a missing session name uses a readable default',()=>{
  const c=core(),value=records();value[0].name='[リンク](https://example.test)\n# 見出し';
  value[3].message.model='<img src=x>';
  const md=c.markdown(c.parse(jsonl(value)),'conversation');
  assert.ok(md.includes('\\[リンク\\]\\(https://example\\.test\\)'));
  assert.ok(md.includes('\\<img src=x\\>'));
  delete value[0].name;assert.equal(c.parse(jsonl(value)).title,'llama.cppの会話');
});

function page(){
  const downloads=[],revoked=[],timers=[],blobs=new Map();let failClick=false;
  const ids=[...fs.readFileSync(path.join(root,'Convert-Llama-JSONL.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
  const elements=new Map(ids.map(id=>[id,{value:'',textContent:'',disabled:false,hidden:false,files:[],srcdoc:''}]));
  elements.get('mode').value='answers';
  const context=vm.createContext({Blob,TextDecoder,
    document:{getElementById:id=>elements.get(id),body:{append(){}},createElement:()=>({remove(){},click(){if(failClick)throw new Error('blocked');downloads.push({name:this.download,blob:blobs.get(this.href)});}})},
    URL:{createObjectURL(blob){const url='blob:'+blobs.size;blobs.set(url,blob);return url;},revokeObjectURL:url=>revoked.push(url)},
    setTimeout:fn=>timers.push(fn),
    marked:{parse:text=>text},DOMPurify:{sanitize:html=>html},
  });
  for(const name of ['reading-export.js','llama-jsonl.js','llama-jsonl-ui.js'])vm.runInContext(fs.readFileSync(path.join(root,'static',name),'utf8'),context);
  async function choose(file){elements.get('jsonl').files=file?[file]:[];await elements.get('jsonl').onchange();}
  function file(text,name='会話.jsonl'){const blob=new Blob([text]);return {name,size:blob.size,arrayBuffer:()=>blob.arrayBuffer()};}
  return {elements,downloads,revoked,timers,context,choose,file,failDownload(){failClick=true;}};
}

test('file selection previews and saves the same UTF-8 HTML with shared rendering',async()=>{
  const p=page();assert.equal(p.elements.get('save').disabled,true);
  await p.choose(p.file(jsonl()));
  assert.equal(p.elements.get('save').disabled,false);
  assert.match(p.elements.get('summary').textContent,/質問 2.*回答 2/);
  p.elements.get('save').onclick();
  assert.equal(p.downloads[0].name,'llama-answers-会話.html');
  assert.equal(p.downloads[0].blob.type,'text/html;charset=utf-8');
  const html=await p.downloads[0].blob.text();
  assert.equal(html,p.elements.get('preview').srcdoc);
  assert.match(html,/<meta name="viewport"/);assert.match(html,/@media print/);
  assert.ok(!html.includes('最初の質問'));assert.ok(!html.includes('THINK_SECRET'));
  assert.notDeepEqual([...Buffer.from(await p.downloads[0].blob.arrayBuffer()).subarray(0,3)],[0xef,0xbb,0xbf]);
  p.elements.get('mode').value='conversation';p.elements.get('mode').onchange();p.elements.get('save').onclick();
  assert.match(await p.downloads[1].blob.text(),/最初の質問/);
  assert.equal(p.downloads[1].name,'llama-conversation-会話.html');
  p.timers.forEach(fn=>fn());assert.equal(p.revoked.length,2);
});

test('invalid replacement, invalid UTF-8 and oversized input clear old output',async()=>{
  const p=page();
  for(const bad of [p.file('{bad'),{name:'bad.jsonl',size:2,arrayBuffer:async()=>new Uint8Array([0xc3,0x28]).buffer},{name:'large.jsonl',size:16*1024*1024+1,arrayBuffer:()=>{throw new Error('should not read');}}]){
    await p.choose(p.file(jsonl()));await p.choose(bad);
    assert.equal(p.elements.get('save').disabled,true);assert.equal(p.elements.get('preview').srcdoc,'');
    assert.equal(p.elements.get('error').hidden,false);p.elements.get('save').onclick();assert.equal(p.downloads.length,0);
  }
  await p.choose(null);assert.equal(p.elements.get('error').hidden,true);
});

test('late file reads cannot replace a newer selection, including read failures',async()=>{
  const p=page();let resolve,reject;
  const first=p.choose({name:'old.jsonl',size:1,arrayBuffer:()=>new Promise(r=>resolve=r)});
  assert.equal(p.elements.get('save').disabled,true);
  await p.choose(p.file(jsonl(),'new.jsonl'));resolve(await new Blob(['{bad']).arrayBuffer());await first;
  assert.equal(p.elements.get('error').hidden,true);p.elements.get('save').onclick();assert.equal(p.downloads[0].name,'llama-answers-new.html');
  const failing=p.choose({name:'old.jsonl',size:1,arrayBuffer:()=>new Promise((_,r)=>reject=r)});
  await p.choose(p.file(jsonl(),'newer.jsonl'));reject(new Error('old failure'));await failing;
  assert.equal(p.elements.get('error').hidden,true);assert.equal(p.elements.get('save').disabled,false);
});

test('download errors are visible and release temporary URLs',async()=>{
  const p=page();await p.choose(p.file(jsonl()));p.failDownload();p.elements.get('save').onclick();
  assert.equal(p.elements.get('error').hidden,false);assert.match(p.elements.get('error').textContent,/保存/);
  assert.equal(p.elements.get('save').disabled,false);p.timers.forEach(fn=>fn());assert.equal(p.revoked.length,1);
});
