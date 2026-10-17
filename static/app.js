'use strict';
const $ = id => document.getElementById(id);
let token=location.hash.slice(1)||sessionStorage.getItem('agweb-token')||'';
if(token){sessionStorage.setItem('agweb-token',token);history.replaceState(null,'',location.pathname);}
let current=null, detail=null, status=null, active=null, selected=[], streamAbort=null, streamId=null;
let quotaBusy=false, submitting=false, deleting=false, fileBusy=false, metadataBusy=false, setupBusy=false, listRevision=0;
let renameId=null, searchTimer=null;
let outputSettings={folder:'',revision:0},outputSettingsDirty=false;
const terminalStates=new Set(['completed','cancelled','timed_out','failed']);
const labels={starting:'準備中',running:'実行中',stopping:'停止中',completed:'完了',cancelled:'中断',timed_out:'時間切れ',failed:'失敗'};
const outputLabels={pending:'生成中',completed:'保存完了',incomplete:'未完了',missing:'ファイル未生成',invalid:'出力ファイルが無効です'};
const modeMismatchMessage='出力先が＜通常回答＞になっています。テキストファイルを出力したい場合は＜テキストファイル＞を選んでください。';
const newFileConversationMessage='この会話の出力先は変更できません。「＋ 新しい会話」から「テキストファイル」を選んでください。';
const reasons={manual:'手動停止',browser_disconnected:'画面との接続断',server_disconnected:'アプリとの接続断',app_shutdown:'アプリ終了',time_limit:'時間上限',guard_denied:'許可されていない操作を停止',guard_error:'ガード異常',guard_not_observed:'ガード未確認',model_unverified:'モデルを確認できません',invalid_output:'CLI出力の形式異常',cli_failed:'CLI処理失敗',exit_timeout:'終了確認の時間超過',descendants_remain:'子プロセスが残存',app_restarted:'アプリ再起動',supervisor_lost:'監視処理が終了',persistence_failed:'保存失敗',launch_failed:'起動失敗',supervisor_startup_error:'監視処理の起動失敗'};
Object.assign(reasons,{output_missing:'出力ファイルが作成されませんでした',output_invalid:'出力ファイルを検証できませんでした'});
reasons.output_mode_mismatch=modeMismatchMessage+' '+newFileConversationMessage;
function error(message){$('error').textContent=message;$('error').hidden=!message;}
async function api(path,opts={}){
  const headers={Authorization:'Bearer '+token,...opts.headers};
  if(opts.body && !(opts.body instanceof FormData))headers['Content-Type']='application/json';
  const response=await fetch('/api'+path,{...opts,headers});
  const data=await response.json();
  if(!response.ok){const e=new Error(typeof data.detail==='string'?data.detail:'入力を確認してください。');e.status=response.status;throw e;}
  return data;
}
function owner(id){return sessionStorage.getItem('agweb-owner-'+id);}
function rememberDraft(){
  if(current&&detail?.conversation.id!==current)return;
  const id=current||'new';
  sessionStorage.setItem('draft-'+id,$('prompt').value);
  if(!detail?.conversation.instruction_locked)sessionStorage.setItem('instruction-draft-'+id,$('system-prompt').value);
  if(!detail?.conversation.instruction_locked)sessionStorage.setItem('output-draft-'+id,outputMode());
}
function loadInstruction(){
  const c=detail?.conversation,id=current||'new';
  $('system-prompt').value=c?.instruction_locked?(c.system_prompt||''):(sessionStorage.getItem('instruction-draft-'+id)??c?.system_prompt??'');
  if(c?.instruction_locked)sessionStorage.removeItem('instruction-draft-'+id);
  $('output-mode').value=c?.instruction_locked?(c.output_mode||'chat'):(sessionStorage.getItem('output-draft-'+id)??c?.output_mode??'chat');
  if(c?.instruction_locked)sessionStorage.removeItem('output-draft-'+id);
}
function outputMode(){return $('output-mode').value||'chat';}
function outputModeWarning(){
  const text=$('prompt').value;
  const requested=/(?:\.txt\b|テキストファイル)/i.test(text)&&/(?:書き出|出力|保存|作成|生成)(?:して(?:ください)?|(?:す|する)ようにしてください|をお願いします)/.test(text);
  return outputMode()==='chat'&&requested?modeMismatchMessage+(detail?.conversation.instruction_locked?' '+newFileConversationMessage:''):'';
}
function instructionText(){return detail?.conversation.instruction_locked?(detail.conversation.system_prompt||''):$('system-prompt').value;}
async function updateConversation(fields,cid=current){
  const updated=await api('/conversations/'+cid,{method:'PATCH',body:JSON.stringify(fields)});
  if(current===cid&&detail){detail.conversation=updated;renderMessages();renderControls();}
  await list();return updated;
}
async function saveInstruction(force=false){
  if(!current){await create($('prompt').value);return;}
  if(detail?.conversation.instruction_locked)return;
  const cid=current,value=$('system-prompt').value;
  if(force||value!==detail.conversation.system_prompt||outputMode()!==(detail.conversation.output_mode||'chat')){
    await updateConversation({system_prompt:value,output_mode:outputMode(),revision:detail.conversation.revision},cid);
  }
  sessionStorage.removeItem('instruction-draft-'+cid);
  sessionStorage.removeItem('output-draft-'+cid);
  $('instruction-state').textContent=detail.conversation.instruction_file_error||'保存しました';
}
async function copyText(text,label){
  try{
    if(!navigator.clipboard?.writeText)throw new Error('このブラウザーではクリップボードを利用できません。');
    await navigator.clipboard.writeText(text);$('copy-state').textContent=label+'をコピーしました。';
  }catch(e){$('copy-state').textContent=label+'をコピーできません。'+e.message;}
}
function canSaveAnswer(run){
  return Boolean(detail&&detail.conversation.output_mode!=='text_file'&&!run.output&&detail.conversation.id===current&&detail.runs.includes(run)&&terminalStates.has(run.state)&&run.response?.trim());
}
function canSaveOutput(run){return Boolean(detail&&detail.conversation.id===current&&detail.runs.includes(run)&&terminalStates.has(run.state)&&['completed','incomplete'].includes(run.output?.state));}
async function downloadOutput(run){
  if(!canSaveOutput(run))return;
  try{
    const r=await fetch('/api/runs/'+run.id+'/output',{headers:{Authorization:'Bearer '+token}});
    if(!r.ok)throw new Error((await r.json()).detail||'出力ファイルを取得できません。');
    const blob=await r.blob();
    if(canSaveOutput(run))downloadFile(blob,run.output.name,'text/plain;charset=utf-8','テキスト');
  }catch(e){error(e.message);}
}
function canSaveConversation(){
  return Boolean(detail&&detail.conversation.id===current&&detail.runs.length&&!submitting&&!deleting&&detail.runs.every(r=>terminalStates.has(r.state))&&!(active?.conversation_id===current&&!terminalStates.has(active.state)));
}
function canDeleteConversation(){
  return Boolean(status&&detail&&detail.conversation.id===current&&!status.busy&&!quotaBusy&&!submitting&&!deleting&&!fileBusy&&!metadataBusy&&detail.runs.every(r=>terminalStates.has(r.state))&&(!active||terminalStates.has(active.state)));
}
function resetConversation(){
  streamAbort?.abort();streamAbort=null;streamId=null;
  current=null;detail=null;selected=[];sessionStorage.removeItem('last-conversation');
  $('title').textContent='新しい会話';$('prompt').value=sessionStorage.getItem('draft-new')||'';$('file').value='';
  loadInstruction();$('instruction-state').textContent='';$('copy-state').textContent='';
  renderMessages();renderAttachments();
}
function forgetConversation(id,runs=[]){
  sessionStorage.removeItem('draft-'+id);
  sessionStorage.removeItem('instruction-draft-'+id);
  sessionStorage.removeItem('output-draft-'+id);
  for(const run of runs)sessionStorage.removeItem('agweb-owner-'+run.id);
  if(sessionStorage.getItem('last-conversation')===id)sessionStorage.removeItem('last-conversation');
  if(current===id)resetConversation();
}
function conversationMarkdown(value){
  const c=value.conversation;
  const parts=['# '+c.title.replace(/[\r\n]+/g,' '),'モデル: '+c.model+'\n\n考える量: '+c.effort];
  value.runs.forEach((run,index)=>{
    parts.push('## 質問 '+(index+1),run.body.text);
    const names=run.body.attachment_ids.map(id=>value.attachments.find(a=>a.id===id)?.name).filter(Boolean);
    if(names.length)parts.push('添付ファイル:\n'+names.map(name=>'- '+name.replace(/[\r\n]+/g,' ')).join('\n'));
    parts.push('## 回答 '+(index+1)+'（'+(labels[run.state]||run.state)+'）');
    if(run.output||c.output_mode==='text_file')parts.push('テキストファイル: '+(run.output?.name||'未生成')+'（'+(outputLabels[run.output?.state]||'ファイル未生成')+'）');
    else parts.push(run.response?.trim()?run.response:'回答本文なし');
  });
  return parts.join('\n\n')+'\n';
}
function downloadFile(text,filename,type,label){
  let url,link;
  try{
    error('');
    url=URL.createObjectURL(new Blob([text],{type}));
    link=document.createElement('a');link.href=url;link.download=filename;
    document.body.append(link);link.click();
  }catch(e){error(label+'をダウンロードできません。'+e.message);}
  finally{link?.remove();if(url)setTimeout(()=>URL.revokeObjectURL(url),1000);}
}
function renderMessages(){
  $('conversation-save').disabled=$('conversation-save-html').disabled=!canSaveConversation();
  if(detail)$('title').textContent=detail.conversation.title;
  if(!detail||!detail.runs.length){$('messages').innerHTML='<div class="welcome"><div class="welcome-icon">↗</div><h2>ひとつずつ、確かめながら。</h2><p>相談したいことを書いて、送信してください。</p></div>';return;}
  $('messages').replaceChildren();
  for(const run of detail.runs){
    const user=document.createElement('article');user.className='message user';
    const label=document.createElement('small');label.textContent='YOU';user.append(label,document.createTextNode(run.body.text));
    if(run.body.attachment_ids.length){const note=document.createElement('div');note.className='result-label';note.textContent='資料 '+run.body.attachment_ids.length+'件';user.append(note);}
    const assistant=document.createElement('article');assistant.className='message assistant';
    const heading=document.createElement('div');heading.className='answer-heading';
    const head=document.createElement('small');head.textContent='ANTIGRAVITY · '+(labels[run.state]||run.state);
    if(run.output||detail.conversation.output_mode==='text_file'){
      const artifact=run.output||{state:'pending'};
      const info=document.createElement('p');info.className='output-summary';info.textContent=(outputLabels[artifact.state]||artifact.state)+' · '+(artifact.name||'')+(artifact.size?' · '+artifact.size.toLocaleString()+'バイト':'');
      const save=document.createElement('button');save.textContent='.txtを保存';save.disabled=!canSaveOutput(run);save.onclick=()=>downloadOutput(run);
      const folder=document.createElement('button');folder.textContent='保存先を開く';folder.disabled=!canSaveOutput(run);
      folder.onclick=async()=>{if(!canSaveOutput(run))return;try{await api('/runs/'+run.id+'/output/open-folder',{method:'POST'});}catch(e){error(e.message);}};
      const actions=document.createElement('div');actions.className='answer-actions';actions.append(save,folder);
      heading.append(head,actions);assistant.append(heading,info);
      if(artifact.path){const path=document.createElement('p');path.className='output-path';path.textContent=artifact.path;assistant.append(path);}
      const note=document.createElement('div');note.className='result-label';note.textContent=[run.reason?(reasons[run.reason]||run.reason):'',run.usage?'CLI報告: '+JSON.stringify(run.usage):'使用量: 不明'].filter(Boolean).join(' · ');assistant.append(note);
      $('messages').append(user,assistant);continue;
    }
    const save=document.createElement('button');save.className='save-answer';save.textContent='回答を.mdで保存';save.disabled=!canSaveAnswer(run);
    save.onclick=()=>{if(canSaveAnswer(run))downloadFile(run.response,'antigravity-answer-'+run.id+'.md','text/markdown;charset=utf-8','Markdown');};
    const saveHtml=document.createElement('button');saveHtml.className='save-answer';saveHtml.textContent='回答を.htmlで保存';saveHtml.disabled=!canSaveAnswer(run);
    saveHtml.onclick=()=>{if(canSaveAnswer(run))downloadFile(readingHtml(detail.conversation.title,run.response),'antigravity-answer-'+run.id+'.html','text/html;charset=utf-8','HTML');};
    const copy=document.createElement('button');copy.className='copy-answer';copy.textContent='回答をコピー';copy.disabled=!canSaveAnswer(run);
    copy.onclick=()=>{if(canSaveAnswer(run))return copyText(run.response,'回答');};
    const actions=document.createElement('div');actions.className='answer-actions';actions.append(copy,save,saveHtml);
    heading.append(head,actions);assistant.append(heading);
    const body=document.createElement('div');body.innerHTML=safeMarkdown(run.response||'応答を待っています…');
    for(const a of body.querySelectorAll('a')){a.target='_blank';a.rel='noopener noreferrer';}
    for(const code of body.querySelectorAll('pre code')){
      const text=code.textContent;
      const button=document.createElement('button');button.className='copy-code';button.textContent='コードをコピー';button.disabled=!canSaveAnswer(run);
      button.onclick=()=>{if(canSaveAnswer(run))return copyText(text,'コード');};
      code.parentElement.before(button);
    }
    assistant.append(body);
    if(run.reason||run.usage){const note=document.createElement('div');note.className='result-label';note.textContent=[run.reason?(reasons[run.reason]||run.reason):'',run.usage?'CLI報告: '+JSON.stringify(run.usage):'使用量: 不明'].filter(Boolean).join(' · ');assistant.append(note);}
    $('messages').append(user,assistant);
  }
}
function renderAttachments(){
  $('attachments').replaceChildren();
  for(const id of selected){
    const row=detail.attachments.find(a=>a.id===id);if(!row)continue;
    const chip=document.createElement('span');chip.className='attachment';chip.textContent=row.name;
    const remove=document.createElement('button');remove.textContent='×';remove.ariaLabel=row.name+'を今回の送信から外す';remove.onclick=()=>{selected=selected.filter(x=>x!==id);renderAttachments();};chip.append(remove);$('attachments').append(chip);
  }
  renderControls();
}
function renderControls(){
  $('conversation-save').disabled=$('conversation-save-html').disabled=!canSaveConversation();
  $('conversation-delete').disabled=!canDeleteConversation();
  const busy=quotaBusy||submitting||deleting||fileBusy||metadataBusy||setupBusy||Boolean(status?.busy), readOnly=detail?.conversation.resumable===0;
  const modelUnavailable=status?.acceptance.operational&&!status.acceptance.models.includes(detail?.conversation.model||$('model').value);
  for(const button of $('conversations').querySelectorAll('button'))button.disabled=deleting||submitting||fileBusy||metadataBusy;
  $('rename').disabled=$('favorite').disabled=!detail||deleting||submitting||fileBusy||metadataBusy;
  $('favorite').textContent=detail?.conversation.is_favorite?'★ お気に入り':'☆ お気に入り';
  $('favorite').setAttribute('aria-pressed',String(Boolean(detail?.conversation.is_favorite)));
  $('system-prompt').disabled=busy||Boolean(current&&!detail)||Boolean(detail?.conversation.instruction_locked);
  $('output-mode').disabled=busy||Boolean(current&&!detail)||Boolean(detail?.conversation.instruction_locked);
  $('instruction-save').disabled=busy||Boolean(current&&!detail)||Boolean(detail?.conversation.instruction_locked)||!$('model').value;
  if(detail?.conversation.instruction_locked)$('instruction-state').textContent='初回送信済みのため変更できません';
  const instructionDirty=Boolean(detail&&instructionText()!==detail.conversation.system_prompt);
  $('instruction-folder-open').disabled=busy||!detail||instructionDirty||!detail.conversation.instruction_file_ready;
  $('instruction-folder-state').textContent=!detail?'指示を入力して「指示を保存」を押すと保存先を開けます。':instructionDirty?'指示に未保存の変更があります。':!instructionText()?'指示が空欄のためAGENTS.mdは作成していません。':!detail.conversation.instruction_file_ready?(detail.conversation.instruction_file_error||'AGENTS.mdは未反映です。指示を保存してください。'):'';
  $('send').disabled=!status?.acceptance.ready||status?.persistence_failed||!$('model').value||modelUnavailable||busy||readOnly;
  const fileMode=outputMode()==='text_file';
  $('output-hint').hidden=!fileMode;
  $('output-hint').textContent=fileMode?(!outputSettings.folder?'「テキストの保存先」で保存先を登録してください。':status?.text_output_ready?'本文はチャットに表示せず、登録先に指定名の.txtを作成します。同名には連番を付けます。':'テキストファイル出力は未有効化です。初回設定の同意を確認してください。'):'';
  if(fileMode&&(!status?.text_output_ready||!outputSettings.folder))$('send').disabled=true;
  $('output-folder').disabled=busy;
  $('output-folder-save').disabled=busy||!$('output-folder').value.trim();
  $('output-folder-open').disabled=busy||!outputSettings.folder||outputSettingsDirty;
  const modeWarning=outputModeWarning();
  $('output-mode-warning').hidden=!modeWarning;$('output-mode-warning').textContent=modeWarning;
  $('new').disabled=busy||!status?.models.length;
  $('file').disabled=busy||readOnly||!$('model').value;
  $('prompt').disabled=busy||readOnly;
  $('model').disabled=Boolean(current)||busy;$('effort').disabled=Boolean(current)||busy;
  if(status?.acceptance.operational){
    const pairs=status.acceptance.allowed_pairs||status.acceptance.tested_pairs||[];
    const efforts=pairs.filter(p=>p.model===$('model').value).map(p=>p.effort);
    for(const o of $('effort').options)o.disabled=!efforts.includes(o.value);
    if(!current&&!efforts.includes($('effort').value))$('effort').value=efforts[0]||'';
  }
  $('model-hint').textContent=current&&modelUnavailable?'この会話のモデルは現在利用できません。履歴は閲覧・保存できます。「＋ 新しい会話」で利用可能なモデルを選んでください。':current?'この会話のモデルは固定です。変更するには「＋ 新しい会話」を押してください。':'モデルは会話ごとに選べます。考える量は選択したモデル変種に合わせて設定されます。';
  $('timeout').disabled=busy;
  $('stop').disabled=!active||!owner(active.id)||active.state==='stopping'||status?.persistence_failed||!status?.busy;
  $('claim').hidden=!active||Boolean(owner(active.id));
  $('readonly').hidden=!readOnly;
  $('copy').disabled=busy;
  $('models-refresh').disabled=busy;
  $('setup-open').disabled=busy;
  $('quota-open').disabled=busy||!status;
  const chars=Array.from($('prompt').value).length+Array.from(instructionText()).length+selected.reduce((n,id)=>n+(detail?.attachments.find(a=>a.id===id)?.chars||0),0);
  $('count').textContent=chars.toLocaleString()+' / 20,000文字';
  if(chars>20000)$('send').disabled=true;
  if(new TextEncoder().encode(instructionText()).length>24000){$('send').disabled=true;$('instruction-state').textContent='指示はUTF-8で24,000バイトまでです。';}
  if(!$('prompt').value.trim()&&!selected.length)$('send').disabled=true;
}
async function list(){
  const revision=++listRevision,query=$('history-search').value;
  const conversations=await api('/conversations'+(query?'?q='+encodeURIComponent(query):''));if(revision!==listRevision)return;
  $('conversations').replaceChildren();
  if(!conversations.length){const empty=document.createElement('p');empty.textContent=query?'一致する会話はありません':'会話はまだありません';$('conversations').append(empty);}
  for(const [favorite,label] of [[true,'お気に入り'],[false,'会話履歴']]){
    const rows=conversations.filter(c=>Boolean(c.is_favorite)===favorite);if(!rows.length)continue;
    const heading=document.createElement('small');heading.textContent=label;$('conversations').append(heading);
    for(const c of rows){const b=document.createElement('button');b.textContent=(c.is_favorite?'★ ':'')+c.title;b.className=c.id===current?'active':'';b.disabled=deleting||submitting||fileBusy||metadataBusy;b.onclick=()=>select(c.id).catch(e=>error(e.message));$('conversations').append(b);}
  }
}
async function select(id,internal=false,saveDraft=true){
  if(deleting||(metadataBusy&&!internal))return;
  if(saveDraft)rememberDraft();
  current=id;sessionStorage.setItem('last-conversation',id);selected=[];
  detail=null;$('title').textContent='会話を読み込んでいます…';renderMessages();renderControls();
  let loaded;
  try{loaded=await api('/conversations/'+id);}
  catch(e){if(e.status===404){forgetConversation(id);await list();return;}throw e;}
  if(current!==id)return;
  detail=loaded;$('title').textContent=detail.conversation.title;
  loadInstruction();$('instruction-state').textContent='';$('copy-state').textContent='';
  $('model').value=detail.conversation.model;$('effort').value=detail.conversation.effort;
  $('prompt').value=sessionStorage.getItem('draft-'+id)||'';
  renderMessages();renderAttachments();await list();
  if(active&&active.conversation_id===id)watch(active.id);
}
async function create(copy=''){
  const wasNew=!current;
  const model=$('model').value,effort=$('effort').value;
  const c=await api('/conversations',{method:'POST',body:JSON.stringify({model,effort,system_prompt:$('system-prompt').value,output_mode:outputMode()})});
  await select(c.id,true);$('prompt').value=copy;renderControls();
  if(wasNew){sessionStorage.removeItem('draft-new');sessionStorage.removeItem('instruction-draft-new');sessionStorage.removeItem('output-draft-new');}
}
async function refresh(){
  status=await api('/status');active=status.active;
  outputSettings=status.output_settings||outputSettings;
  if(!outputSettingsDirty)$('output-folder').value=outputSettings.folder;
  $('gate').textContent=status.acceptance.reason;
  if(status.persistence_failed)error('実行監視または履歴保存に失敗したため、新規送信を停止しています。アプリを再起動して履歴を確認してください。');
  if(status.deletion_warning)error(status.deletion_warning);
  const wanted=detail?.conversation.model||$('model').value;
  $('model').replaceChildren();
  if(status.acceptance.mode==='real'){const choose=document.createElement('option');choose.value='';choose.textContent='モデルを選択';choose.disabled=true;$('model').append(choose);}
  const models=status.acceptance.operational?status.acceptance.models:status.models;
  for(const m of models){const o=document.createElement('option');o.value=m;o.textContent=m;$('model').append(o);}
  if(wanted)$('model').value=wanted;else if(status.acceptance.mode==='real')$('model').value='';
  if(status.acceptance.operational){
    if(!current&&!models.includes($('model').value))$('model').value=(status.acceptance.allowed_pairs||status.acceptance.tested_pairs||[]).find(p=>models.includes(p.model))?.model||'';
  }
  $('state').textContent=active?(labels[active.state]||active.state):'待機中';
  if(active){
    const elapsed=Math.max(0,Math.floor(Date.now()/1000-active.created));
    $('clock').textContent='経過 '+elapsed+'秒 / 残り '+Math.max(0,active.timeout-elapsed)+'秒';
    if(current===active.conversation_id)watch(active.id);
  }else{$('clock').textContent='';}
  renderControls();
}
async function watch(id,force=false){
  if(streamId===id&&!force)return;
  if(streamAbort)streamAbort.abort();
  streamId=id;streamAbort=new AbortController();
  const connection=streamAbort;
  const cid=current,runs=detail?.runs||[];
  let renderTimer=null;
  const renderPending=()=>{if(renderTimer!==null)clearTimeout(renderTimer);renderTimer=null;if(streamAbort===connection&&current===cid)renderMessages();};
  // Replay deltas into a fresh buffer; final result replaces them instead of appending.
  let after=0;
  try{
    const response=await fetch('/api/runs/'+id+'/events?after='+after,{headers:{Authorization:'Bearer '+token,...(owner(id)?{'X-Run-Owner':owner(id)}:{})},signal:connection.signal});
    if(!response.ok){const e=new Error('イベントを取得できません。');e.status=response.status;throw e;}
    const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
    let accumulated='';
    while(true){
      const {value,done}=await reader.read();if(done)break;
      if(streamAbort!==connection)return;
      pending+=decoder.decode(value,{stream:true});
      let index;
      while((index=pending.indexOf('\n'))>=0){
        const event=JSON.parse(pending.slice(0,index));pending=pending.slice(index+1);after=event.seq;
        if(event.kind==='keepalive')continue;
        if(current!==active?.conversation_id)continue;
        const local=detail?.runs.find(r=>r.id===id);if(!local)continue;
        if(event.kind==='delta'&&!local.output){accumulated+=event.text;local.response=accumulated;}
        if(event.kind==='result'){local.response=local.output?'':event.response;local.usage=event.usage;}
        if(event.kind==='submitted')local.state='running';
        if(event.kind==='output_assigned'&&local.output)local.output={...local.output,name:event.name,path:local.output_root+'\\'+event.name};
        if(event.kind==='stopping'){local.state='stopping';local.reason=event.reason;}
        if(event.kind==='tool')$('state').textContent='処理中: '+event.name;
        if(event.kind==='terminal'){local.state=event.state;local.reason=event.reason;if(event.output)local.output=event.output;if(event.response!==null)local.response=local.output?'':event.response;}
        if(event.kind==='delta'){
          if(renderTimer===null)renderTimer=setTimeout(renderPending,100);
        }else renderPending();
      }
    }
    if(current===cid){const loaded=await api('/conversations/'+cid);if(current===cid){detail=loaded;renderMessages();await list();}}
    await refresh();
  }catch(e){
    if(e.status===404){forgetConversation(cid,runs);await list().catch(e=>error(e.message));}
    else if(e.name!=='AbortError')error('接続が切れました。再読込で履歴を確認できます。再送は行っていません。');
  }
  finally{renderPending();if(streamAbort===connection)streamId=null;}
}
$('new').onclick=()=>{if(deleting||submitting||fileBusy||metadataBusy)return;error('');rememberDraft();resetConversation();renderControls();list().catch(e=>error(e.message));};
$('send').onclick=async()=>{
  if(deleting||submitting||fileBusy)return;
  const modeWarning=outputModeWarning();
  if(modeWarning){rememberDraft();error('');renderControls();return;}
  try{
    error('');submitting=true;renderControls();
    if(!current)await create($('prompt').value);
    await saveInstruction();
    const body={request_id:crypto.randomUUID(),revision:detail.conversation.revision,text:$('prompt').value,attachment_ids:selected,timeout:Number($('timeout').value)};
    const cid=current;
    const r=await api('/conversations/'+cid+'/runs',{method:'POST',body:JSON.stringify(body)});
    if(r.owner)sessionStorage.setItem('agweb-owner-'+r.run.id,r.owner);
    sessionStorage.removeItem('draft-'+cid);
    if(current===cid){$('prompt').value='';selected=[];}
    const loaded=await api('/conversations/'+cid);
    if(current===cid){detail=loaded;loadInstruction();renderMessages();renderAttachments();}
    await refresh();
  }catch(e){error(e.message);}
  finally{submitting=false;renderControls();}
};
$('conversation-save').onclick=()=>{if(canSaveConversation())downloadFile(conversationMarkdown(detail),'antigravity-conversation-'+detail.conversation.id+'.md','text/markdown;charset=utf-8','Markdown');};
$('conversation-save-html').onclick=()=>{if(canSaveConversation())downloadFile(readingHtml(detail.conversation.title,conversationMarkdown(detail)),'antigravity-conversation-'+detail.conversation.id+'.html','text/html;charset=utf-8','HTML');};
$('conversation-delete').onclick=async()=>{
  if(!canDeleteConversation())return;
  const cid=current,runs=detail.runs;
  if(!confirm('「'+detail.conversation.title+'」を削除しますか？\n質問・回答・添付ファイル・作業フォルダーを含めて削除され、元に戻せません。\n登録フォルダーへ書き出したテキストファイルは残ります。'))return;
  deleting=true;listRevision++;error('');renderControls();
  try{
    const result=await api('/conversations/'+cid,{method:'DELETE'});
    forgetConversation(cid,runs);await list();await refresh();
    if(result.warning)error(result.warning);
  }catch(e){
    if(e.status===404){forgetConversation(cid,runs);await list().catch(e=>error(e.message));}
    else error(e.message);
  }finally{deleting=false;renderControls();}
};
$('stop').onclick=async()=>{if(!active)return;try{await api('/runs/'+active.id+'/stop',{method:'POST',headers:{'X-Run-Owner':owner(active.id)||''}});$('stop').disabled=true;}catch(e){error(e.message);}};
$('claim').onclick=async()=>{if(!active)return;try{const id=active.id,r=await api('/runs/'+id+'/claim',{method:'POST'});sessionStorage.setItem('agweb-owner-'+id,r.owner);watch(id,true);renderControls();}catch(e){error(e.message);}};
$('copy').onclick=async()=>{if(deleting)return;try{const text=detail.runs.at(-1)?.body.text||'';const model=detail.conversation.model,effort=detail.conversation.effort;current=null;$('model').value=model;$('effort').value=effort;await create(text);}catch(e){error(e.message);}};
$('file').onchange=async()=>{if(deleting)return;fileBusy=true;renderControls();try{if(!current)await create($('prompt').value);for(const f of $('file').files){const form=new FormData();form.append('file',f);const a=await api('/conversations/'+current+'/attachments',{method:'POST',body:form});detail.attachments.push(a);selected.push(a.id);}renderAttachments();}catch(e){error(e.message);}finally{fileBusy=false;$('file').value='';renderControls();}};
$('models-refresh').onclick=async()=>{try{error('');quotaBusy=true;renderControls();const r=await api('/models/refresh',{method:'POST'});if(r.ok===false)error(r.reason);await refresh();}catch(e){error(e.message);}finally{quotaBusy=false;renderControls();}};
$('quota-open').onclick=async()=>{
  if(quotaBusy)return;
  quotaBusy=true;renderControls();
  $('quota-dialog').showModal();$('quota-state').textContent='クォータを取得しています…（最大30秒）';$('quota-text').textContent='';
  try{
    const r=await api('/quota/refresh',{method:'POST'});
    if(!r.ok)throw new Error(r.reason);
    $('quota-text').textContent=r.text;
    $('quota-state').textContent='取得日時: '+new Date(r.fetched_at).toLocaleString();
  }catch(e){$('quota-state').textContent=e.message;}
  finally{quotaBusy=false;await refresh().catch(()=>{});renderControls();}
};
$('quota-close').onclick=()=>$('quota-dialog').close();
$('history-open').onclick=async()=>{
  $('history-open').disabled=true;
  try{const r=await api('/history/open-folder',{method:'POST'});$('history-note').textContent=r.message;}
  catch(e){$('history-note').textContent=e.message;}
  finally{$('history-open').disabled=false;}
};
$('prompt').oninput=()=>{rememberDraft();renderControls();};
$('system-prompt').oninput=()=>{rememberDraft();$('instruction-state').textContent='未保存（送信時にも保存します）';renderControls();};
$('output-mode').onchange=()=>{rememberDraft();renderControls();};
$('output-folder').oninput=()=>{outputSettingsDirty=true;$('output-folder-state').textContent='未登録の変更';renderControls();};
$('output-folder-open').onclick=async()=>{
  if(!outputSettings.folder||outputSettingsDirty||metadataBusy||status?.busy)return;
  try{await api('/output-settings/open-folder',{method:'POST'});$('output-folder-state').textContent='このPCのエクスプローラーを開きました。';}
  catch(e){$('output-folder-state').textContent=e.message;}
};
$('instruction-folder-open').onclick=async()=>{
  if(!detail||!detail.conversation.instruction_file_ready||instructionText()!==detail.conversation.system_prompt||metadataBusy||status?.busy)return;
  try{await api('/conversations/'+detail.conversation.id+'/instructions/open-folder',{method:'POST'});$('instruction-state').textContent='このPCでAGENTS.mdの保存先を開きました。';}
  catch(e){$('instruction-state').textContent=e.message;}
};
$('output-folder-save').onclick=async()=>{
  if(metadataBusy||submitting||deleting||status?.busy)return;
  metadataBusy=true;renderControls();
  try{
    outputSettings=await api('/output-settings',{method:'PUT',body:JSON.stringify({folder:$('output-folder').value.trim(),revision:outputSettings.revision})});
    outputSettingsDirty=false;$('output-folder').value=outputSettings.folder;$('output-folder-state').textContent='登録しました';
  }catch(e){$('output-folder-state').textContent=e.message;}
  finally{metadataBusy=false;renderControls();}
};
$('instruction-save').onclick=async()=>{
  if(metadataBusy||submitting||deleting)return;
  metadataBusy=true;renderControls();
  try{error('');const wasNew=!current;if(wasNew)await create($('prompt').value);await saveInstruction(!wasNew);}
  catch(e){error(e.message);}finally{metadataBusy=false;renderControls();}
};
$('history-search').oninput=()=>{
  listRevision++;clearTimeout(searchTimer);searchTimer=setTimeout(()=>list().catch(e=>error(e.message)),180);
};
$('favorite').onclick=async()=>{
  if(!detail||metadataBusy||submitting||deleting)return;
  metadataBusy=true;renderControls();
  try{error('');await updateConversation({is_favorite:!detail.conversation.is_favorite});}
  catch(e){error(e.message);}finally{metadataBusy=false;renderControls();}
};
$('rename').onclick=()=>{if(!detail)return;renameId=current;$('rename-input').value=detail.conversation.title;$('rename-error').textContent='';$('rename-dialog').showModal();$('rename-input').focus();};
$('rename-cancel').onclick=()=>$('rename-dialog').close();
$('rename-form').onsubmit=async event=>{
  event.preventDefault();if(metadataBusy)return;
  const title=$('rename-input').value.trim();
  if(!title||Array.from(title).length>100||/[\r\n]/.test(title)){$('rename-error').textContent='改行なしの1〜100文字で入力してください。';return;}
  metadataBusy=true;$('rename-save').disabled=true;renderControls();
  try{await updateConversation({title},renameId);$('rename-dialog').close();}
  catch(e){$('rename-error').textContent=e.message;}finally{metadataBusy=false;$('rename-save').disabled=false;renderControls();}
};
$('model').onchange=renderControls;
setInterval(async()=>{const id=active?.id;if(id&&owner(id)){try{await api('/runs/'+id+'/heartbeat',{method:'POST',headers:{'X-Run-Owner':owner(id)}});}catch(e){sessionStorage.removeItem('agweb-owner-'+id);renderControls();}}},3000);
setInterval(()=>refresh().catch(()=>error('アプリに接続できません。自動再送は行っていません。')),1500);
(async()=>{try{await refresh();await list();const saved=sessionStorage.getItem('last-conversation');if(saved)await select(saved,false,false);else{$('prompt').value=sessionStorage.getItem('draft-new')||'';loadInstruction();renderControls();}}catch(e){error(e.message);}})();
