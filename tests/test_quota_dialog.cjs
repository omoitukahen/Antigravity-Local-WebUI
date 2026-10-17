const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,fixture}=require('./webui_harness.cjs');

test('quota button displays the Argon text returned by the API without changing the conversation',async()=>{
  const p=page();p.load(fixture());
  const dialog=p.elements.get('quota-dialog');
  dialog.showModal=()=>{dialog.open=true;};
  dialog.close=()=>{dialog.open=false;};
  const text='Models & Quota\n\nGEMINI 4 ARGON\n  Gemini 4 Argon Weekly Limit\n    [████░] 98.38%\n    Refreshes in 167h 25m';
  const calls=[];
  p.context.fetch=async(url,opts={})=>{
    calls.push([url,opts.method||'GET']);
    const data=url==='/api/quota/refresh'?{ok:true,text,fetched_at:'2026-10-17T00:00:00Z'}:
      {acceptance:{ready:true,reason:'mock'},models:['mock-model'],busy:false,active:null};
    return {ok:true,json:async()=>data};
  };
  const before=p.evaluate('JSON.stringify(detail)');
  await p.elements.get('quota-open').click();
  assert.equal(dialog.open,true);
  assert.equal(p.elements.get('quota-text').textContent,text);
  assert.match(p.elements.get('quota-state').textContent,/取得日時:/);
  assert.equal(p.elements.get('quota-open').disabled,false);
  assert.equal(p.evaluate('JSON.stringify(detail)'),before);
  assert.deepEqual(calls,[['/api/quota/refresh','POST'],['/api/status','GET']]);
  p.elements.get('quota-close').click();
  assert.equal(dialog.open,false);
});
