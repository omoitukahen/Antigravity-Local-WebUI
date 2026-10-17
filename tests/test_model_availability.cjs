const {test}=require('node:test');
const assert=require('node:assert/strict');
const {page,fixture}=require('./webui_harness.cjs');

function operational(p){
  p.evaluate(`status={acceptance:{ready:true,operational:true,models:['mock-model'],
    tested_pairs:[{model:'retired-model',effort:'low'}],
    allowed_pairs:[{model:'mock-model',effort:'low'}]},models:['mock-model'],busy:false};`);
  p.elements.get('prompt').value='相談';
  p.elements.get('effort').value='low';
}

test('available approved model remains sendable when another model was removed',()=>{
  const p=page();operational(p);p.load(fixture());
  assert.equal(p.elements.get('send').disabled,false);
});

test('retired model conversation remains readable and explains disabled sending',()=>{
  const p=page();operational(p);
  const old=fixture();old.conversation.model='retired-model';
  p.elements.get('model').value='retired-model';p.load(old);
  assert.equal(p.elements.get('send').disabled,true);
  assert.match(p.elements.get('model-hint').textContent,/利用できません/);
  assert.equal(p.answers().length,1);
  assert.equal(p.elements.get('new').disabled,false);
});
