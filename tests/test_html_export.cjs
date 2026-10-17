// Browser rendering and the real Markdown/sanitizer are verified separately.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {page,fixture} = require('./webui_harness.cjs');

test('HTML download is a standalone UTF-8 reading document containing only the answer',async()=>{
  const p=page(),value=fixture();
  value.conversation.title='日本語 & <題名> "📄" </title><script>bad()</script>';
  value.runs[0].response='# 本文 📄\n\n読みやすい回答';
  value.runs[0].usage={total_tokens:987654};
  value.runs[0].reason='manual';
  value.runs[0].body.text='QUESTION_NOT_EXPORTED';
  value.attachments=[{id:'attachment-1',name:'SECRET_ATTACHMENT.md',text:'ATTACHMENT_NOT_EXPORTED'}];
  value.runs[0].body.attachment_ids=['attachment-1'];
  p.context.sessionStorage.setItem('agweb-token','TOKEN_NOT_EXPORTED');
  p.context.sessionStorage.setItem('agweb-owner-run-1','OWNER_NOT_EXPORTED');
  // Exercise the shared rendering boundary without claiming this stub sanitizes HTML.
  let parsed,sanitized;
  p.context.marked.parse=text=>{parsed=text;return '<h1>本文 📄</h1><p>読みやすい回答</p>';};
  p.context.DOMPurify.sanitize=html=>{sanitized=html;return html;};
  p.load(value);p.htmlAnswers()[0].click();
  const saved=p.downloads[0],text=await saved.blob.text();
  assert.equal(saved.name,'antigravity-answer-run-1.html');
  assert.equal(saved.blob.type,'text/html;charset=utf-8');
  assert.equal(parsed,value.runs[0].response);
  assert.equal(sanitized,'<h1>本文 📄</h1><p>読みやすい回答</p>');
  assert.match(text,/^<!doctype html>/i);
  assert.match(text,/<html lang="ja">/);
  assert.match(text,/<meta charset="utf-8">/);
  assert.match(text,/<meta name="viewport" content="width=device-width,initial-scale=1">/);
  assert.match(text,/<meta http-equiv="Content-Security-Policy"/);
  assert.ok(text.includes("default-src 'none'"));
  assert.match(text,/<style>[\s\S]+@media print[\s\S]+<\/style>/);
  assert.match(text,/<main[^>]*><h1>本文 📄<\/h1><p>読みやすい回答<\/p><\/main>/);
  assert.match(text,/<title>日本語 &amp; &lt;題名&gt; &quot;📄&quot; &lt;\/title&gt;&lt;script&gt;bad\(\)&lt;\/script&gt;<\/title>/);
  for(const excluded of ['QUESTION_NOT_EXPORTED','987654','CLI報告','SECRET_ATTACHMENT','ATTACHMENT_NOT_EXPORTED','TOKEN_NOT_EXPORTED','OWNER_NOT_EXPORTED','/api/','/static/','回答を.mdで保存','回答を.htmlで保存'])assert.ok(!text.includes(excluded),excluded);
  assert.doesNotMatch(text,/<(?:script|link|button|input|form)\b/i);
  const bytes=Buffer.from(await saved.blob.arrayBuffer());
  assert.deepEqual(bytes,Buffer.from(text,'utf8'));
  assert.notDeepEqual([...bytes.subarray(0,3)],[0xef,0xbb,0xbf]);
  assert.equal(p.context.document.body.children.length,0);
  p.timers.forEach(fn=>fn());assert.equal(p.revoked.length,1);
});

test('HTML uses the same terminal and nonempty conditions as Markdown during generation',()=>{
  const p=page();
  p.load(fixture(['completed','cancelled','failed','timed_out','starting','running','stopping','completed'],['保存','中断','失敗','時間切れ','途中','途中','途中',' \n\t']));
  assert.deepEqual(p.htmlAnswers().map(b=>b.disabled),[false,false,false,false,true,true,true,true]);
  for(const b of p.htmlAnswers())b.click();
  assert.equal(p.downloads.length,4);
  assert.equal(p.elements.get('conversation-save').disabled,true);
});

test('HTML saving ignores a stale answer button after switching or clearing conversations',()=>{
  const p=page();p.load(fixture());const old=p.htmlAnswers()[0];
  p.evaluate("current='loading';renderControls();");old.click();
  assert.equal(p.downloads.length,0);
  p.evaluate('resetConversation();renderControls();');old.click();
  assert.equal(p.downloads.length,0);assert.equal(p.htmlAnswers().length,0);
});

test('HTML download errors are visible and temporary resources are released',()=>{
  const p=page();p.load(fixture());p.failDownload();p.htmlAnswers()[0].click();
  assert.equal(p.downloads.length,0);
  assert.equal(p.elements.get('error').hidden,false);
  assert.match(p.elements.get('error').textContent,/HTML.*ダウンロード/);
  assert.equal(p.context.document.body.children.length,0);
  p.timers.forEach(fn=>fn());assert.equal(p.revoked.length,1);
});

test('conversation HTML contains ordered questions, answers, states and attachment names',async()=>{
  const p=page(),value=fixture(['completed','cancelled','failed','timed_out'],['最初の回答','中断した本文','失敗した本文',' \n']);
  value.attachments=[{id:'a1',name:'資料.md',text:'ATTACHMENT_SECRET',saved:'private/path'},{id:'a2',name:'未使用.txt'}];
  value.runs[0].body.attachment_ids=['a1'];value.runs[0].usage={total_tokens:987654};
  p.load(value);
  p.context.fetch=()=>{throw new Error('Saving must not contact the server');};
  p.elements.get('conversation-save-html').click();
  const saved=p.downloads[0],text=await saved.blob.text();
  assert.equal(saved.name,'antigravity-conversation-conversation-1.html');
  assert.equal(saved.blob.type,'text/html;charset=utf-8');
  assert.match(text,/<title>日本語の会話 📄<\/title>/);
  assert.match(text,/<meta charset="utf-8">/);
  // The harness leaves Markdown unchanged; real conversion is checked in the browser.
  const ordered=['# 日本語の会話 📄','モデル: mock-model','考える量: low','## 質問 1','質問1','資料.md','## 回答 1（完了）','最初の回答','## 質問 2','質問2','## 回答 2（中断）','中断した本文','## 質問 3','質問3','## 回答 3（失敗）','失敗した本文','## 質問 4','質問4','## 回答 4（時間切れ）','回答本文なし'];
  let offset=text.indexOf('<main');
  for(const part of ordered){const at=text.indexOf(part,offset);assert.ok(at>=offset,part);offset=at+part.length;}
  for(const excluded of ['ATTACHMENT_SECRET','private/path','未使用.txt','987654','CLI報告','会話を.htmlで保存','/api/','/static/'])assert.ok(!text.includes(excluded),excluded);
  assert.notDeepEqual([...Buffer.from(await saved.blob.arrayBuffer()).subarray(0,3)],[0xef,0xbb,0xbf]);
  assert.equal(p.context.document.body.children.length,0);
  p.timers.forEach(fn=>fn());assert.equal(p.revoked.length,1);
});

test('both conversation formats use the same saving conditions and become ready after completion',()=>{
  const p=page(),buttons=['conversation-save','conversation-save-html'];
  const check=disabled=>{for(const id of buttons)assert.equal(p.elements.get(id).disabled,disabled,id);};
  p.evaluate('renderMessages();renderControls();');check(true);
  p.load(fixture([]));check(true);
  p.load(fixture(['completed','cancelled','failed','timed_out'],['本文','','','']));check(false);
  for(const flag of ['submitting','deleting']){
    p.evaluate(flag+'=true;renderControls();');check(true);
    p.evaluate(flag+'=false;renderControls();');check(false);
  }
  p.evaluate("active={conversation_id:current,state:'running'};renderControls();");check(true);
  p.evaluate("active=null;current='loading';renderControls();");check(true);
  p.load(fixture(['completed','running']));check(true);
  p.evaluate("detail.runs[1].state='completed';renderMessages();");check(false);
  p.elements.get('new').click();check(true);
});

test('conversation HTML rejects saving during loading or generation even through its handler',()=>{
  const p=page();p.load(fixture());const button=p.elements.get('conversation-save-html');
  p.evaluate("current='loading';");button.onclick();assert.equal(p.downloads.length,0);
  p.load(fixture(['running']));button.onclick();assert.equal(p.downloads.length,0);
});

test('conversation HTML download failure is visible and preserves the selected conversation',()=>{
  const p=page();p.load(fixture());p.failDownload();p.elements.get('conversation-save-html').click();
  assert.equal(p.downloads.length,0);assert.equal(p.evaluate('current'),'conversation-1');
  assert.equal(p.elements.get('error').hidden,false);
  assert.match(p.elements.get('error').textContent,/HTML.*ダウンロード/);
  assert.equal(p.context.document.body.children.length,0);
  p.timers.forEach(fn=>fn());assert.equal(p.revoked.length,1);
});
