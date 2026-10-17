const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,fixture}=require('./webui_harness.cjs');
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});

test('answer copy keeps raw Markdown and checks current conversation and terminal state',async()=>{
  const p=page();let copied=[];
  p.context.navigator.clipboard.writeText=async text=>copied.push(text);
  const value=fixture(['completed','failed','running'],['# 題名 📄\r\n\n```js\n  x();\n```','残った本文','途中']);
  p.load(value);
  const buttons=p.elements.get('messages').querySelectorAll('button').filter(b=>b.textContent==='回答をコピー');
  assert.deepEqual(buttons.map(b=>b.disabled),[false,false,true]);
  await buttons[0].click();await buttons[1].click();await buttons[2].click();
  assert.deepEqual(copied,value.runs.slice(0,2).map(r=>r.response));
  assert.match(p.elements.get('copy-state').textContent,/コピーしました/);
  p.evaluate("current='different'");await buttons[0].click();assert.equal(copied.length,2);
});

test('clipboard rejection is visible and does not change the answer',async()=>{
  const p=page();p.load(fixture());
  p.context.navigator.clipboard.writeText=async()=>{throw new Error('permission denied');};
  await p.elements.get('messages').querySelectorAll('button').find(b=>b.textContent==='回答をコピー').click();
  assert.match(p.elements.get('copy-state').textContent,/コピーできません/);
  assert.equal(p.evaluate('detail.runs[0].response'),'回答本文');
});

test('search uses one encoded phrase, keeps drafts and discards stale list results',async()=>{
  const p=page(),value=fixture();p.load(value);
  p.elements.get('prompt').value='保持する入力';p.elements.get('system-prompt').value='指示';
  let finish;const calls=[];
  p.context.fetch=async url=>{calls.push(url);if(calls.length===1)return new Promise(resolve=>{finish=resolve;});return response([]);};
  p.elements.get('history-search').value='日本語 %_';const old=p.evaluate('list()');
  p.elements.get('history-search').value='new';await p.evaluate('list()');
  finish(response([value.conversation]));await old;
  assert.equal(calls[0],'/api/conversations?q='+encodeURIComponent('日本語 %_'));
  assert.equal(p.elements.get('conversations').querySelectorAll('button').length,0);
  assert.equal(p.elements.get('prompt').value,'保持する入力');assert.equal(p.evaluate('current'),value.conversation.id);
});

test('instruction drafts survive switching and reload through session storage',async()=>{
  const p=page(),value=fixture([]);p.load(value);
  p.elements.get('prompt').value='質問の下書き';p.elements.get('system-prompt').value='指示の下書き';
  p.elements.get('system-prompt').oninput();
  p.context.fetch=async url=>response(url.includes('/conversation-1')?value:[]);
  await p.evaluate("select('conversation-1')");
  assert.equal(p.elements.get('system-prompt').value,'指示の下書き');
  assert.equal(p.elements.get('prompt').value,'質問の下書き');
  p.elements.get('system-prompt').value='';p.evaluate('loadInstruction()');
  assert.equal(p.elements.get('system-prompt').value,'指示の下書き');
  p.load(fixture());p.evaluate('loadInstruction();renderControls()');
  assert.equal(p.elements.get('system-prompt').value,'');assert.equal(p.elements.get('system-prompt').disabled,true);
});

test('an unsaved new conversation draft survives visiting history and returning',async()=>{
  const p=page(),value=fixture();
  p.elements.get('prompt').value='新規の質問';p.elements.get('system-prompt').value='新規の指示';
  p.elements.get('system-prompt').oninput();
  p.context.fetch=async url=>response(url.includes('/conversation-1')?value:[]);
  await p.evaluate("select('conversation-1')");
  p.elements.get('new').click();
  assert.equal(p.elements.get('prompt').value,'新規の質問');
  assert.equal(p.elements.get('system-prompt').value,'新規の指示');
});

test('instruction save uses the displayed revision and preserves a failed draft',async()=>{
  const p=page(),value=fixture([]);value.conversation.revision=3;p.load(value);
  p.elements.get('system-prompt').value='新しい指示';p.elements.get('system-prompt').oninput();let sent;
  p.context.fetch=async(url,opts)=>{sent=JSON.parse(opts.body);return response({detail:'会話が更新されました'},409);};
  await p.elements.get('instruction-save').click();
  assert.equal(sent.revision,3);assert.equal(sent.system_prompt,'新しい指示');
  assert.equal(p.context.sessionStorage.getItem('instruction-draft-conversation-1'),'新しい指示');
  assert.equal(p.elements.get('system-prompt').value,'新しい指示');assert.match(p.elements.get('error').textContent,/更新/);
});

test('instruction is counted with prompt and attachments without changing export content',async()=>{
  const p=page(),value=fixture([]);p.load(value);
  p.elements.get('system-prompt').value='日'.repeat(8001);p.evaluate('renderControls()');assert.equal(p.elements.get('send').disabled,true);
  p.elements.get('system-prompt').value='指示';p.elements.get('prompt').value='質問';p.evaluate('renderControls()');
  assert.match(p.elements.get('count').textContent,/4 \/ 20,000/);
  value.runs=fixture().runs;value.conversation.system_prompt='INSTRUCTION_PRIVATE';value.conversation.instruction_locked=true;
  p.load(value);p.elements.get('conversation-save-html').click();
  const html=await p.downloads[0].blob.text();assert.ok(!html.includes('INSTRUCTION_PRIVATE'));assert.doesNotMatch(html,/回答をコピー|コードをコピー/);
});
