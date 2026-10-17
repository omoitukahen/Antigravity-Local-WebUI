/* Explicit local setup: opening this dialog never launches the CLI. */
let setupConnection=null, setupDetails=null;
function setupReview(){
  return {auth_method:$('setup-auth').value.trim(),endpoint:$('setup-endpoint').value.trim(),
    proxy_reviewed:$('setup-proxy').checked,loading_sources_verified:$('setup-loading').checked,
    no_external_customizations:$('setup-customizations').checked};
}
function setupChanged(){setupConnection=null;$('setup-save').disabled=true;}
function setupControls(){
  for(const id of ['setup-close','setup-check','setup-save','setup-auth','setup-endpoint','setup-proxy','setup-loading','setup-customizations','setup-chat','setup-text']){
    $(id).disabled=setupBusy||(id==='setup-save'&&!setupConnection);
  }
}
function setupModelRows(models){
  $('setup-models').replaceChildren();
  for(const model of models){
    const row=document.createElement('label');row.className='setup-model';
    const check=document.createElement('input');check.type='checkbox';check.dataset.model=model;
    const text=document.createElement('span');text.textContent=model;
    const effort=document.createElement('select');effort.ariaLabel=model+' の考える量';
    for(const [value,label] of [['','選択'],['low','低'],['medium','中'],['high','高']]){
      const option=document.createElement('option');option.value=value;option.textContent=label;effort.append(option);
    }
    const previous=setupDetails?.allowed_pairs?.find(p=>p.model===model);
    check.checked=Boolean(previous);effort.value=previous?.effort||model.match(/-(low|medium|high)$/)?.[1]||'';
    row.append(check,text,effort);$('setup-models').append(row);
  }
}
$('setup-open').onclick=async()=>{
  if(setupBusy||status?.busy)return;
  setupChanged();$('setup-state').textContent='構成を読み込んでいます…';$('setup-dialog').showModal();
  setupBusy=true;setupControls();renderControls();
  try{
    setupDetails=await api('/setup');
    $('setup-info').textContent=['CLI: '+setupDetails.cli_path,...(setupDetails.issues||[]),
      ...(setupDetails.findings||[]).map(f=>f.path+'\n'+f.reason)].join('\n');
    $('setup-sources').textContent=[...(setupDetails.sources||[]).map(s=>s.path+' : '+(s.sha256||'未存在')),
      '環境変数名: '+(setupDetails.environment_names||[]).join(', ')].join('\n');
    $('setup-auth').value=setupDetails.review?.auth_method||'';$('setup-endpoint').value=setupDetails.review?.endpoint||'';
    for(const id of ['setup-proxy','setup-loading','setup-customizations','setup-chat','setup-text'])$(id).checked=false;
    $('setup-models').replaceChildren();$('setup-version').textContent='';
    // Display as text, never execute or interpolate this path into a shell.
    $('setup-quota').textContent=setupDetails.quota_folder||'CLIと関連設定を確認してください。';
    $('setup-state').textContent=status?.acceptance.mode==='mock'?'模擬モードでは設定を保存しません。通常モードで確認してください。':'読込元を確認し、確認欄を入力してください。';
  }catch(e){$('setup-state').textContent=e.message;}
  finally{setupBusy=false;setupControls();renderControls();}
};
$('setup-close').onclick=()=>{if(!setupBusy)$('setup-dialog').close();};
$('setup-dialog').oncancel=event=>{if(setupBusy)event.preventDefault();};
for(const id of ['setup-auth','setup-endpoint'])$(id).oninput=setupChanged;
for(const id of ['setup-proxy','setup-loading','setup-customizations'])$(id).onchange=setupChanged;
$('setup-check').onclick=async()=>{
  if(setupBusy||status?.busy)return;
  setupChanged();setupBusy=true;setupControls();renderControls();$('setup-state').textContent='CLI情報とモデル一覧を確認しています… 自動再試行はしません。';
  try{
    const result=await api('/setup/check',{method:'POST',body:JSON.stringify({review:setupReview()})});
    if(!result.ok){$('setup-models').replaceChildren();throw new Error([...(result.issues||[]),...(result.findings||[]).map(f=>f.path+' : '+f.reason)].join('\n')||'接続を確認できません。');}
    setupConnection=result;setupModelRows(result.models);
    $('setup-version').textContent='CLI '+result.version+'（既存検証の基準版: 1.3.0。他版は未検証。接続確認は実モデル試験ではありません）';
    $('setup-state').textContent='利用するモデル・考える量を選び、利用に同意してください。';
  }catch(e){$('setup-state').textContent=e.message;}
  finally{setupBusy=false;setupControls();renderControls();}
};
$('setup-save').onclick=async()=>{
  if(setupBusy||!setupConnection||status?.busy)return;
  const pairs=[];
  for(const row of $('setup-models').children){
    const check=row.querySelector('input'),effort=row.querySelector('select');
    if(check.checked)pairs.push({model:check.dataset.model,effort:effort.value});
  }
  if(!pairs.length||pairs.some(p=>!p.effort)){$('setup-state').textContent='モデルと考える量を選択してください。';return;}
  if(!$('setup-chat').checked){$('setup-state').textContent='通常回答の利用への同意が必要です。';return;}
  setupBusy=true;setupControls();renderControls();
  try{
    await api('/setup',{method:'PUT',body:JSON.stringify({fingerprint:setupConnection.fingerprint,review:setupReview(),
      allowed_pairs:pairs,chat_consent:true,text_output_consent:$('setup-text').checked})});
    setupChanged();await refresh();$('setup-state').textContent='この構成で有効化しました。テキスト出力は保存先も登録してください。';
  }catch(e){$('setup-state').textContent=e.message;}
  finally{setupBusy=false;setupControls();renderControls();}
};
