const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,fixture}=require('./webui_harness.cjs');

function value(state='completed',outputState='completed'){
  const d=fixture([state],['PRIVATE_CHAT_BODY']);d.conversation.output_mode='text_file';
  d.runs[0].output={name:'run-1.txt',state:outputState,size:123,sha256:'hash'};return d;
}

test('text output renders metadata only and downloads authenticated original bytes',async()=>{
  const p=page();p.load(value());let url,headers;
  const raw='本文 📄\r\n改行\n';
  p.context.fetch=async(u,o)=>{url=u;headers=o.headers;return {ok:true,blob:async()=>new Blob([raw])};};
  const buttons=p.elements.get('messages').querySelectorAll('button');
  assert.deepEqual(buttons.map(b=>b.textContent),['.txtを保存','保存先を開く']);
  assert.ok(!JSON.stringify(p.elements.get('messages').children,(k,v)=>k==='parent'?undefined:v).includes('PRIVATE_CHAT_BODY'));
  await buttons[0].click();assert.equal(url,'/api/runs/run-1/output');assert.ok('Authorization' in headers);
  assert.equal(await p.downloads[0].blob.text(),raw);
  p.elements.get('conversation-save-html').click();
  const html=await p.downloads[1].blob.text();assert.ok(html.includes('run-1.txt'));assert.ok(html.includes('保存完了'));assert.ok(!html.includes('PRIVATE_CHAT_BODY'));
});

test('unverified text mode blocks only file mode and mode locks after first send',()=>{
  const p=page();p.load(fixture([]));p.elements.get('prompt').value='小説';p.evaluate("outputSettings={folder:'C:/Novels',revision:1}");
  p.elements.get('output-mode').value='text_file';p.evaluate('renderControls()');
  assert.equal(p.elements.get('send').disabled,true);assert.match(p.elements.get('output-hint').textContent,/未有効化/);
  p.evaluate('status.text_output_ready=true;renderControls()');assert.equal(p.elements.get('send').disabled,false);
  p.load(value());p.evaluate('loadInstruction();renderControls()');assert.equal(p.elements.get('output-mode').disabled,true);
  p.load(fixture([]));p.elements.get('output-mode').value='chat';p.evaluate('renderControls()');assert.equal(p.elements.get('send').disabled,false);
});

test('folder registration preserves revisions and displays errors without changing the saved folder',async()=>{
  const p=page();p.load(fixture([]));let request;
  p.elements.get('output-folder').value='C:/Novels';p.elements.get('output-folder').oninput();
  p.context.fetch=async(url,opts)=>{request={url,body:JSON.parse(opts.body)};return {ok:true,json:async()=>({folder:'C:/Novels',revision:1})};};
  await p.elements.get('output-folder-save').click();
  assert.equal(request.url,'/api/output-settings');assert.deepEqual(request.body,{folder:'C:/Novels',revision:0});
  assert.match(p.elements.get('output-folder-state').textContent,/登録しました/);
  p.context.fetch=async()=>({ok:false,json:async()=>({detail:'保存先が更新されました'})});
  p.elements.get('output-folder').value='D:/Changed';p.elements.get('output-folder').oninput();await p.elements.get('output-folder-save').click();
  assert.match(p.elements.get('output-folder-state').textContent,/更新/);assert.equal(p.evaluate('outputSettings.folder'),'C:/Novels');
});

test('file mode requires a registered folder and shows the actual output path',()=>{
  const p=page();p.load(fixture([]));p.elements.get('prompt').value='x';p.elements.get('output-mode').value='text_file';
  p.evaluate('status.text_output_ready=true;renderControls()');assert.equal(p.elements.get('send').disabled,true);
  assert.match(p.elements.get('output-hint').textContent,/登録/);
  const d=value();d.runs[0].output.path='C:/Novels/日本語 (2).txt';p.load(d);
  assert.ok(p.elements.get('messages').querySelectorAll('p').some(e=>e.textContent==='C:/Novels/日本語 (2).txt'));
  p.evaluate('status.busy=true;renderControls()');assert.equal(p.elements.get('output-folder-save').disabled,true);
});

test('partial output is downloadable; missing, invalid and pending outputs are not',()=>{
  const p=page();
  for(const [runState,fileState,disabled] of [['cancelled','incomplete',false],['failed','missing',true],['failed','invalid',true],['running','pending',true]]){
    p.load(value(runState,fileState));assert.equal(p.elements.get('messages').querySelectorAll('button')[0].disabled,disabled);
  }
});

test('stale output controls and failed downloads never save a different conversation file',async()=>{
  const p=page();p.load(value());const button=p.elements.get('messages').querySelectorAll('button')[0];
  p.context.fetch=async()=>({ok:false,json:async()=>({detail:'保存時から変更されています'})});
  await button.click();assert.match(p.elements.get('error').textContent,/変更/);assert.equal(p.downloads.length,0);
  p.evaluate("current='other'");await button.click();assert.equal(p.downloads.length,0);
});
