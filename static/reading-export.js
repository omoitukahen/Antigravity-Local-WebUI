'use strict';
function safeMarkdown(text){
  const html=marked.parse(text||'',{breaks:true});
  return DOMPurify.sanitize(html,{FORBID_TAGS:['img','script','style','iframe','form','input','button','select','option','textarea','fieldset','video','audio','svg','math'],FORBID_ATTR:['style']});
}
function readingHtml(title,text){
  const escapedTitle=title.replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  return `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<title>${escapedTitle}</title>
<style>
:root{color-scheme:light;font-family:system-ui,-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif;color:#202936;background:#fff;font-size:17px;line-height:1.85}
*{box-sizing:border-box}
body{margin:0;overflow-wrap:anywhere}
.reader{max-width:52rem;margin:0 auto;padding:clamp(20px,5vw,64px)}
.reader>:first-child{margin-top:0}
.reader>:last-child{margin-bottom:0}
h1,h2,h3,h4,h5,h6{line-height:1.45;letter-spacing:.01em;margin:1.8em 0 .65em;color:#15253b;break-after:avoid}
h1{font-size:1.85em}h2{font-size:1.4em;border-bottom:1px solid #dce3eb;padding-bottom:.35em}h3{font-size:1.15em}
p,ul,ol,dl,blockquote,pre,table{margin:1em 0}
ul,ol{padding-left:1.6em}li+li{margin-top:.3em}li>p{margin:.4em 0}
a{color:#145eb1;text-decoration:underline;text-underline-offset:.15em}
blockquote{margin-left:0;margin-right:0;border-left:4px solid #b6c9e0;padding:.2em 1em;color:#4a5b70;background:#f6f8fb}
code,kbd,samp{font-family:ui-monospace,Consolas,"SFMono-Regular",monospace;font-size:.88em;background:#f0f3f7;border-radius:4px;padding:.15em .35em}
pre{max-width:100%;overflow-x:auto;white-space:pre;line-height:1.65;padding:1em;border:1px solid #dce3eb;border-radius:8px;background:#f6f8fb;tab-size:4}
pre code{background:transparent;padding:0;border-radius:0}
table{display:block;width:max-content;max-width:100%;overflow-x:auto;border-collapse:collapse;font-size:.94em}
th,td{min-width:6em;border:1px solid #d4dde7;padding:.55em .8em;text-align:left;vertical-align:top}
th{background:#edf2f8;font-weight:650}tbody tr:nth-child(even){background:#f8fafc}
hr{border:0;border-top:1px solid #dce3eb;margin:2em 0}
@media(max-width:480px){:root{font-size:16px}h1{font-size:1.6em}h2{font-size:1.3em}}
@media print{
  @page{margin:18mm}
  :root{font-size:11pt;color:#000;background:#fff}
  .reader{max-width:none;padding:0}
  pre{white-space:pre-wrap;overflow-wrap:anywhere;overflow:visible}
  table{display:table;width:100%;font-size:.9em}
  th,td{min-width:0;overflow-wrap:anywhere}tr{break-inside:avoid}
}
</style>
</head>
<body><main class="reader">${safeMarkdown(text)}</main></body>
</html>
`;
}
