// No browser or model process is started here. Real download/rendering is checked separately.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {page,fixture} = require('./webui_harness.cjs');

test('answer download preserves raw Markdown, Unicode, whitespace and CRLF without a BOM',async()=>{
  const p=page();
  const text='  # 見出し 📄\r\n\r\n| 名前 | 値 |\r\n| --- | --- |\r\n| 日本語 | 😀 |\r\n\r\n[リンク](https://example.com)\r\n\r\n```js\r\nconst s = "日本語";\r\n```\r\n<script>raw source</script>\r\n';
  p.load(fixture(['completed'],[text]));
  assert.equal(p.answers().length,1);
  assert.equal(p.answers()[0].textContent,'回答を.mdで保存');
  p.answers()[0].click();
  const saved=p.downloads[0];
  assert.equal(saved.name,'antigravity-answer-run-1.md');
  assert.equal(saved.blob.type,'text/markdown;charset=utf-8');
  assert.deepEqual(Buffer.from(await saved.blob.arrayBuffer()),Buffer.from(text,'utf8'));
  assert.equal(p.revoked.length,0);
  p.timers.forEach(fn=>fn());
  assert.equal(p.revoked.length,1);
});

test('conversation contains ordered questions, answers, attachment names and terminal states',async()=>{
  const p=page(),value=fixture(['completed','cancelled','failed','timed_out'],['最初の回答','中断した本文','失敗した本文','  \n']);
  value.attachments=[{id:'a1',name:'資料.md',text:'ATTACHMENT_SECRET',saved:'private/path'},{id:'a2',name:'未使用.txt'}];
  value.runs[0].body.attachment_ids=['a1'];
  value.runs[0].usage={total_tokens:987654};
  value.runs[1].reason='manual';
  p.load(value);
  p.elements.get('conversation-save').click();
  const saved=p.downloads[0],text=await saved.blob.text();
  assert.equal(saved.name,'antigravity-conversation-conversation-1.md');
  assert.match(text,/^# 日本語の会話 📄\n/);
  assert.ok(text.includes('モデル: mock-model'));
  assert.ok(text.includes('考える量: low'));
  const ordered=['## 質問 1','質問1','資料.md','## 回答 1（完了）','最初の回答','## 質問 2','質問2','## 回答 2（中断）','中断した本文','## 質問 3','質問3','## 回答 3（失敗）','失敗した本文','## 質問 4','質問4','## 回答 4（時間切れ）','回答本文なし'];
  let offset=0;
  for(const part of ordered){const at=text.indexOf(part,offset);assert.ok(at>=offset,part);offset=at+part.length;}
  for(const excluded of ['ATTACHMENT_SECRET','private/path','未使用.txt','987654','usage'])assert.ok(!text.includes(excluded),excluded);
  assert.equal(text.charCodeAt(0),35);
  assert.equal(p.answers()[3].disabled,true);
});

test('only terminal, nonempty answers can be saved; earlier answers remain available during generation',()=>{
  const p=page();
  p.load(fixture(['completed','cancelled','failed','timed_out','starting','running','stopping','completed'],['保存','中断','失敗','時間切れ','途中','途中','途中',' \n\t']));
  assert.deepEqual(p.answers().map(b=>b.disabled),[false,false,false,false,true,true,true,true]);
  for(const b of p.answers())b.click();
  assert.equal(p.downloads.length,4);
  assert.equal(p.elements.get('conversation-save').disabled,true);
});

test('conversation saving is disabled without a selected conversation or submitted history',()=>{
  const p=page();
  p.evaluate('renderMessages();renderControls();');
  assert.equal(p.elements.get('conversation-save').disabled,true);
  p.load(fixture([]));
  assert.equal(p.elements.get('conversation-save').disabled,true);
  p.load(fixture());
  p.elements.get('new').onclick();
  assert.equal(p.elements.get('conversation-save').disabled,true);
  assert.equal(p.answers().length,0);
});

test('download failure is shown and the temporary link/URL are cleaned up',()=>{
  const p=page();p.load(fixture());p.failDownload();
  p.answers()[0].click();
  assert.equal(p.downloads.length,0);
  assert.equal(p.elements.get('error').hidden,false);
  assert.match(p.elements.get('error').textContent,/Markdown.*ダウンロード/);
  assert.equal(p.context.document.body.children.length,0);
  p.timers.forEach(fn=>fn());
  assert.equal(p.revoked.length,1);
});

test('conversation switching invalidates old save buttons and ignores an older selection response',async()=>{
  const p=page(),pending=new Map();p.load(fixture());
  const oldButton=p.answers()[0];
  p.context.fetch=url=>url==='/api/conversations'?Promise.resolve({ok:true,json:async()=>[]}):new Promise(resolve=>pending.set(url,resolve));
  const first=p.evaluate("select('conversation-2')"),second=p.evaluate("select('conversation-3')");
  assert.equal(p.elements.get('conversation-save').disabled,true);
  oldButton.click();assert.equal(p.downloads.length,0);
  const selected=fixture();selected.conversation.id='conversation-3';selected.runs[0].conversation_id='conversation-3';selected.runs[0].response='別の会話の本文';
  pending.get('/api/conversations/conversation-3')({ok:true,json:async()=>selected});await second;
  pending.get('/api/conversations/conversation-2')({ok:true,json:async()=>fixture()});await first;
  p.answers()[0].click();
  assert.equal(await p.downloads[0].blob.text(),'別の会話の本文');
  assert.equal(p.evaluate('current'), 'conversation-3');
  assert.equal(p.evaluate('detail.conversation.id'), 'conversation-3');
});

test('stream completion enables saving the final response once, replacing provisional deltas',async()=>{
  const p=page(),value=fixture(['running'],['途中']);p.load(value);
  assert.equal(p.answers()[0].disabled,true);
  const final=structuredClone(value);final.runs[0].state='completed';final.runs[0].response='確定した本文';
  const events=[{seq:1,kind:'delta',text:'仮の本文'},{seq:2,kind:'result',response:'確定した本文',usage:null},{seq:3,kind:'terminal',state:'completed',reason:null,response:'確定した本文'}];
  p.context.fetch=async url=>{
    if(url.includes('/events'))return new Response(events.map(e=>JSON.stringify(e)).join('\n')+'\n');
    const result=url==='/api/status'?{acceptance:{ready:true,reason:'mock'},models:['mock-model'],busy:false,active:null}:url==='/api/conversations'?[final.conversation]:final;
    return {ok:true,json:async()=>result};
  };
  p.evaluate("active={id:'run-1',conversation_id:current};");
  await p.evaluate("watch('run-1')");
  assert.equal(p.answers()[0].disabled,false);
  assert.equal(p.elements.get('conversation-save').disabled,false);
  p.answers()[0].click();assert.equal(await p.downloads[0].blob.text(),'確定した本文');
});
