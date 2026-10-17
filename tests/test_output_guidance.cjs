const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,fixture}=require('./webui_harness.cjs');
const message='出力先が＜通常回答＞になっています。テキストファイルを出力したい場合は＜テキストファイル＞を選んでください。';

test('normal mode warns and stops before any request, preserving the draft',async()=>{
  const p=page();p.load(fixture([]));p.evaluate('current=null;detail=null');let calls=0;
  p.context.fetch=async()=>{calls++;throw Error('must not send');};
  for(const text of ['小説本文をNovel.txtに書き出してください。','テキストファイルに保存して','novel.TXTとして出力してください']){
    p.elements.get('prompt').value=text;p.elements.get('prompt').oninput();
    assert.equal(p.elements.get('output-mode-warning').textContent,message);
    await p.elements.get('send').click();assert.equal(calls,0);
    assert.equal(p.elements.get('output-mode-warning').hidden,false);
    assert.equal(p.elements.get('prompt').value,text);assert.equal(p.context.sessionStorage.getItem('draft-new'),text);
  }
});

test('explanations are not blocked, file mode hides warning, and locked mode explains a new conversation',()=>{
  const p=page();p.load(fixture([]));
  for(const text of ['Novel.txtの保存方法を教えて','テキストファイルの出力手順は？','普通の相談']){
    p.elements.get('prompt').value=text;p.evaluate('renderControls()');assert.equal(p.elements.get('output-mode-warning').hidden,true);
  }
  p.load(fixture());p.elements.get('prompt').value='Novel.txtに保存してください';p.evaluate('renderControls()');
  assert.match(p.elements.get('output-mode-warning').textContent,/＋ 新しい会話/);
  p.elements.get('output-mode').value='text_file';p.evaluate('renderControls()');assert.equal(p.elements.get('output-mode-warning').hidden,true);
  const d=fixture(['failed']);d.runs[0].reason='output_mode_mismatch';p.load(d);
  assert.ok(p.elements.get('messages').querySelectorAll('div').some(e=>e.textContent?.includes(message)));
});

test('open folder controls use saved IDs and block missing, dirty or busy state',async()=>{
  const p=page();const d=fixture([]);d.conversation.system_prompt='指示';d.conversation.instruction_file_ready=true;
  p.load(d);p.elements.get('system-prompt').value='指示';p.evaluate("outputSettings={folder:'C:/Novels',revision:1};renderControls()");
  const requests=[];p.context.fetch=async(url,opts)=>{requests.push({url,opts});return {ok:true,json:async()=>({ok:true})};};
  await p.elements.get('output-folder-open').click();await p.elements.get('instruction-folder-open').click();
  assert.deepEqual(requests.map(r=>r.url),['/api/output-settings/open-folder','/api/conversations/conversation-1/instructions/open-folder']);
  assert.ok(requests.every(r=>r.opts.body===undefined));
  p.evaluate('outputSettingsDirty=true;renderControls()');assert.equal(p.elements.get('output-folder-open').disabled,true);
  p.elements.get('system-prompt').value='未保存';p.evaluate('renderControls()');assert.equal(p.elements.get('instruction-folder-open').disabled,true);
  p.evaluate('status.busy=true;renderControls()');assert.equal(p.elements.get('output-folder-open').disabled,true);
});

test('explicit unchanged instruction save retries file reflection and reports persisted DB on failure',async()=>{
  const p=page(),d=fixture([]);d.conversation.system_prompt='指示';p.load(d);p.elements.get('system-prompt').value='指示';
  let updates=0;
  p.context.fetch=async(url,opts)=>{
    if(opts?.method==='PATCH'){
      updates++;return {ok:true,json:async()=>({...d.conversation,revision:1,instruction_file_ready:false,instruction_file_error:'会話DBには保存済みですが、AGENTS.mdへの反映に失敗しました。'})};
    }
    return {ok:true,json:async()=>[]};
  };
  await p.elements.get('instruction-save').click();assert.equal(updates,1);
  assert.equal(p.evaluate('detail.conversation.revision'),1);
  assert.match(p.elements.get('instruction-state').textContent,/DBには保存済み/);
  assert.equal(p.elements.get('system-prompt').value,'指示');
});
