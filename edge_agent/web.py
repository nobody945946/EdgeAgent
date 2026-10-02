from __future__ import annotations

NATIVE_CHAT_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
<title>EdgeAgent — 本機編程 Agent</title>
<style>
:root{color-scheme:dark;font-family:system-ui,"Microsoft JhengHei",sans-serif;background:#181818;color:#ececec;font-size:14px;--app-height:100vh;--app-top:0px;--sidebar-width:clamp(200px,18vw,250px);--header-height:58px}
@supports(height:100dvh){:root{--app-height:100dvh}}
*{box-sizing:border-box}
body{margin:0;position:fixed;top:var(--app-top);inset-inline:0;height:var(--app-height);overflow:hidden}
button,summary,select{touch-action:manipulation}
button{border:0;border-radius:9px;background:#333;color:#eee;padding:9px 13px;font:inherit;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
button:focus-visible,summary:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid #aaa;outline-offset:3px}
.secondary{background:#303030}.danger{background:#333}
main{height:100%;margin-left:var(--sidebar-width);display:flex;flex-direction:column;min-width:0;overflow:hidden}
header{flex:none;min-height:58px;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px max(12px,env(safe-area-inset-right)) 8px max(12px,env(safe-area-inset-left));border-bottom:1px solid #2a2a2a}
.heading{min-width:0;flex:1}h1{font-size:14px;font-weight:500;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sub{font-size:11px;color:#999;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.actions{display:flex;align-items:center;gap:6px;flex:none}.actions>button{font-size:12px;white-space:nowrap}
#actions-menu{position:relative}#actions-menu>summary{list-style:none;cursor:pointer;display:grid;place-items:center;width:40px;height:40px;border-radius:9px;background:#303030;font-size:22px}#actions-menu>summary::-webkit-details-marker{display:none}
.action-menu{position:absolute;z-index:35;right:0;top:calc(100% + 6px);width:min(220px,calc(100vw - 24px));max-height:calc(var(--app-height) - var(--header-height) - 16px);overflow:auto;padding:6px;background:#252525;border:1px solid #444;border-radius:12px;box-shadow:0 8px 28px #0008;display:grid;gap:4px}
.action-menu button{text-align:left;min-height:40px}
#nav-toggle,#nav-close{display:none}
#sidebar{position:absolute;inset:0 auto 0 0;width:var(--sidebar-width);background:#202020;border-right:1px solid #303030;padding:16px 14px max(12px,env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:10px;z-index:50;min-height:0}
.sidebar-heading{display:flex;align-items:center;justify-content:space-between;flex:none}#sidebar h2{font-size:15px;margin:0 8px 12px}
.nav-title{font-size:11px;color:#929292;margin:14px 10px 4px;letter-spacing:1px;flex:none}
#threads{display:flex;flex-direction:column;overflow:auto;min-height:0;gap:3px;flex:1}
.thread{flex:none;background:transparent;color:#c7c7c7;padding:11px 12px;text-align:left;border-radius:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.thread.active,.thread:hover{background:#343434;color:white}
#privacy{color:#888;font-size:11px;padding:8px 12px;line-height:1.7;flex:none}
#nav-backdrop{position:absolute;inset:0;z-index:40;background:#0009;border-radius:0}
[hidden]{display:none!important}
#chat{order:1;flex:1;min-height:0;min-width:0;overflow:auto;overscroll-behavior:contain;display:flex;flex-direction:column;padding:24px max(16px,calc((100% - 820px)/2));gap:20px}
.msg{white-space:pre-wrap;line-height:1.65;padding:4px 0;overflow-wrap:anywhere;min-width:0;flex:none}
.user{align-self:flex-end;max-width:90%;background:#303030;padding:12px 18px;border-radius:18px}
.assistant{width:100%}.assistant pre{max-width:100%;background:#252525;border:1px solid #383838;padding:15px;border-radius:12px;overflow:auto;white-space:pre}
.assistant code{font:12px/1.7 Consolas,monospace}.assistant h2{font-size:20px}.assistant h3{font-size:17px}
.notice{align-self:center;color:#999;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;flex:none}
#welcome{text-align:center;margin:auto 0;color:#999;flex:none}#welcome h2{font-size:clamp(20px,4vw,28px);color:#ececec;font-weight:500}
#form{order:3;flex:none;display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;gap:6px;margin:8px auto 0;width:min(820px,calc(100% - 24px));background:#262626;border:1px solid #3b3b3b;border-radius:18px;padding:8px;align-items:end;min-width:0}
textarea{width:100%;min-width:0;resize:none;min-height:44px;max-height:min(180px,25vh);border:0;background:transparent;color:inherit;padding:6px;font:inherit;overflow:auto}
#send{height:38px;width:38px;padding:0;border-radius:50%;background:#ddd;color:#171717;font-size:20px}#stop{min-height:38px;background:#444;padding:0 10px}
.controls{order:4;flex:none;margin:0 auto;width:min(820px,calc(100% - 24px));display:grid;gap:8px;align-items:center;padding:8px 0 max(10px,env(safe-area-inset-bottom));grid-template-columns:auto auto minmax(0,1fr);min-width:0}
.control{display:grid;gap:5px;font-size:12px;color:#aeb9c5}.control>span:not(.control-title),.control-title>span{display:none}
.control-title{display:flex;justify-content:flex-end;flex:none}
.agent-controls{display:flex;align-items:center;gap:8px;min-width:0}.toggle{display:flex;align-items:center;gap:6px;font-size:12px;white-space:nowrap}.toggle input{width:17px;height:17px;flex:none}
select{min-height:32px;min-width:0;border:0;border-radius:7px;background:#252525;color:inherit;padding:0 8px;font:inherit}
.cwd{min-width:0;display:flex;align-items:center;gap:6px;flex-direction:row-reverse}.cwd input{width:100%;min-width:0;height:32px;background:transparent;border:0;color:#999;font-size:11px;text-overflow:ellipsis}
.mini{padding:5px 9px;white-space:nowrap;font-size:12px}
.tool-card,.approval-card{border:1px solid #383838;background:#212121;border-radius:12px;padding:12px;width:100%;font-size:12px;min-width:0;flex:none}
.tool-card summary{cursor:pointer;color:#bbb;overflow-wrap:anywhere}.tool-card pre,.approval-card pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;color:#bbb}
.approval-card button{margin:8px 8px 0 0;padding:8px 14px;background:#eee;color:#222}.approval-card .secondary{background:#444;color:#ddd}
#attachments{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px;max-height:min(100px,18vh);overflow:auto}#attachments:empty{display:none}
.attachment{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;border:1px solid #555;padding:5px 9px}
#attach{width:38px;height:38px;padding:0;background:transparent;font-size:22px}
#terminal-pane{order:2;flex:0 1 clamp(96px,28vh,240px);min-height:0;background:#181818;border-block:1px solid #3b3b3b;display:grid;grid-template-rows:auto minmax(0,1fr) auto;padding:6px 12px;gap:4px;min-width:0}
.terminal-head{display:flex;align-items:center;gap:4px;font-size:11px;min-width:0}.terminal-head strong{margin-right:6px}.terminal-head span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#999}.terminal-head button{font-size:11px;padding:4px 8px}
#terminal-output{margin:0;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 Consolas,monospace}
#terminal-form{display:flex;gap:6px;margin:0;min-width:0}#terminal-command{flex:1;min-width:0;border:1px solid #444;background:#222;color:inherit;border-radius:6px;padding:7px;font:12px Consolas,monospace}#terminal-run{font-size:11px;padding:6px 12px}
#folder-dialog{position:fixed;top:calc(var(--app-top) + var(--app-height)/2);bottom:auto;inset-inline:0;transform:translateY(-50%);margin:0 auto;width:min(680px,calc(100vw - 24px));max-height:calc(var(--app-height) - 24px);border:1px solid #444;border-radius:16px;background:#202020;color:inherit;padding:0;overflow:hidden}
#folder-dialog[open]{display:flex;flex-direction:column}dialog::backdrop{background:#000a}
.folder-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border-bottom:1px solid #3b3b3b;flex:none}
#folder-address-form{display:flex;padding:10px 12px;gap:6px;flex:none}#folder-address{flex:1;min-width:0;padding:8px;background:#292929;color:inherit;border:1px solid #444;border-radius:7px}
.folder-path{padding:6px 12px;color:#aeb9c5;overflow-wrap:anywhere;flex:none;max-height:48px;overflow:auto}
#folder-error{margin:0;padding:0 12px 6px;color:#ed9999;max-height:48px;overflow:auto;flex:none}
.folder-list{display:flex;flex-direction:column;gap:5px;overflow:auto;overscroll-behavior:contain;flex:1;min-height:0;padding:0 10px 8px}
.folder-item{flex:none;background:#303030;text-align:left;min-height:40px;padding:8px 12px;overflow-wrap:anywhere}
.folder-actions{display:flex;justify-content:flex-end;gap:8px;padding:10px 12px;border-top:1px solid #3b3b3b;flex:none}
#review{position:absolute;right:0;top:var(--header-height);bottom:0;width:min(480px,100%);padding:16px max(16px,env(safe-area-inset-right)) max(16px,env(safe-area-inset-bottom));background:#202020;border-left:1px solid #444;overflow:auto;z-index:30}
#review h2{font-size:16px}#review pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 monospace}#review-close{float:right;position:sticky;top:0}
#review details{border-bottom:1px solid #444;padding:14px 0}#review summary{overflow-wrap:anywhere;cursor:pointer}
.diff-add{color:#8bd49b}.diff-remove{color:#ed9999}
@media(max-width:1100px){.controls{grid-template-columns:auto minmax(0,1fr)}.cwd{grid-column:1/-1}.agent-controls{justify-content:flex-end}}
@media(max-width:760px){
  main{margin-left:0}#nav-toggle,#nav-close{display:grid;place-items:center;width:44px;height:44px;padding:0;flex:none}
  #sidebar{width:min(300px,calc(100vw - 48px));transform:translateX(-100%);visibility:hidden;padding-left:max(14px,env(safe-area-inset-left))}
  body.nav-open #sidebar{transform:none;visibility:visible}.sidebar-heading h2{margin:0!important}
  header .sub{display:none}.actions{gap:4px}.actions>button{padding:8px;font-size:12px}
  #chat{padding:16px 12px;gap:16px}.controls{gap:6px}.cwd input{font-size:12px}
  #review{width:100%;border-left:0}input,textarea,select,#terminal-command,#folder-address{font-size:16px}
  #sidebar>button,.thread,button,select,.tool-card summary,#review summary{min-height:44px}button{min-width:44px}
  #send,#attach,#actions-menu>summary{width:44px;height:44px}#stop{min-height:44px}
  .toggle{min-height:44px}.toggle input{width:20px;height:20px}.attachment{min-height:36px}
  .terminal-head strong{display:none}.terminal-head button{min-width:44px}
  .controls select{font-size:14px}
}
@media(pointer:coarse){button,select,summary{min-height:44px}button{min-width:44px}input,textarea,#folder-address,#terminal-command{font-size:16px}}
.compact-height header{min-height:48px;padding-block:4px}.compact-height #chat{padding-block:8px;gap:10px}
.compact-height .controls{grid-template-columns:auto minmax(0,1fr);padding-block:4px}.compact-height .cwd{display:none}
.compact-height #welcome p{display:none}.compact-height #welcome h2{margin:4px 0}
.compact-height #form{margin-top:4px}.compact-height #terminal-pane{flex-basis:24%}
.compact-height .folder-head,.compact-height #folder-address-form,.compact-height .folder-actions{padding:6px 10px}
.compact-height .folder-path,.compact-height #folder-error{max-height:32px}
@media(prefers-reduced-motion:no-preference){#sidebar{transition:transform .15s ease,visibility .15s}}
#models-dialog{width:min(700px,calc(100vw - 24px));max-height:calc(var(--app-height) - 24px);padding:0;color:#ececec;background:#222;border:1px solid #444;border-radius:14px;overflow:auto}
#models-dialog::backdrop{background:#0009}
.model-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;position:sticky;top:0;background:#222;z-index:1}
.model-body{padding:0 18px 18px;display:grid;gap:12px}.model-body label{display:grid;gap:6px}.model-body input,.model-body select{width:100%;min-width:0;padding:10px;background:#181818;color:#eee;border:1px solid #555;border-radius:8px;font:inherit}
.model-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.model-body .model-check{display:flex;align-items:center;gap:8px}.model-check input{width:auto}.model-body p{margin:0;overflow-wrap:anywhere;color:#bbb}.model-row{display:flex;gap:8px;align-items:center}.model-row input{flex:1}.model-row button{flex-shrink:0}#model-progress{width:100%}#model-result{white-space:pre-wrap;color:#ddd;overflow-wrap:anywhere}
@media(max-width:480px){.model-grid{grid-template-columns:1fr}.model-row{flex-wrap:wrap}.model-row input{flex-basis:100%}}
</style>
</head>
<body><aside id="sidebar" aria-label="專案與對話"><div class="sidebar-heading"><h2>EdgeAgent</h2><button id="nav-close" aria-label="關閉側欄">×</button></div><button id="new-thread" class="secondary">＋ 新對話</button><button id="open-project" class="secondary">開啟專案</button><div class="nav-title">專案與對話</div><div id="threads"></div><div id="privacy">本機工作 · 對話只在記憶體<br>關閉頁面後不保留對話</div></aside><button id="nav-backdrop" aria-label="關閉側欄" hidden></button><main>
<header><button id="nav-toggle" aria-label="開啟側欄" aria-controls="sidebar" aria-expanded="false">☰</button><div class="heading"><h1>EdgeAgent</h1><div class="sub">本機編程 Agent · 對話只留在記憶體</div></div><div class="actions"><details id="actions-menu"><summary aria-label="更多操作">⋯</summary><div class="action-menu"><button id="models-open" class="secondary">模型模組</button><button id="save" class="secondary">儲存設定</button><button id="reset" class="secondary">新對話</button><button id="shutdown" class="danger">關閉EdgeAgent</button></div></details></div></header>
<section class="controls">
  <label class="control"><span>Thinking effort</span><select id="effort" aria-label="Thinking effort"><option value="off">關閉</option><option value="low">低</option><option value="medium" selected>中</option><option value="high">高</option></select></label>
  <div class="agent-controls"><label class="toggle"><input id="agent" type="checkbox" checked>啟用 Agent</label></div>
  <div class="control cwd" id="cwd-wrap"><span class="control-title"><span>目前 Workspace</span><button id="browse" type="button" class="secondary mini">開啟資料夾</button></span><input id="cwd" type="text" readonly placeholder="尚未開啟資料夾"></div>
</section>
<section id="chat"><div id="welcome"><h2>今天要完成什麼？</h2><p>開啟專案，讓目前模型讀取、修改與驗證你的工作。</p></div><div class="notice"><span id="active-model-label">正在讀取模型狀態…</span>；Agent 的寫檔、編輯與 Shell 操作會先請你批准</div></section>
<section id="terminal-pane" hidden><div class="terminal-head"><strong>PowerShell</strong><span id="terminal-cwd"></span><button id="terminal-stop" type="button" disabled>停止</button><button id="terminal-clear" type="button">清除</button><button id="terminal-hide" type="button">收起</button></div><pre id="terminal-output" role="log" aria-live="polite"></pre><form id="terminal-form"><input id="terminal-command" aria-label="終端命令" autocomplete="off" placeholder="在專案中執行命令…"><button id="terminal-run">執行</button></form></section>
<form id="form"><div id="attachments"></div><input id="file-input" type="file" multiple hidden accept=".txt,.md,.py,.js,.ts,.tsx,.jsx,.json,.csv,.html,.css,.yaml,.yml,.toml,.xml,.log,.pdf,.docx"><button id="attach" type="button" aria-label="附加檔案">＋</button><textarea id="input" aria-label="工作指令" placeholder="描述工作，或提出問題…"></textarea><button id="stop" type="button" hidden>停止</button><button id="send" aria-label="送出">↑</button></form>
</main>
<dialog id="folder-dialog"><div class="folder-head"><strong>選擇 Agent 工作目錄</strong><button id="folder-close" class="secondary mini">關閉</button></div><form id="folder-address-form"><input id="folder-address" aria-label="資料夾路徑" placeholder="輸入資料夾路徑"><button>前往</button></form><div id="folder-path" class="folder-path"></div><p id="folder-error" role="alert" hidden></p><div id="folder-list" class="folder-list"></div><div class="folder-actions"><button id="folder-use" disabled>使用這個資料夾</button></div></dialog>
<aside id="review" aria-label="修改審閱" hidden><button id="review-close">關閉</button><h2>本次修改</h2><p id="review-empty">這個對話尚未修改檔案。</p><div id="review-files"></div></aside>
<dialog id="models-dialog" aria-label="模型模組">
<div class="model-head"><strong>模型模組</strong><button id="models-close" type="button">關閉</button></div>
<div class="model-body">
<p>同一套工具，切換回覆模型即可。聊天、Workspace、終端與修改審閱繼續留在這個頁面。</p>
<p id="model-active"></p>
<label>已下載的模型<select id="model-select" aria-label="已下載的模型"></select></label>
<p id="model-info"></p>
<label>LoRA（選用）<select id="model-lora" aria-label="相容 LoRA"><option value="">不使用</option></select></label>
<p>LoRA 是個人資產；框架不附帶或自動下載。自行安裝後重新掃描，並選擇訓練時使用的相同基底模型。</p>
<div class="model-grid"><label>Context（token）<input id="model-context" type="number" min="4096" max="262144" step="1024"></label><label>GPU 層數（0 使用 CPU）<input id="model-gpu" type="number" min="0" max="999"></label></div>
<label class="model-check"><input id="model-low-vram" type="checkbox">節省 VRAM</label>
<div class="model-row"><button id="model-switch" type="button">套用並切換</button><button id="model-refresh" type="button" class="secondary">重新掃描</button></div>
<hr style="width:100%;border:0;border-top:1px solid #444">
<label>下載其他模型<div class="model-row"><input id="model-repository" placeholder="Hugging Face 作者/模型庫" aria-label="Hugging Face 模型庫"><button id="model-catalog" type="button">列出檔案</button></div></label>
<label>GGUF 檔案<select id="model-remote" aria-label="可下載的 GGUF 檔案" disabled></select></label>
<button id="model-download" type="button" disabled>下載模型</button>
<p>支援現有 llama.cpp 可載入的單檔 GGUF。下載會驗證 SHA256；LoRA 和多模態配件不會列為回覆模型。工具呼叫格式不代表模型具有足夠的 Agent 能力。</p>
<progress id="model-progress" hidden></progress><div id="model-result" role="status" aria-live="polite"></div>
</div></dialog>
<script>
// Model controls use the same core and keep conversation/workspace state in RAM.
const modelDialog=document.querySelector('#models-dialog'),modelSelect=document.querySelector('#model-select'),modelResult=document.querySelector('#model-result');
let modelState=null,modelTimer=null,modelChanging=false,remoteRepository='',remoteRequest=0,modelRefreshRequest=0;
const modelBytes=value=>(Number(value)/1073741824).toFixed(2)+' GiB';
async function modelPost(path,payload){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error||'模型操作失敗');return data}
function adapterOptions(selected=''){const select=document.querySelector('#model-lora'),base=modelState?.models.find(item=>item.filename===modelSelect.value);select.replaceChildren();const none=document.createElement('option');none.value='';none.textContent='\u4e0d\u4f7f\u7528';select.append(none);for(const item of modelState?.models||[]){if(!item.adapter||item.split||item.error||item.architecture!==base?.architecture)continue;const option=document.createElement('option');option.value=item.filename;option.textContent=item.filename;select.append(option)}select.value=[...select.options].some(item=>item.value===selected)?selected:''}
function modelDetails(){const selected=modelState?.models.find(item=>item.filename===modelSelect.value);document.querySelector('#model-info').textContent=selected?selected.architecture+' · '+modelBytes(selected.bytes)+' · '+(selected.context_length?'模型上限 '+selected.context_length+' token':'Context 上限未提供')+' · '+(selected.tool_template?'內建工具呼叫格式':'未偵測到工具呼叫格式；Agent 相容性需實測'):'';if(selected?.context_length){const context=document.querySelector('#model-context');context.value=Math.min(Number(context.value)||32768,selected.context_length)}}
function modelControls(){const running=modelState?.job.state==='running';modelChanging=running&&modelState.job.action==='switch';for(const id of ['model-switch','model-refresh','model-catalog','model-download','model-select','model-lora','model-context','model-gpu','model-low-vram']){const node=document.querySelector('#'+id);node.disabled=running||(id==='model-switch'&&(!modelSelect.value||busy||modelState?.mode==='character'))||(id==='model-download'&&!document.querySelector('#model-remote').value)}send.disabled=modelChanging}
async function refreshModels(reset=false){const request=++modelRefreshRequest;if(reset)modelSelect.disabled=true;try{const response=await fetch('/models');if(!response.ok)throw Error('無法讀取模型清單');const data=await response.json();if(request!==modelRefreshRequest)return;const previous=modelSelect.value;modelState=data;modelSelect.replaceChildren();for(const item of data.models){if(item.adapter||item.split||item.error)continue;const option=document.createElement('option');option.value=item.filename;option.textContent=item.filename+' · '+modelBytes(item.bytes);modelSelect.append(option)}modelSelect.value=data.models.some(item=>item.filename===previous)?previous:data.config.model;if(!modelSelect.value&&modelSelect.options.length)modelSelect.selectedIndex=0;if(reset){document.querySelector('#model-context').value=data.config.context_size;document.querySelector('#model-gpu').value=data.config.gpu_layers;document.querySelector('#model-low-vram').checked=data.config.low_vram}const active=(data.active_model||'尚未載入模型')+(data.context_size?' · '+data.context_size+' token':'');document.querySelector('#model-active').textContent='目前：'+active;document.querySelector('#active-model-label')?.replaceChildren(document.createTextNode(active));adapterOptions(reset?data.config.lora:(document.querySelector('#model-lora').value||data.config.lora));modelDetails();const job=data.job,progress=document.querySelector('#model-progress');progress.hidden=job.state!=='running'||job.action!=='download';if(job.total){progress.max=job.total;progress.value=job.received}else progress.removeAttribute('value');if(job.state==='running'){modelResult.textContent=job.action==='switch'?'正在切換模型，完成後可繼續工作…':'正在下載 '+(job.filename||'模型')+' · '+modelBytes(job.received)+(job.total?' / '+modelBytes(job.total):'');clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),1000)}else{if(job.state==='done')modelResult.textContent=job.action==='switch'?'已切換模型，可在原對話繼續工作。':'下載完成，請選擇模型並套用。';if(job.state==='failed')modelResult.textContent=job.error;clearTimeout(modelTimer)}if(data.loading||(!data.active_model&&!data.error)){clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),1000)}if(data.error)modelResult.textContent=data.error;modelControls()}catch(error){if(request!==modelRefreshRequest)return;modelResult.textContent=error.message;modelChanging=false;send.disabled=false;clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),3000)}}
document.querySelector('#models-open').onclick=()=>{modelDialog.showModal();refreshModels(true)};
document.querySelector('#models-close').onclick=()=>modelDialog.close();
document.querySelector('#model-refresh').onclick=()=>refreshModels(true);
modelSelect.onchange=()=>{adapterOptions();modelDetails()};
document.querySelector('#model-switch').onclick=async()=>{if(busy)return;try{const context=Number(document.querySelector('#model-context').value),gpu=Number(document.querySelector('#model-gpu').value);if(!Number.isInteger(context)||context<4096||context>262144||!Number.isInteger(gpu)||gpu<0||gpu>999)throw Error('請輸入有效的 Context 和 GPU 層數');const job=await modelPost('/models/switch',{model:modelSelect.value,lora:document.querySelector('#model-lora').value,context_size:context,gpu_layers:gpu,low_vram:document.querySelector('#model-low-vram').checked});modelRefreshRequest++;modelState.job=job;modelControls();await refreshModels()}catch(error){modelResult.textContent=error.message}};
document.querySelector('#model-repository').oninput=()=>{remoteRequest++;remoteRepository='';document.querySelector('#model-remote').replaceChildren();document.querySelector('#model-download').disabled=true};
document.querySelector('#model-catalog').onclick=async()=>{const request=++remoteRequest,repository=document.querySelector('#model-repository').value.trim();const button=document.querySelector('#model-catalog');button.disabled=true;modelResult.textContent='正在查詢模型庫…';try{const data=await modelPost('/models/catalog',{repository});if(request!==remoteRequest)return;remoteRepository=repository;const select=document.querySelector('#model-remote');select.replaceChildren();for(const item of data.files){const option=document.createElement('option');option.value=item.filename;option.textContent=item.filename+' · '+modelBytes(item.bytes);select.append(option)}select.disabled=!data.files.length;modelResult.textContent=data.files.length?'選擇檔案後下載。':'此模型庫沒有可驗證的單檔 GGUF。';modelControls()}catch(error){if(request===remoteRequest)modelResult.textContent=error.message}finally{button.disabled=false}};
document.querySelector('#model-download').onclick=async()=>{try{const job=await modelPost('/models/download',{repository:remoteRepository,filename:document.querySelector('#model-remote').value});modelRefreshRequest++;modelState.job=job;modelControls();await refreshModels()}catch(error){modelResult.textContent=error.message}};
window.addEventListener('pagehide',()=>clearTimeout(modelTimer));
const chat=document.querySelector('#chat'),input=document.querySelector('#input'),send=document.querySelector('#send');
const effort=document.querySelector('#effort'),agent=document.querySelector('#agent'),cwd=document.querySelector('#cwd'),cwdWrap=document.querySelector('#cwd-wrap');
const mode=document.createElement('select');mode.id='mode';mode.setAttribute('aria-label','工作模式');for(const [value,label] of [['work','工作'],['plan','規劃']]){const option=document.createElement('option');option.value=value;option.textContent=label;mode.append(option)}document.querySelector('.agent-controls').append(mode);
const sidebar=document.querySelector('#sidebar'),navToggle=document.querySelector('#nav-toggle'),navMedia=matchMedia('(max-width:760px)');
function syncOverlayInert(){document.querySelector('main').inert=document.body.classList.contains('nav-open')||(navMedia.matches&&!document.querySelector('#review').hidden)}
function setNavigation(open,restoreFocus=true){const active=navMedia.matches&&open;document.body.classList.toggle('nav-open',active);sidebar.inert=navMedia.matches&&!active;sidebar.setAttribute('aria-hidden',String(sidebar.inert));syncOverlayInert();document.querySelector('#nav-backdrop').hidden=!active;navToggle.setAttribute('aria-expanded',String(active));if(active)document.querySelector('#nav-close').focus();else if(navMedia.matches&&restoreFocus)navToggle.focus()}
navToggle.onclick=()=>setNavigation(!document.body.classList.contains('nav-open'));
document.querySelector('#nav-close').onclick=()=>setNavigation(false);
document.querySelector('#nav-backdrop').onclick=()=>setNavigation(false);
navMedia.addEventListener('change',()=>setNavigation(false,false));setNavigation(false,false);
document.addEventListener('keydown',event=>{const navigation=document.body.classList.contains('nav-open'),reviewOpen=!document.querySelector('#review').hidden;if(event.key==='Escape'){if(navigation){event.preventDefault();setNavigation(false)}else if(actionsMenu.open){event.preventDefault();actionsMenu.open=false}else if(reviewOpen){event.preventDefault();document.querySelector('#review-close').click()}return}const panel=navigation?sidebar:navMedia.matches&&reviewOpen?document.querySelector('#review'):null;if(panel&&event.key==='Tab'){const items=[...panel.querySelectorAll('button:not(:disabled),summary')].filter(node=>node.getClientRects().length),first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}});
const actionsMenu=document.querySelector('#actions-menu');actionsMenu.addEventListener('click',event=>{if(event.target.closest('button'))actionsMenu.open=false});document.addEventListener('click',event=>{if(!actionsMenu.contains(event.target))actionsMenu.open=false});
function fitComposer(){input.style.height='auto';const available=window.visualViewport?.height||innerHeight;input.style.height=Math.min(input.scrollHeight,Math.max(44,Math.min(180,available*.25)))+'px'}
function fitViewport(){const viewport=window.visualViewport;if(!viewport||Math.abs(viewport.scale-1)<.01){const height=viewport?.height||innerHeight;document.documentElement.style.setProperty('--app-height',height+'px');document.documentElement.style.setProperty('--app-top',(viewport?.offsetTop||0)+'px');document.body.classList.toggle('compact-height',height<500);fitComposer()}}
window.addEventListener('resize',fitViewport);window.visualViewport?.addEventListener('resize',fitViewport);window.visualViewport?.addEventListener('scroll',fitViewport);input.addEventListener('input',fitComposer);fitViewport();
new ResizeObserver(entries=>document.documentElement.style.setProperty('--header-height',entries[0].target.getBoundingClientRect().height+'px')).observe(document.querySelector('header'));
const branchLabel=document.createElement('span');branchLabel.id='branch-label';branchLabel.className='sub';document.querySelector('header h1').after(branchLabel);const worktreeButton=document.createElement('button');worktreeButton.id='worktree-create';worktreeButton.textContent='建立分支';worktreeButton.title='Git worktree: create an isolated workspace from committed HEAD';worktreeButton.className='mini';worktreeButton.disabled=true;document.querySelector('.cwd').prepend(worktreeButton);
async function refreshWorkspace(){const id=session,path=cwd.value;document.querySelector('header h1').textContent=path?path.split(/[\\/]/).at(-1):'EdgeAgent';if(!path){branchLabel.textContent='';worktreeButton.disabled=true;return}try{const response=await fetch('/workspace/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});const info=await response.json();if(session!==id)return;branchLabel.textContent=info.branch?' · '+info.branch:'';worktreeButton.disabled=!info.git}catch{worktreeButton.disabled=true}}
worktreeButton.onclick=async()=>{if(busy||!cwd.value)return;worktreeButton.disabled=true;try{const response=await fetch('/workspace/worktree',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:cwd.value})});const info=await response.json();if(!response.ok)throw Error(info.error);openWorkspace(info.path)}catch(error){add('notice',error.message)}finally{refreshWorkspace()}};
const folderDialog=document.querySelector('#folder-dialog'),folderPath=document.querySelector('#folder-path'),folderList=document.querySelector('#folder-list');
let session=crypto.randomUUID(),busy=false,statusNode=null;
let pendingFiles=[],workQueue=[],queuePaused=false,completedTurns=0;
const queueResume=document.createElement('button');queueResume.id='queue-resume';queueResume.className='secondary';queueResume.textContent='繼續排隊';queueResume.hidden=true;document.querySelector('.action-menu').prepend(queueResume);
function refreshQueue(){queueResume.hidden=!queuePaused||!workQueue.length;queueResume.disabled=busy||modelChanging}
queueResume.onclick=()=>{if(busy||modelChanging||!workQueue.length)return;queuePaused=false;refreshQueue();const next=workQueue.shift();ask(next.text,next.attachments)};
const terminals=new Map();
window.addEventListener('pagehide',()=>{if(busy)navigator.sendBeacon('/chat/cancel',JSON.stringify({session_id:session}));for(const id of terminals.keys())navigator.sendBeacon('/terminal/close',JSON.stringify({session_id:id}))});
const threads=new Map(),toolCards=new Map();
const review=document.querySelector('#review'),reviewFiles=document.querySelector('#review-files');const reviewButton=document.createElement('button');reviewButton.id='review-toggle';reviewButton.textContent='修改';document.querySelector('.actions').prepend(reviewButton);reviewButton.onclick=()=>{review.hidden=!review.hidden;syncOverlayInert();if(!review.hidden)document.querySelector('#review-close').focus()};document.querySelector('#review-close').onclick=()=>{review.hidden=true;syncOverlayInert();reviewButton.focus()};
function rememberThread(){threads.set(session,{nodes:[...chat.childNodes],changes:[...reviewFiles.childNodes],cwd:cwd.value,title:threads.get(session)?.title||'新對話',draft:input.value,files:pendingFiles,queue:workQueue,queuePaused,effort:effort.value,agent:agent.checked,mode:mode.value});refreshQueue();renderThreads()}
function renderThreads(){const list=document.querySelector('#threads');list.replaceChildren();for(const [id,thread] of threads){const button=document.createElement('button');button.className='thread'+(id===session?' active':'');button.textContent=(thread.cwd?thread.cwd.split(/[\\/]/).at(-1)+' · ':'')+thread.title;button.title=thread.cwd||thread.title;button.onclick=()=>{if(busy)return;setNavigation(false,false);if(id===session)return;rememberThread();session=id;cwd.value=thread.cwd;chat.replaceChildren(...thread.nodes);reviewFiles.replaceChildren(...thread.changes);document.querySelector('#review-empty').hidden=reviewFiles.childNodes.length>0;clearStatus();restoreDraft();renderThreads()};list.append(button)}}
function newThread(){if(busy)return;setNavigation(false,false);rememberThread();session=crypto.randomUUID();workQueue=[];queuePaused=false;refreshQueue();chat.replaceChildren();reviewFiles.replaceChildren();document.querySelector('#review-empty').hidden=false;input.value='';pendingFiles=[];renderFiles();fitComposer();refreshTerminal();add('notice','已開始新對話');rememberThread();if(!matchMedia('(pointer:coarse)').matches)input.focus()}
document.querySelector('#new-thread').onclick=newThread;
document.querySelector('#open-project').onclick=()=>{setNavigation(false,false);document.querySelector('#browse').click()};
document.querySelector('#stop').onclick=async()=>{workQueue=[];queuePaused=false;refreshQueue();await fetch('/chat/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session})});document.querySelector('#stop').disabled=true;add('notice','正在停止工作…')};
const terminalPane=document.querySelector('#terminal-pane'),terminalOutput=document.querySelector('#terminal-output');
const terminalButton=document.createElement('button');terminalButton.id='terminal-toggle';terminalButton.textContent='終端';document.querySelector('.actions').prepend(terminalButton);
function showTerminal(open){terminalPane.hidden=!open;document.querySelector('main').classList.toggle('terminal-open',open);refreshTerminal();if(open)document.querySelector('#terminal-command').focus()}
terminalButton.onclick=()=>showTerminal(terminalPane.hidden);
document.querySelector('#terminal-hide').onclick=()=>showTerminal(false);
function terminalState(id=session){if(!terminals.has(id))terminals.set(id,{output:'',cwd:cwd.value,history:[],index:0,busy:false});return terminals.get(id)}
function refreshTerminal(){const state=terminalState();terminalOutput.textContent=state.output;terminalOutput.scrollTop=terminalOutput.scrollHeight;document.querySelector('#terminal-cwd').textContent=state.cwd||cwd.value||'請先開啟專案';document.querySelector('#terminal-run').disabled=state.busy;document.querySelector('#terminal-stop').disabled=!state.busy}
document.querySelector('#terminal-clear').onclick=()=>{terminalState().output='';refreshTerminal()};
document.querySelector('#terminal-stop').onclick=async()=>{await fetch('/terminal/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session})})};
async function consumeStream(response,onEvent){if(!response.ok)throw new Error(await response.text());const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';while(true){const {value,done}=await reader.read();pending+=decoder.decode(value||new Uint8Array(),{stream:!done});const [blocks,rest]=eventBlocks(pending);pending=rest;for(const block of blocks){let type='message',data='{}';for(const line of block.split('\n')){if(line.startsWith('event: '))type=line.slice(7);if(line.startsWith('data: '))data=line.slice(6)}await onEvent(type,JSON.parse(data))}if(done)break}}
document.querySelector('#terminal-form').onsubmit=async event=>{event.preventDefault();const field=document.querySelector('#terminal-command'),command=field.value.trim(),id=session,state=terminalState(id);if(!command||state.busy)return;if(!cwd.value){state.output+='請先開啟專案。\n';refreshTerminal();return}field.value='';state.busy=true;state.output+='PS '+(state.cwd||cwd.value)+'> '+command+'\n';state.history.push(command);state.index=state.history.length;refreshTerminal();try{const response=await fetch('/terminal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:id,command,working_dir:state.cwd||cwd.value})});await consumeStream(response,(type,data)=>{if(type==='delta')state.output+=data.text;else if(type==='done'){state.cwd=data.cwd;state.output+='[exit code: '+data.exit_code+']\n'}else if(type==='cancelled')state.output+='[已停止]\n';else if(type==='error')state.output+='錯誤：'+data.message+'\n';state.output=state.output.slice(-200000);if(session===id)refreshTerminal()})}catch(error){state.output+='錯誤：'+error.message+'\n'}finally{state.busy=false;if(session===id)refreshTerminal()}};
document.querySelector('#terminal-command').onkeydown=event=>{const state=terminalState();if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();state.index=Math.max(0,Math.min(state.history.length,state.index+(event.key==='ArrowUp'?-1:1)));event.target.value=state.history[state.index]||''}};
function renderFiles(){const host=document.querySelector('#attachments');host.replaceChildren();for(const file of pendingFiles){const chip=document.createElement('button');chip.type='button';chip.className='attachment';chip.textContent=file.name+' ×';chip.onclick=()=>{pendingFiles=pendingFiles.filter(item=>item!==file);renderFiles()};host.append(chip)}}
document.querySelector('#attach').onclick=()=>document.querySelector('#file-input').click();
document.querySelector('#file-input').onchange=async event=>{try{const selected=[...event.target.files];if(pendingFiles.length+selected.length>8)throw Error('最多附加 8 個檔案');if([...pendingFiles,...selected].reduce((sum,file)=>sum+file.size,0)>2000000)throw Error('附件合計不得超過 2 MB');for(const file of selected){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let offset=0;offset<bytes.length;offset+=8192)binary+=String.fromCharCode(...bytes.subarray(offset,offset+8192));pendingFiles.push({name:file.name,data:btoa(binary),size:file.size})}renderFiles()}catch(error){add('notice',error.message)}finally{event.target.value=''}};
function restoreDraft(){const thread=threads.get(session);input.value=thread?.draft||'';fitComposer();pendingFiles=thread?.files||[];workQueue=thread?.queue||[];queuePaused=thread?.queuePaused||false;refreshQueue();if(thread){effort.value=thread.effort;agent.checked=thread.agent;mode.value=thread.mode}cwdWrap.hidden=!agent.checked;renderFiles();refreshTerminal();refreshWorkspace();reviewButton.textContent='修改'+(reviewFiles.children.length?' · '+reviewFiles.children.length:'')}
rememberThread();refreshModels(true);
function add(kind,text){const node=document.createElement('div');node.className=(kind==='notice'?'notice':'msg '+kind);node.textContent=text;chat.append(node);chat.scrollTop=chat.scrollHeight;return node}
function eventBlocks(buffer){const blocks=buffer.split('\n\n');return [blocks.slice(0,-1),blocks.at(-1)]}
function clearStatus(){if(statusNode){statusNode.remove();statusNode=null}}
function renderAnswer(node){const raw=node.textContent;node.replaceChildren();let code=null;for(const line of raw.split('\n')){if(line.startsWith('```')){if(code){code=null}else{const pre=document.createElement('pre');code=document.createElement('code');pre.append(code);node.append(pre)}continue}if(code){code.textContent+=line+'\n';continue}const heading=line.match(/^(#{1,3})\s+(.*)/);const item=document.createElement(heading?'h'+(heading[1].length+1):'div');const source=heading?heading[2]:line;const tokens=source.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);for(const token of tokens){const element=document.createElement(token.startsWith('`')?'code':token.startsWith('**')?'strong':'span');element.textContent=token.startsWith('`')?token.slice(1,-1):token.startsWith('**')?token.slice(2,-2):token;item.append(element)}if(!source)item.append(document.createElement('br'));node.append(item)}}
agent.addEventListener('change',()=>{cwdWrap.hidden=!agent.checked});
fetch('/config').then(response=>response.json()).then(value=>{effort.value=value.thinking_effort;agent.checked=value.agent;cwdWrap.hidden=!agent.checked}).catch(()=>{});
document.querySelector('#save').addEventListener('click',async()=>{if(busy)return;const response=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thinking_effort:effort.value,agent:agent.checked})});if(!response.ok){add('notice','設定儲存失敗：'+await response.text());return}add('notice','Agent 與 thinking effort 設定已儲存')});
document.querySelector('#shutdown').addEventListener('click',async()=>{if(busy||!confirm('關閉 EdgeAgent 並釋放 GPU 記憶體？'))return;const response=await fetch('/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok){add('notice','關閉失敗：'+await response.text());return}send.disabled=true;input.disabled=true;add('notice','EdgeAgent 已關閉；需要時再執行「開啟 EdgeAgent.cmd」')});
async function directoryInfo(path){const response=await fetch('/directories',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});if(!response.ok){const value=await response.json().catch(()=>({}));throw new Error(value.error||'資料夾不存在')}return response.json()}
let folderRequest=0;
async function showDirectory(path,fallback=true){
  const request=++folderRequest,use=document.querySelector('#folder-use'),errorNode=document.querySelector('#folder-error');
  const address=document.querySelector('#folder-address'),previousAddress=address.value;
  use.disabled=true;delete folderPath.dataset.path;errorNode.hidden=true;folderList.setAttribute('aria-busy','true');
  try{
    let value;
    try{value=await directoryInfo(path)}catch(error){if(fallback&&path)value=await directoryInfo('');else throw error}
    if(request!==folderRequest||!folderDialog.open)return;
    folderPath.textContent=value.current;folderPath.dataset.path=value.current;
    if(document.activeElement!==address&&address.value===previousAddress)address.value=value.current;
    folderList.replaceChildren();
    if(value.parent){const up=document.createElement('button');up.className='folder-item';up.textContent='↰ 上一層';up.addEventListener('click',()=>showDirectory(value.parent,false));folderList.append(up)}
    for(const item of value.directories){const button=document.createElement('button');button.className='folder-item';button.textContent='📁 '+item.name;button.addEventListener('click',()=>showDirectory(item.path,false));folderList.append(button)}
    use.disabled=busy;
  }catch(error){
    if(request!==folderRequest||!folderDialog.open)return;
    errorNode.textContent='無法開啟資料夾：'+error.message;errorNode.hidden=false;
  }finally{if(request===folderRequest)folderList.setAttribute('aria-busy','false')}
}
document.querySelector('#browse').addEventListener('click',()=>{if(busy){add('notice','請先停止或等目前工作完成，再開啟資料夾。');return}folderDialog.showModal();showDirectory(cwd.value.trim())});
folderDialog.addEventListener('close',()=>{folderRequest++;document.querySelector('#folder-use').disabled=true;delete folderPath.dataset.path;folderList.setAttribute('aria-busy','false')});
document.querySelector('#folder-close').addEventListener('click',()=>folderDialog.close());
document.querySelector('#folder-address-form').onsubmit=event=>{event.preventDefault();showDirectory(document.querySelector('#folder-address').value.trim(),false)};
function openWorkspace(path){if(busy||typeof path!=='string'||!path)return;rememberThread();cwd.value=path;session=crypto.randomUUID();workQueue=[];queuePaused=false;refreshQueue();folderDialog.close();input.value='';fitComposer();pendingFiles=[];renderFiles();refreshTerminal();refreshWorkspace();chat.replaceChildren();reviewFiles.replaceChildren();document.querySelector('#review-empty').hidden=false;add('notice','已開啟 Workspace：'+cwd.value+'；這是新的對話 context');rememberThread()}
document.querySelector('#folder-use').addEventListener('click',()=>openWorkspace(folderPath.dataset.path));
async function approve(payload){
  const details=payload.preview||JSON.stringify(payload.arguments,null,2);
  const accepted=await new Promise(resolve=>{const card=document.createElement('section');card.className='approval-card';const title=document.createElement('strong');title.textContent='需要批准 · '+payload.tool;const pre=document.createElement('pre');pre.textContent=details;card.append(title,pre);for(const [label,value,scope] of [['允許本輪工作',true,'turn'],['允許這次操作',true,'once'],['拒絕',false,'once']]){const button=document.createElement('button');button.textContent=label;if(!value)button.className='secondary';button.onclick=()=>{card.replaceChildren(title);title.textContent=value?'已批准 · '+payload.tool:'已拒絕 · '+payload.tool;resolve({approved:value,scope})};card.append(button)}chat.append(card);card.scrollIntoView({block:'nearest'});document.querySelector('#stop').addEventListener('click',()=>resolve({approved:false,scope:'once'}),{once:true})});
  const response=await fetch('/chat/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session,call_id:payload.call_id,...accepted})});
  if(!response.ok)throw new Error(await response.text());
}
async function handleEvent(type,payload,answer){
  if(type==='delta'){clearStatus();answer.textContent+=payload.text;chat.scrollTop=chat.scrollHeight}
  else if(type==='status'){clearStatus();statusNode=add('notice',payload.message)}
  else if(type==='compacted'){add('notice',payload.message)}
  else if(type==='approval'){clearStatus();await approve(payload);statusNode=add('notice','等待 Agent 繼續…')}
  else if(type==='tool'){clearStatus();let card=toolCards.get(payload.call_id);if(payload.phase==='running'){card=document.createElement('details');card.className='tool-card';const title=document.createElement('summary');title.textContent='執行中 · '+payload.tool;const pre=document.createElement('pre');pre.textContent=JSON.stringify(payload.arguments,null,2);card.append(title,pre);chat.append(card);toolCards.set(payload.call_id,card)}else if(card){card.querySelector('summary').textContent='已結束 · '+payload.tool;const pre=document.createElement('pre');pre.textContent=typeof payload.result==='string'?payload.result:(payload.result?.content?.filter(item=>item.type==='text').map(item=>item.text).join('\n')||JSON.stringify(payload.result,null,2));card.append(pre)}}
  else if(type==='cancelled'){clearStatus();add('notice','工作已停止')}
  else if(type==='done'){completedTurns++}
  else if(type==='change'){document.querySelector('#review-empty').hidden=true;let card=[...reviewFiles.children].find(node=>node.dataset.path===payload.path);if(!card){card=document.createElement('details');card.dataset.path=payload.path;reviewFiles.append(card)}card.replaceChildren();const title=document.createElement('summary');title.textContent=payload.path;const pre=document.createElement('pre');for(const line of payload.diff.split('\n')){const span=document.createElement('span');span.textContent=line+'\n';if(line.startsWith('+'))span.className='diff-add';if(line.startsWith('-'))span.className='diff-remove';pre.append(span)}card.append(title,pre);reviewButton.textContent='修改 · '+reviewFiles.children.length}
  else if(type==='error'){throw new Error(payload.message)}
}
async function ask(text,attachments=[]){
  busy=true;
  const restoreInput=()=>{busy=false;input.value=text;pendingFiles=attachments;renderFiles()};
  if(agent.checked&&!cwd.value.trim()){restoreInput();add('notice','請先按「開啟資料夾」選擇這次要使用的 Workspace。');return}
  if(agent.checked){try{await directoryInfo(cwd.value.trim())}catch(error){restoreInput();add('notice','Workspace 無效：'+error.message+'；請重新開啟資料夾。');return}}
  busy=true;send.disabled=false;send.textContent='＋';send.setAttribute('aria-label','排入下一輪');document.querySelector('#stop').hidden=false;document.querySelector('#stop').disabled=false;document.querySelector('#welcome')?.remove();if(threads.get(session)?.title==='新對話'){threads.get(session).title=text.slice(0,28);renderThreads()}add('user',text+(attachments.length?'\n📎 '+attachments.map(file=>file.name).join('、'):''));const answer=add('assistant','');
  try{
    const body={session_id:session,text,attachments,thinking_effort:effort.value,agent:agent.checked,plan:mode.value==='plan',working_dir:cwd.value.trim()};
    const response=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw new Error(await response.text());
    await consumeStream(response,(type,payload)=>handleEvent(type,payload,answer));
    clearStatus();if(!answer.textContent)answer.remove();else renderAnswer(answer);
  }catch(error){clearStatus();answer.textContent='錯誤：'+error.message;queuePaused=true;if(workQueue.length)add('notice','排隊工作已暫停；檢查進度後可從更多操作選擇「繼續排隊」。')}
  finally{busy=false;send.disabled=modelChanging;send.textContent='↑';send.setAttribute('aria-label','送出');document.querySelector('#stop').hidden=true;rememberThread();input.focus();if(!queuePaused&&workQueue.length){const next=workQueue.shift();ask(next.text,next.attachments)}}
}
document.querySelector('#form').addEventListener('submit',event=>{event.preventDefault();if(modelChanging){add('notice','模型切換中，請稍候再送出');return}const text=input.value.trim()||(pendingFiles.length?'分析附件':'');if(!text)return;const attachments=pendingFiles;pendingFiles=[];input.value='';renderFiles();if(busy){workQueue.push({text,attachments});add('notice','已排入下一輪工作 · 待處理 '+workQueue.length+' 項')}else ask(text,attachments)});
input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229&&!matchMedia('(pointer:coarse)').matches){event.preventDefault();document.querySelector('#form').requestSubmit()}});
document.querySelector('#reset').addEventListener('click',newThread);
</script></body></html>"""
