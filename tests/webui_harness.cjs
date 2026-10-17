const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');

function page() {
  const downloads = [], blobs = new Map(), revoked = [], timers = [];
  let failClick = false;
  class Element {
    constructor(tag) { this.tag=tag;this.children=[];this.value='';this.options=[]; }
    append(...children) { for(const child of children){this.children.push(child);if(typeof child==='object')child.parent=this;} }
    replaceChildren(...children) { this.children=[];this.append(...children); }
    set innerHTML(value) { this.html=value;this.children=[]; }
    get innerHTML() { return this.html; }
    setAttribute(key,value) { this[key]=value; }
    focus() {}
    showModal() { this.open=true; }
    close() { this.open=false; }
    querySelectorAll(tag) { return this.children.flatMap(c=>typeof c==='object'?[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]:[]); }
    querySelector(tag) { return this.querySelectorAll(tag)[0]||null; }
    remove() { if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this); }
    click() {
      if(this.disabled)return;
      if(this.tag==='a'){
        if(failClick)throw new Error('download blocked');
        downloads.push({name:this.download,blob:blobs.get(this.href)});
      } else return this.onclick?.();
    }
  }
  const elements = new Map([...fs.readFileSync(path.join(root,'static/index.html'),'utf8').matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element('section')]));
  elements.get('model').value='mock-model';
  elements.get('output-mode').value='chat';
  const storage = new Map();
  const context = vm.createContext({
    Blob,FormData,AbortController,TextDecoder,TextEncoder,confirm:()=>true,
    navigator:{clipboard:{writeText:async()=>{}}},
    document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),createTextNode:text=>text,body:new Element('body')},
    location:{hash:'',pathname:'/'},history:{replaceState(){}},
    sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    marked:{parse:text=>text},DOMPurify:{sanitize:html=>html},
    URL:{createObjectURL(blob){const url='blob:'+blobs.size;blobs.set(url,blob);return url;},revokeObjectURL:url=>revoked.push(url)},
    fetch:()=>new Promise(()=>{}),setInterval(){},setTimeout:fn=>timers.push(fn),clearTimeout(){},
  });
  const evaluate=source=>vm.runInContext(source,context);
  evaluate(fs.readFileSync(path.join(root,'static/reading-export.js'),'utf8'));
  evaluate(fs.readFileSync(path.join(root,'static/app.js'),'utf8'));
  evaluate("status={acceptance:{ready:true},models:['mock-model'],busy:false};");
  return {
    elements,downloads,revoked,timers,context,evaluate,
    load(value){context.fixture=value;evaluate('detail=fixture;current=detail.conversation.id;renderMessages();renderControls();');},
    answers:()=>elements.get('messages').querySelectorAll('button').filter(b=>b.textContent==='回答を.mdで保存'),
    htmlAnswers:()=>elements.get('messages').querySelectorAll('button').filter(b=>b.textContent==='回答を.htmlで保存'),
    failDownload(){failClick=true;},
  };
}
function fixture(states=['completed'], responses=states.map(()=> '回答本文')) {
  return {
    conversation:{id:'conversation-1',title:'日本語の会話 📄',model:'mock-model',effort:'low',resumable:1,revision:states.length,system_prompt:'',is_favorite:false,instruction_locked:states.length>0},
    runs:states.map((state,i)=>({id:'run-'+(i+1),conversation_id:'conversation-1',state,response:responses[i],body:{text:'質問'+(i+1),attachment_ids:[]}})),
    attachments:[],
  };
}

module.exports={page,fixture};
