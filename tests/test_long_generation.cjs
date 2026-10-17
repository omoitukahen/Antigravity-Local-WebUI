const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,fixture}=require('./webui_harness.cjs');

for(const length of [300,3000,10000,30000]){
  test(`Japanese streaming ${length} chars throttles rendering and preserves final text`,async()=>{
    const p=page(),value=fixture(['running'],['']);p.load(value);
    const text='日本語の物語。\n'.repeat(4000).slice(0,length);
    const final=structuredClone(value);final.runs[0].response=text+'完';final.runs[0].state='completed';
    const events=[{kind:'keepalive'},...Array.from(text,ch=>({kind:'delta',text:ch})),
      {kind:'result',response:text+'完'},{kind:'terminal',state:'completed',response:text+'完'}];
    let headers;
    p.context.fetch=async(url,options)=>{
      if(url.includes('/events')){headers=options.headers;return new Response(events.map(e=>JSON.stringify(e)).join('\n')+'\n');}
      return {ok:true,json:async()=>url==='/api/status'?{acceptance:{ready:true},models:['mock-model'],busy:false}:url==='/api/conversations'?[]:final};
    };
    p.evaluate("active={id:'run-1',conversation_id:current};sessionStorage.setItem('agweb-owner-run-1','owner-secret');let renderCount=0;const originalRender=renderMessages;renderMessages=()=>{renderCount++;originalRender();};");
    await p.evaluate("watch('run-1')");
    assert.equal(headers['X-Run-Owner'],'owner-secret');
    assert.equal(p.evaluate('detail.runs[0].response'),text+'完');
    assert.ok(p.evaluate('renderCount')<10);
    assert.equal(p.timers.length,1,'only one pending delta repaint for a burst');
    p.timers.forEach(fn=>fn());
    assert.equal(p.evaluate('detail.runs[0].response'),text+'完');
  });
}

test('partial Japanese text is painted after throttled tick and retained on cancellation',async()=>{
  const p=page(),value=fixture(['running'],['']);p.load(value);
  let resolveEnd;
  const end=new Promise(resolve=>resolveEnd=resolve);
  let reads=0;
  p.context.fetch=async url=>{
    if(url.includes('/events'))return {ok:true,body:{getReader:()=>({read:async()=>{
      if(reads++===0)return {value:new TextEncoder().encode('{"kind":"delta","text":"途中の日本語"}\n'),done:false};
      await end;throw new Error('network disconnected');
    }})}};
    return {ok:true,json:async()=>value};
  };
  p.evaluate("active={id:'run-1',conversation_id:current};let renders=0;const originalRender=renderMessages;renderMessages=()=>{renders++;originalRender();};");
  const watching=p.evaluate("watch('run-1')");
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(p.evaluate('renders'),0);
  p.timers[0]();
  assert.equal(p.evaluate('renders'),1);
  assert.equal(p.evaluate('detail.runs[0].response'),'途中の日本語');
  resolveEnd();await watching;
  assert.equal(p.evaluate('detail.runs[0].response'),'途中の日本語');
});
