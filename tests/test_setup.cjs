const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {page}=require('./webui_harness.cjs');

function setupPage(){
  const p=page();
  // Minimal DOM behavior used by the additional setup controls.
  p.evaluate(`const originalCreate=document.createElement;
    document.createElement=tag=>{const e=originalCreate(tag);e.dataset={};return e;};`);
  p.evaluate(fs.readFileSync(path.join(__dirname,'../static/setup.js'),'utf8'));
  return p;
}

test('opening setup only reads configuration and resets consent',async()=>{
  const p=setupPage();const calls=[];
  p.context.fetch=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>({cli_path:'missing.exe',sources:[],issues:['CLI missing'],findings:[],environment_names:['PROXY'],quota_folder:'C:/quota'})};};
  await p.elements.get('setup-open').click();
  assert.deepEqual(calls.map(c=>c[0]),['/api/setup']);
  assert.equal(p.elements.get('setup-chat').checked,false);
  assert.equal(p.elements.get('setup-save').disabled,true);
  assert.match(p.elements.get('setup-info').textContent,/CLI missing/);
  assert.equal(p.elements.get('setup-dialog').open,true);
});

test('connection then explicit model and consent are required; changing review invalidates check',async()=>{
  const p=setupPage();const calls=[];
  p.context.fetch=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>({ok:true,fingerprint:'a'.repeat(64),version:'1.3.6',models:['example-medium']})};};
  p.elements.get('setup-auth').value='account';p.elements.get('setup-endpoint').value='official';
  for(const id of ['setup-proxy','setup-loading','setup-customizations'])p.elements.get(id).checked=true;
  await p.elements.get('setup-check').click();
  assert.equal(p.elements.get('setup-save').disabled,false);
  await p.elements.get('setup-save').click();
  assert.equal(calls.length,1);
  const row=p.elements.get('setup-models').children[0];row.querySelector('input').checked=true;
  await p.elements.get('setup-save').click();assert.equal(calls.length,1);
  p.elements.get('setup-chat').checked=true;
  p.evaluate('refresh=async()=>{}');
  await p.elements.get('setup-save').click();
  assert.equal(calls[1][0],'/api/setup');
  const body=JSON.parse(calls[1][1].body);
  assert.deepEqual(body.allowed_pairs,[{model:'example-medium',effort:'medium'}]);
  assert.equal(body.chat_consent,true);
  assert.equal(p.elements.get('setup-save').disabled,true);
  await p.elements.get('setup-check').click();
  p.elements.get('setup-auth').oninput();
  assert.equal(p.elements.get('setup-save').disabled,true);
});
