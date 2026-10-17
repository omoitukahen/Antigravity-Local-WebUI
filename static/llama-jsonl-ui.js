'use strict';
(()=>{
  const $=id=>document.getElementById(id);
  let revision=0,conversation=null,filename='',output='';
  const error=message=>{$('error').textContent=message;$('error').hidden=!message;};
  function clear(){
    conversation=null;output='';filename='';
    $('save').disabled=true;$('preview').srcdoc='';$('preview').hidden=true;$('summary').textContent='';
  }
  function render(){
    output='';$('save').disabled=true;$('preview').srcdoc='';$('preview').hidden=true;error('');
    if(!conversation)return;
    try{
      output=readingHtml(conversation.title,LlamaJsonl.markdown(conversation,$('mode').value));
      $('preview').srcdoc=output;$('preview').hidden=false;$('save').disabled=false;
    }catch(e){error(e.message);}
  }
  $('save').disabled=true;
  $('jsonl').onchange=async()=>{
    const current=++revision,file=$('jsonl').files[0];
    clear();error('');if(!file)return;
    try{
      if(file.size>16*1024*1024)throw new Error('16MiB以下のJSONLを選んでください。');
      $('summary').textContent='読み込み中…';
      const bytes=await file.arrayBuffer();
      if(current!==revision)return;
      let text;
      try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}
      catch{throw new Error('UTF-8のJSONLを選んでください。文字コードを読み取れません。');}
      conversation=LlamaJsonl.parse(text);
      filename=Array.from(file.name.replace(/\.jsonl$/i,'').replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_')).slice(0,120).join('').replace(/[. ]+$/g,'')||'conversation';
      const messages=conversation.messages;
      $('summary').textContent=conversation.title+' — 質問 '+messages.filter(m=>m.role==='user').length+' / 回答 '+messages.filter(m=>m.role==='assistant').length;
      render();
    }catch(e){if(current===revision){clear();error(e.message);}}
  };
  $('mode').onchange=render;
  $('save').onclick=()=>{
    if(!output)return;
    let url,link;
    try{
      error('');url=URL.createObjectURL(new Blob([output],{type:'text/html;charset=utf-8'}));
      link=document.createElement('a');link.href=url;link.download='llama-'+$('mode').value+'-'+filename+'.html';
      document.body.append(link);link.click();
    }catch(e){error('HTMLを保存できません。'+e.message);}
    finally{link?.remove();if(url)setTimeout(()=>URL.revokeObjectURL(url),1000);}
  };
})();
