'use strict';
globalThis.LlamaJsonl=(()=>{
  const isObject=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const hasId=value=>typeof value==='string'&&Boolean(value.trim());
  function parse(text){
    if(!text.trim())throw new Error('JSONLが空です。');
    let session=null;
    const nodes=new Map();
    text.replace(/^\ufeff/,'').split(/\r?\n/).forEach((line,index)=>{
      if(!line.trim())return;
      const fail=message=>{throw new Error((index+1)+'行目: '+message);};
      let row;
      try{row=JSON.parse(line);}catch{fail('JSONを読み取れません。');}
      if(!isObject(row))fail('sessionまたはmessageレコードが必要です。');
      if(row.type==='session'){
        if(session)fail('複数のsessionには対応していません。1会話ずつ変換してください。');
        if(row.harness!=='llama.app')fail('llama.appから出力したJSONLを選んでください。');
        if(!hasId(row.id)||!hasId(row.currNode))fail('sessionのidとcurrNodeが必要です。');
        if(row.name!==undefined&&typeof row.name!=='string')fail('sessionのnameは文字列である必要があります。');
        session=row;
      }else if(row.type==='message'){
        const m=row.message;
        if(!isObject(m)||!hasId(m.id))fail('messageのidが必要です。');
        if(nodes.has(m.id))fail('messageのidが重複しています。');
        nodes.set(m.id,m);
      }else fail('対応していないレコードtypeです。');
    });
    if(!session)throw new Error('sessionレコードが見つかりません。');
    if(!nodes.has(session.currNode))throw new Error('currNodeのメッセージが見つかりません。');
    const seen=new Set(),messages=[];
    let id=session.currNode;
    while(id!==null){
      if(seen.has(id))throw new Error('会話の親メッセージが循環しています。');
      seen.add(id);
      const m=nodes.get(id);
      if(!m)throw new Error('親メッセージが見つかりません: '+id);
      if(m.convId!==session.id)throw new Error('messageのconvIdがsessionと一致しません。');
      if(m.parent!==null&&!hasId(m.parent))throw new Error('messageの親idが不正です。');
      if(typeof m.content!=='string')throw new Error('文字列以外の本文には対応していません。');
      if(m.type==='root'){
        if(m.role!=='system'||m.parent!==null)throw new Error('rootのroleまたは親が不正です。');
      }else{
        if(m.type!=='text')throw new Error('対応していないメッセージtypeです: '+m.type);
        if(!['user','assistant','system'].includes(m.role))throw new Error('対応していないroleです: '+m.role);
        if(m.parent===null)throw new Error('会話の先頭にrootメッセージが必要です。');
        if(m.model!==undefined&&typeof m.model!=='string')throw new Error('modelは文字列である必要があります。');
        if(m.role!=='system')messages.push(m);
      }
      id=m.parent;
    }
    messages.reverse();
    if(!messages.length)throw new Error('出力できる質問・回答がありません。');
    return {title:session.name?.trim()||'llama.cppの会話',messages};
  }
  const literal=text=>text.replace(/[\r\n]+/g,' ').replace(/[\\`*_{}\[\]()<>#+.!|~-]/g,'\\$&');
  function markdown(value,mode){
    if(mode==='answers'){
      const answers=value.messages.filter(m=>m.role==='assistant'&&m.content.trim());
      if(!answers.length)throw new Error('保存できる回答本文がありません。「会話全体」を選んでください。');
      return answers.length===1?answers[0].content:answers.map((m,i)=>'## 回答 '+(i+1)+'\n\n'+m.content).join('\n\n---\n\n');
    }
    if(mode!=='conversation')throw new Error('出力形式が不正です。');
    const parts=['# '+literal(value.title)];
    const models=[...new Set(value.messages.filter(m=>m.role==='assistant'&&m.model).map(m=>m.model))];
    if(models.length)parts.push('モデル: '+models.map(literal).join(' / '));
    let questions=0,answers=0;
    for(const m of value.messages){
      const heading=m.role==='user'?'質問 '+(++questions):'回答 '+(++answers);
      parts.push('## '+heading,m.content.trim()?m.content:(m.role==='user'?'質問本文なし':'回答本文なし'));
    }
    return parts.join('\n\n')+'\n';
  }
  return {parse,markdown};
})();
