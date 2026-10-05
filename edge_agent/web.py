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
#form{order:3;flex:none;display:grid;grid-template-columns:auto minmax(0,1fr) auto auto auto;gap:6px;margin:8px auto 0;width:min(820px,calc(100% - 24px));background:#262626;border:1px solid #3b3b3b;border-radius:24px;padding:10px;align-items:end;min-width:0;position:relative}
#input{grid-column:1/-1}#attach-wrap{position:relative}#attach-menu{position:absolute;bottom:calc(100% + 12px);left:0;z-index:36;width:min(250px,calc(100vw - 48px));padding:6px;background:#303030;border:1px solid #555;border-radius:14px;box-shadow:0 8px 28px #0008;display:grid;gap:3px}#attach-menu button{text-align:left;background:transparent;min-height:44px}#attach-menu button:hover:not(:disabled){background:#444}#record{width:38px;height:38px;padding:8px;background:transparent;border-radius:50%}#record svg{width:22px;height:22px}#record[aria-pressed=true]{background:#913737}#record-status{grid-column:1/-1;font-size:12px;color:#ed9999}#form.dragging{border-color:#bbb}
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
.tool-card.tool-error{border-color:#a34e4e}.tool-card.tool-error summary{color:#ed9999}
.approval-card button{margin:8px 8px 0 0;padding:8px 14px;background:#eee;color:#222}.approval-card .secondary{background:#444;color:#ddd}
#attachments{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px;max-height:min(100px,18vh);overflow:auto}#attachments:empty{display:none}
.attachment{max-width:100%;display:flex;gap:8px;align-items:center;font-size:12px;border:1px solid #555;padding:6px 9px;border-radius:12px;background:#303030}.attachment img{width:54px;height:54px;object-fit:cover;border-radius:7px}.attachment span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.attachment button{padding:2px;background:transparent;min-width:28px;min-height:28px}.attachment.unsupported{border-color:#b97878}.message-media{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.message-media img{max-width:min(240px,100%);max-height:220px;border-radius:12px}.message-media audio{max-width:100%;width:260px}
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
#models-dialog,#permissions-dialog,#records-dialog{width:min(700px,calc(100vw - 24px));max-height:calc(var(--app-height) - 24px);padding:0;color:#ececec;background:#222;border:1px solid #444;border-radius:14px;overflow:auto}
#permissions-dialog textarea{background:#181818;border:1px solid #555;border-radius:8px;min-height:85px}
#models-dialog::backdrop{background:#0009}
.model-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;position:sticky;top:0;background:#222;z-index:1}
.model-body{padding:0 18px 18px;display:grid;gap:12px}.model-body label{display:grid;gap:6px}.model-body input,.model-body select{width:100%;min-width:0;padding:10px;background:#181818;color:#eee;border:1px solid #555;border-radius:8px;font:inherit}
.model-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.model-body .model-check{display:flex;align-items:center;gap:8px}.model-check input{width:auto}.model-body p{margin:0;overflow-wrap:anywhere;color:#bbb}.model-row{display:flex;gap:8px;align-items:center}.model-row input{flex:1}.model-row button{flex-shrink:0}#model-progress{width:100%}#model-result{white-space:pre-wrap;color:#ddd;overflow-wrap:anywhere}
@media(max-width:480px){.model-grid{grid-template-columns:1fr}.model-row{flex-wrap:wrap}.model-row input{flex-basis:100%}}
</style>
</head>
<body><aside id="sidebar" aria-label="專案與對話"><div class="sidebar-heading"><h2>EdgeAgent</h2><button id="nav-close" aria-label="關閉側欄">×</button></div><button id="new-thread" class="secondary">＋ 新對話</button><button id="open-project" class="secondary">開啟專案</button><div class="nav-title">專案與對話</div><div id="threads"></div><div id="privacy">本機工作 · 未開啟紀錄時只在記憶體<br>紀錄可寫進你選的加密磁碟</div></aside><button id="nav-backdrop" aria-label="關閉側欄" hidden></button><main>
<header><button id="nav-toggle" aria-label="開啟側欄" aria-controls="sidebar" aria-expanded="false">☰</button><div class="heading"><h1>EdgeAgent</h1><div class="sub">本機編程 Agent · 紀錄由你選擇</div></div><div class="actions"><details id="actions-menu"><summary aria-label="更多操作">⋯</summary><div class="action-menu"><button id="export-chat" class="secondary" title="下載目前對話的 TXT，儲存位置由瀏覽器決定">匯出對話 TXT</button><button id="models-open" class="secondary">模型模組</button><button id="permissions-open" class="secondary">權限</button><button id="records-open" class="secondary">自動紀錄</button><button id="save" class="secondary">儲存設定</button><button id="reset" class="secondary">新對話</button><button id="shutdown" class="danger">關閉EdgeAgent</button></div></details></div></header>
<section class="controls">
  <label class="control"><span>Thinking effort</span><select id="effort" aria-label="Thinking effort"><option value="off">關閉</option><option value="low">低</option><option value="medium" selected>中</option><option value="high">高</option></select></label>
  <div class="agent-controls"><label class="toggle"><input id="agent" type="checkbox" checked>啟用 Agent</label></div>
  <div class="control cwd" id="cwd-wrap"><span class="control-title"><span>目前 Workspace</span><button id="browse" type="button" class="secondary mini">開啟資料夾</button></span><input id="cwd" type="text" readonly placeholder="尚未開啟資料夾"></div>
</section>
<section id="chat"><div id="welcome"><h2>今天要完成什麼？</h2><p>開啟專案，讓目前模型讀取、修改與驗證你的工作。</p></div><div class="notice"><span id="active-model-label">正在讀取模型狀態…</span>；Agent 的寫檔、編輯與 Shell 操作會先請你批准</div></section>
<section id="terminal-pane" hidden><div class="terminal-head"><strong>PowerShell</strong><span id="terminal-cwd"></span><button id="terminal-stop" type="button" disabled>停止</button><button id="terminal-clear" type="button">清除</button><button id="terminal-hide" type="button">收起</button></div><pre id="terminal-output" role="log" aria-live="polite"></pre><form id="terminal-form"><input id="terminal-command" aria-label="終端命令" autocomplete="off" placeholder="在專案中執行命令…"><button id="terminal-run">執行</button></form></section>
<form id="form"><div id="attachments"></div><input id="file-input" type="file" multiple hidden accept=".txt,.md,.py,.js,.ts,.tsx,.jsx,.json,.csv,.html,.css,.yaml,.yml,.toml,.xml,.log,.pdf,.docx"><input id="image-input" type="file" multiple hidden accept=".png,.jpg,.jpeg,.gif,.bmp,.webp"><input id="audio-input" type="file" multiple hidden accept=".wav,.mp3,.flac"><textarea id="input" aria-label="工作指令" placeholder="描述工作，或提出問題…"></textarea><div id="attach-wrap"><button id="attach" type="button" aria-label="附加檔案" aria-expanded="false" aria-controls="attach-menu">＋</button><div id="attach-menu" aria-label="附件選項" hidden><button id="attach-image" type="button" disabled>▧　加入圖片</button><button id="attach-audio" type="button" disabled>♫　加入音訊</button><button id="attach-document" type="button">▤　加入文件</button></div></div><span></span><button id="record" type="button" aria-label="錄製音訊" aria-pressed="false" disabled><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg></button><button id="stop" type="button" hidden>停止</button><button id="send" aria-label="送出">↑</button><div id="record-status" role="status" hidden></div></form>
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
<label>圖片／音訊配件<select id="model-mmproj" aria-label="多模態配件"><option value="">預設配件（內建模型自動下載）</option></select></label>
<p>其他模型請選同一來源、相同基底的 mmproj；圖片和音訊選項依載入後的實際能力啟用。</p>
<p>LoRA 是個人資產；框架不附帶或自動下載。自行安裝後重新掃描，並選擇訓練時使用的相同基底模型。</p>
<div class="model-grid"><label>Context（token）<input id="model-context" type="number" min="8192" max="262144" step="1024"></label><label>GPU 層數（-1 自動分配，0 使用 CPU）<input id="model-gpu" type="number" min="-1" max="999"></label></div>
<label>日常工作記憶目標（token）<input id="model-working" type="number" min="8192" max="262144" step="1024"></label>
<p>超過工作目標會滾動摘要，相關舊對話從 RAM 檢索。Context 是引擎容量；工作目標不會縮小它或減少已配置的 KV。工作目標至少為輸出上限的兩倍，至多為 Context；摘要會保留完整的訊息邊界，因此可能暫時超過目標。結束服務會清除對話。</p>
<label>單次輸出上限（token，含思考）<input id="model-output" type="number" min="512" max="131072" step="512"></label>
<p>Context 容納輸入、媒體、歷史與輸出，至少 8192 token。單次輸出可另行設定，額度至多為 context 的一半；SDK 依剩餘空間進一步調整。較長輸出可能需要更多時間。</p>
<label class="model-check"><input id="model-low-vram" type="checkbox">節省 VRAM</label>
<div class="model-row"><button id="model-switch" type="button">套用並切換</button><button id="model-refresh" type="button" class="secondary">重新掃描</button></div>
<hr style="width:100%;border:0;border-top:1px solid #444">
<label>下載其他模型<div class="model-row"><input id="model-repository" placeholder="Hugging Face 作者/模型庫" aria-label="Hugging Face 模型庫"><button id="model-catalog" type="button">列出檔案</button></div></label>
<label>GGUF 檔案<select id="model-remote" aria-label="可下載的 GGUF 檔案" disabled></select></label>
<button id="model-download" type="button" disabled>下載模型</button>
<p>支援現有 llama.cpp 可載入的單檔 GGUF。下載會驗證 SHA256；LoRA 和多模態配件不會列為回覆模型。工具呼叫格式不代表模型具有足夠的 Agent 能力。</p>
<progress id="model-progress" hidden></progress><div id="model-result" role="status" aria-live="polite"></div>
</div></dialog>
<dialog id="permissions-dialog" aria-label="權限">
<div class="model-head"><strong>權限</strong><button id="permissions-close" type="button">關閉</button></div>
<form id="permissions-form" class="model-body">
<p>「日常唯讀自動」會直接執行工作區內可確認的列檔、搜尋、計數指令；寫入、外部程式和無法確認的指令仍詢問。讀過網頁之後，寫檔與指令會重新詢問；刪除或觸及受保護檔案的指令仍需單次批准。</p>
<div id="permissions-profiles"></div>
<button id="permissions-save" type="submit">儲存權限</button><p id="permissions-result" role="status"></p>
</form></dialog>
<dialog id="records-dialog" aria-label="紀錄">
<div class="model-head"><strong>紀錄</strong><button id="records-close" type="button">關閉</button></div>
<form id="records-form" class="model-body">
<p>只存文字到你選的加密磁碟（外接硬碟，或 SSD 上的加密容器）。容器必須掛上、解鎖，並有獨立磁碟代號。磁碟無法使用時就暫停，不會改寫到別處。</p>
<label class="model-check"><input id="records-enabled" type="checkbox">記錄工作對話</label>
<label>紀錄資料夾<input id="records-folder" type="text" placeholder="例如 E:\\AgentRecords" aria-label="紀錄資料夾"></label>
<button id="records-prepare" type="button">設為紀錄資料夾</button>
<button id="records-save" type="submit">儲存紀錄設定</button>
<p id="records-status" role="status" aria-live="polite"></p><p id="records-result" role="status"></p>
</form></dialog>
<script>
const recordsDialog=document.querySelector('#records-dialog'),recordsResult=document.querySelector('#records-result');
let recordsTimer=null,recordsLoading=false;
async function refreshRecords(reset=false){try{const response=await fetch('/records');if(!response.ok)throw Error('讀取紀錄設定失敗');const data=await response.json();if(reset){document.querySelector('#records-enabled').checked=data.record_sessions;document.querySelector('#records-folder').value=data.record_folder}document.querySelector('#records-status').textContent=data.status;return true}catch(error){recordsResult.textContent=error.message;return false}finally{clearTimeout(recordsTimer);if(recordsDialog.open)recordsTimer=setTimeout(()=>refreshRecords(),1000)}}
document.querySelector('#records-open').onclick=async()=>{recordsDialog.showModal();recordsResult.textContent='';recordsLoading=true;document.querySelector('#records-save').disabled=true;document.querySelector('#records-prepare').disabled=true;const loaded=await refreshRecords(true);recordsLoading=!loaded;document.querySelector('#records-save').disabled=!loaded;document.querySelector('#records-prepare').disabled=!loaded};
document.querySelector('#records-close').onclick=()=>recordsDialog.close();
recordsDialog.addEventListener('close',()=>clearTimeout(recordsTimer));
document.querySelector('#records-prepare').onclick=async()=>{if(recordsLoading)return;try{await modelPost('/records/prepare',{folder:document.querySelector('#records-folder').value.trim()});recordsResult.textContent='已設為紀錄資料夾；儲存並開啟記錄後生效。';await refreshRecords()}catch(error){recordsResult.textContent=error.message}};
document.querySelector('#records-form').onsubmit=async event=>{event.preventDefault();if(recordsLoading)return;try{await modelPost('/config',{record_sessions:document.querySelector('#records-enabled').checked,record_folder:document.querySelector('#records-folder').value.trim()});recordsResult.textContent='紀錄設定已儲存';await refreshRecords()}catch(error){recordsResult.textContent=error.message}};
const permissionDialog=document.querySelector('#permissions-dialog');
let permissionLoading=false;
function renderPermissions(profiles){const container=document.querySelector('#permissions-profiles');container.replaceChildren();for(const [client,title] of [['ui','網頁'],['discord','Discord']]){const section=document.createElement('fieldset');section.style.minWidth='0';const legend=document.createElement('legend');legend.textContent=title;section.append(legend);const enabled=document.createElement('label');enabled.className='model-check';const check=document.createElement('input');check.type='checkbox';check.id='permission-'+client+'-enabled';check.checked=profiles[client].enabled;enabled.append(check,document.createTextNode('啟用 '+title+' Agent'));section.append(enabled);for(const [category,label] of [['edit','寫檔'],['shell','執行指令'],['web','上網']]){const row=document.createElement('label');row.textContent=label;const select=document.createElement('select');select.id='permission-'+client+'-'+category;const choices=[['allow','允許'],['ask','詢問'],['deny','禁止']];if(category==='shell')choices.unshift(['routine','日常唯讀自動，其餘詢問']);for(const [value,text] of choices)select.append(new Option(text,value));select.value=profiles[client][category];row.append(select);section.append(row)}const folders=document.createElement('label');folders.textContent='可用資料夾（每行一個；Discord 至少需要一個）';const area=document.createElement('textarea');area.id='permission-'+client+'-workspaces';area.value=profiles[client].workspaces.join('\n');folders.append(area);section.append(folders);container.append(section)}}
document.querySelector('#permissions-open').onclick=async()=>{permissionDialog.showModal();permissionLoading=true;document.querySelector('#permissions-save').disabled=true;const result=document.querySelector('#permissions-result');result.textContent='正在讀取權限…';try{const response=await fetch('/config');if(!response.ok)throw Error('讀取權限失敗');renderPermissions((await response.json()).permissions);result.textContent=''}catch(error){result.textContent=error.message}finally{permissionLoading=false;document.querySelector('#permissions-save').disabled=!document.querySelector('#permission-ui-edit')}};
document.querySelector('#permissions-close').onclick=()=>permissionDialog.close();
document.querySelector('#permissions-form').onsubmit=async event=>{event.preventDefault();if(permissionLoading)return;const button=document.querySelector('#permissions-save'),result=document.querySelector('#permissions-result');button.disabled=true;try{const permissions={};for(const client of ['ui','discord']){const profile={enabled:document.querySelector('#permission-'+client+'-enabled').checked,workspaces:document.querySelector('#permission-'+client+'-workspaces').value.split(/\r?\n/).map(path=>path.trim()).filter(Boolean)};for(const category of ['edit','shell','web'])profile[category]=document.querySelector('#permission-'+client+'-'+category).value;permissions[client]=profile}await modelPost('/config',{permissions});result.textContent='權限已儲存，下次工作生效。'}catch(error){result.textContent=error.message}finally{button.disabled=false}};
// Model controls use the same core and keep conversation/workspace state in RAM.
const modelDialog=document.querySelector('#models-dialog'),modelSelect=document.querySelector('#model-select'),modelResult=document.querySelector('#model-result');
let modelState=null,modelTimer=null,modelChanging=false,remoteRepository='',remoteRequest=0,modelRefreshRequest=0;
const modelBytes=value=>(Number(value)/1073741824).toFixed(2)+' GiB';
async function modelPost(path,payload){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error||'模型操作失敗');return data}
function syncModelOptions(select,options,selected){const current=[...select.options];if(current.length!==options.length||current.some((option,index)=>option.value!==options[index][0]||option.textContent!==options[index][1]))select.replaceChildren(...options.map(([value,label])=>new Option(label,value)));const target=options.some(([value])=>value===selected)?selected:(options[0]?.[0]||'');if(select.value!==target)select.value=target}
function adapterOptions(selected=''){const select=document.querySelector('#model-lora'),base=modelState?.models.find(item=>item.filename===modelSelect.value);const options=[['','\u4e0d\u4f7f\u7528']];for(const item of modelState?.models||[]){if(!item.adapter||item.split||item.error||item.architecture!==base?.architecture)continue;options.push([item.filename,item.filename])}syncModelOptions(select,options,selected)}
function modelDetails(){const selected=modelState?.models.find(item=>item.filename===modelSelect.value);document.querySelector('#model-info').textContent=selected?selected.architecture+' · '+modelBytes(selected.bytes)+' · '+(selected.context_length?'模型上限 '+selected.context_length+' token':'Context 上限未提供')+' · '+(selected.tool_template?'內建工具呼叫格式':'未偵測到工具呼叫格式；Agent 相容性需實測'):'';if(selected?.context_length){const context=document.querySelector('#model-context');context.value=Math.min(Number(context.value)||32768,selected.context_length)}}
function projectorOptions(selected=''){const select=document.querySelector('#model-mmproj'),options=[['','\u9810\u8a2d\u914d\u4ef6\uff08\u5167\u5efa\u6a21\u578b\u81ea\u52d5\u4e0b\u8f09\uff09']];for(const item of modelState?.models||[]){if(item.projector&&!item.error&&!item.split)options.push([item.filename,item.filename])}syncModelOptions(select,options,selected)}
function modelControls(){const running=modelState?.job.state==='running';modelChanging=running&&modelState.job.action==='switch';for(const id of ['model-switch','model-refresh','model-catalog','model-download','model-select','model-lora','model-mmproj','model-context','model-output','model-working','model-gpu','model-low-vram']){const node=document.querySelector('#'+id);node.disabled=running||(id==='model-switch'&&(!modelSelect.value||busy||modelState?.mode==='character'))||(id==='model-download'&&!document.querySelector('#model-remote').value)}send.disabled=modelChanging;updateAttachmentControls()}
async function refreshModels(reset=false){const request=++modelRefreshRequest;if(reset)modelSelect.disabled=true;try{const response=await fetch('/models');if(!response.ok)throw Error('無法讀取模型清單');const data=await response.json();if(request!==modelRefreshRequest)return;const previous=modelSelect.value;modelState=data;const options=data.models.filter(item=>!item.adapter&&!item.projector&&!item.split&&!item.error).map(item=>[item.filename,item.filename+' · '+modelBytes(item.bytes)]);syncModelOptions(modelSelect,options,options.some(([value])=>value===previous)?previous:data.config.model);if(reset){document.querySelector('#model-context').value=data.config.context_size;document.querySelector('#model-output').value=data.config.max_output_tokens;document.querySelector('#model-working').value=data.config.working_context_size;document.querySelector('#model-gpu').value=data.config.gpu_layers;document.querySelector('#model-low-vram').checked=data.config.low_vram}const active=(data.active_model||'尚未載入模型')+(data.context_size?' · Context '+data.context_size+' token':'')+(data.working_context_size?' \u00b7 \u5de5\u4f5c\u76ee\u6a19 '+data.working_context_size+' token':'')+(data.max_output_tokens?' · 輸出上限 '+data.max_output_tokens+' token':'');const adapter=' · LoRA '+(data.active_lora||'不使用');const lease=data.mode==='character'?'SilentTalk 租用中，Agent 自動共用；歸還後恢復 '+data.config.model+' · LoRA '+(data.config.lora||'不使用')+'。 ':'';document.querySelector('#model-active').textContent=lease+'目前：'+active+adapter;document.querySelector('#active-model-label')?.replaceChildren(document.createTextNode(active));adapterOptions(reset?data.config.lora:document.querySelector('#model-lora').value);projectorOptions(reset?data.config.mmproj:document.querySelector('#model-mmproj').value);modelDetails();const job=data.job,progress=document.querySelector('#model-progress');progress.hidden=job.state!=='running'||job.action!=='download';if(job.total){progress.max=job.total;progress.value=job.received}else progress.removeAttribute('value');if(job.state==='running'){modelResult.textContent=job.action==='switch'?'正在切換模型，完成後可繼續工作…':'正在下載 '+(job.filename||'模型')+' · '+modelBytes(job.received)+(job.total?' / '+modelBytes(job.total):'');clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),1000)}else{if(job.state==='done')modelResult.textContent=job.action==='switch'?'已切換模型，可在原對話繼續工作。':'下載完成，請選擇模型並套用。';if(job.state==='failed')modelResult.textContent=job.error;clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),3000)}if(data.loading||(!data.active_model&&!data.error)){clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),1000)}if(data.error)modelResult.textContent=data.error;modelControls()}catch(error){if(request!==modelRefreshRequest)return;modelResult.textContent=error.message;modelChanging=false;send.disabled=false;clearTimeout(modelTimer);modelTimer=setTimeout(()=>refreshModels(),3000)}}
document.querySelector('#models-open').onclick=()=>{modelDialog.showModal();refreshModels(true)};
document.querySelector('#models-close').onclick=()=>modelDialog.close();
document.querySelector('#model-refresh').onclick=()=>refreshModels(true);
modelSelect.onchange=()=>{adapterOptions();document.querySelector('#model-mmproj').value='';modelDetails()};
document.querySelector('#model-switch').onclick=async()=>{if(busy)return;try{const context=Number(document.querySelector('#model-context').value),output=Number(document.querySelector('#model-output').value),working=Number(document.querySelector('#model-working').value),gpu=Number(document.querySelector('#model-gpu').value);if(!Number.isInteger(context)||context<8192||context>262144||!Number.isInteger(output)||output<512||output>131072||!Number.isInteger(working)||working<8192||working>262144||!Number.isInteger(gpu)||gpu< -1||gpu>999)throw Error('請輸入有效的 Context、工作記憶目標、單次輸出上限和 GPU 層數');const job=await modelPost('/models/switch',{model:modelSelect.value,lora:document.querySelector('#model-lora').value,mmproj:document.querySelector('#model-mmproj').value,context_size:context,max_output_tokens:output,working_context_size:working,gpu_layers:gpu,low_vram:document.querySelector('#model-low-vram').checked});modelRefreshRequest++;modelState.job=job;modelControls();await refreshModels()}catch(error){modelResult.textContent=error.message}};
document.querySelector('#model-repository').oninput=()=>{remoteRequest++;remoteRepository='';document.querySelector('#model-remote').replaceChildren();document.querySelector('#model-download').disabled=true};
document.querySelector('#model-catalog').onclick=async()=>{const request=++remoteRequest,repository=document.querySelector('#model-repository').value.trim();const button=document.querySelector('#model-catalog');button.disabled=true;modelResult.textContent='正在查詢模型庫…';try{const data=await modelPost('/models/catalog',{repository});if(request!==remoteRequest)return;remoteRepository=repository;const select=document.querySelector('#model-remote');select.replaceChildren();for(const item of data.files){const option=document.createElement('option');option.value=item.filename;option.textContent=(item.projector?'配件 · ':'')+item.filename+' · '+modelBytes(item.bytes);select.append(option)}select.disabled=!data.files.length;modelResult.textContent=data.files.length?'選擇檔案後下載。':'此模型庫沒有可驗證的單檔 GGUF。';modelControls()}catch(error){if(request===remoteRequest)modelResult.textContent=error.message}finally{button.disabled=false}};
document.querySelector('#model-download').onclick=async()=>{try{const job=await modelPost('/models/download',{repository:remoteRepository,filename:document.querySelector('#model-remote').value});modelRefreshRequest++;modelState.job=job;modelControls();await refreshModels()}catch(error){modelResult.textContent=error.message}};
window.addEventListener('pagehide',()=>clearTimeout(modelTimer));
const chat=document.querySelector('#chat'),input=document.querySelector('#input'),send=document.querySelector('#send');
const effort=document.querySelector('#effort'),agent=document.querySelector('#agent'),cwd=document.querySelector('#cwd'),cwdWrap=document.querySelector('#cwd-wrap');
const mode=document.createElement('select');mode.id='mode';mode.setAttribute('aria-label','工作模式');mode.title='工作：直接動手。求知儀式：先一輪一輪問清楚你的需求、每題附建議答案，你確認後寫成規格；儀式中只讀不改。推演儀式：討論資料和想法，先查清事實和前提，指出漏洞，不附和、不誤導。';for(const [value,label] of [['work','工作'],['interview','求知儀式'],['discussion','推演儀式']]){const option=document.createElement('option');option.value=value;option.textContent=label;mode.append(option)}document.querySelector('.agent-controls').append(mode);
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
function renderThreads(){const list=document.querySelector('#threads');list.replaceChildren();for(const [id,thread] of threads){const button=document.createElement('button');button.className='thread'+(id===session?' active':'');button.textContent=(thread.cwd?thread.cwd.split(/[\\/]/).at(-1)+' · ':'')+thread.title;button.title=thread.cwd||thread.title;button.onclick=()=>{if(busy||recording||recordStarting||readingFiles)return;setNavigation(false,false);if(id===session)return;rememberThread();session=id;cwd.value=thread.cwd;chat.replaceChildren(...thread.nodes);reviewFiles.replaceChildren(...thread.changes);document.querySelector('#review-empty').hidden=reviewFiles.childNodes.length>0;clearStatus();restoreDraft();renderThreads()};list.append(button)}}
function newThread(){if(busy||recording||recordStarting||readingFiles)return;setNavigation(false,false);rememberThread();session=crypto.randomUUID();workQueue=[];queuePaused=false;refreshQueue();chat.replaceChildren();reviewFiles.replaceChildren();document.querySelector('#review-empty').hidden=false;input.value='';pendingFiles=[];renderFiles();fitComposer();refreshTerminal();add('notice','已開始新對話');rememberThread();if(!matchMedia('(pointer:coarse)').matches)input.focus()}
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
const attachMenu=document.querySelector('#attach-menu'),recordButton=document.querySelector('#record');
let recording=null,recordStarting=false,readingFiles=false;
function fileKind(file){if(/\.(png|jpe?g|gif|bmp|webp)$/i.test(file.name))return 'vision';if(/\.(wav|mp3|flac)$/i.test(file.name))return 'audio';return 'document'}
function mediaSupported(file){const kind=fileKind(file);return kind==='document'||modelState?.capabilities?.[kind]===true}
function updateAttachmentControls(){for(const [id,kind] of [['attach-image','vision'],['attach-audio','audio']]){const node=document.querySelector('#'+id);node.disabled=!modelState?.capabilities?.[kind];node.title=node.disabled?'目前模型不支援'+(kind==='audio'?'音訊':'圖片')+'輸入':''}recordButton.disabled=!recording&&(!modelState?.capabilities?.audio||!navigator.mediaDevices?.getUserMedia||recordStarting);recordButton.title=!modelState?.capabilities?.audio?'目前模型不支援音訊輸入':!navigator.mediaDevices?.getUserMedia?'瀏覽器無法使用麥克風':'錄製音訊';renderFiles()}
function closeAttachMenu(){attachMenu.hidden=true;document.querySelector('#attach').setAttribute('aria-expanded','false')}
document.querySelector('#attach').onclick=()=>{attachMenu.hidden=!attachMenu.hidden;document.querySelector('#attach').setAttribute('aria-expanded',String(!attachMenu.hidden))};
for(const [button,field] of [['attach-image','image-input'],['attach-audio','audio-input'],['attach-document','file-input']])document.querySelector('#'+button).onclick=()=>{closeAttachMenu();document.querySelector('#'+field).click()};
document.addEventListener('click',event=>{if(!event.target.closest('#attach-wrap'))closeAttachMenu()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!attachMenu.hidden){closeAttachMenu();document.querySelector('#attach').focus()}});
function renderFiles(){const host=document.querySelector('#attachments');host.replaceChildren();for(const file of pendingFiles){const chip=document.createElement('div');chip.className='attachment'+(mediaSupported(file)?'':' unsupported');if(fileKind(file)==='vision'){const image=document.createElement('img');image.src='data:'+file.mime+';base64,'+file.data;image.alt=file.name;chip.append(image)}const label=document.createElement('span');label.textContent=(fileKind(file)==='audio'?'♫ ':'')+file.name+(mediaSupported(file)?'':' · 模型不支援');const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','移除 '+file.name);remove.onclick=()=>{pendingFiles=pendingFiles.filter(item=>item!==file);renderFiles()};chip.append(label,remove);host.append(chip)}}
async function addFiles(selected){if(readingFiles)throw Error('附件正在讀取，請稍候');readingFiles=true;try{if(pendingFiles.length+selected.length>8)throw Error('最多附加 8 個檔案');const prepared=[];for(let file of selected){if(!mediaSupported(file))throw Error('目前模型不支援'+(fileKind(file)==='audio'?'音訊':'圖片')+'輸入');if(file.size>20000000)throw Error('附件合計不得超過 20 MB');if(/\.webp$/i.test(file.name)){const bitmap=await createImageBitmap(file);try{const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;canvas.getContext('2d').drawImage(bitmap,0,0);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error('無法讀取圖片');file=new File([blob],file.name.replace(/\.webp$/i,'.png'),{type:'image/png'})}finally{bitmap.close()}}const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let offset=0;offset<bytes.length;offset+=8192)binary+=String.fromCharCode(...bytes.subarray(offset,offset+8192));const extension=file.name.split('.').at(-1).toLowerCase(),mime={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',bmp:'image/bmp',wav:'audio/wav',mp3:'audio/mpeg',flac:'audio/flac'}[extension]||file.type;prepared.push({name:file.name,data:btoa(binary),size:file.size,mime})}const combined=[...pendingFiles,...prepared];if(combined.reduce((sum,file)=>sum+file.size,0)>20000000)throw Error('附件合計不得超過 20 MB');if(combined.filter(file=>fileKind(file)==='document').reduce((sum,file)=>sum+file.size,0)>2000000)throw Error('文件附件合計不得超過 2 MB');if(combined.length>8)throw Error('最多附加 8 個檔案');pendingFiles=combined;renderFiles()}finally{readingFiles=false}}
for(const id of ['file-input','image-input','audio-input'])document.querySelector('#'+id).onchange=async event=>{try{await addFiles([...event.target.files])}catch(error){add('notice',error.message)}finally{event.target.value=''}};
input.addEventListener('paste',async event=>{const files=[...event.clipboardData.files];if(!files.length)return;event.preventDefault();try{await addFiles(files)}catch(error){add('notice',error.message)}});
const composer=document.querySelector('#form');composer.addEventListener('dragover',event=>{event.preventDefault();composer.classList.add('dragging')});composer.addEventListener('dragleave',event=>{if(!composer.contains(event.relatedTarget))composer.classList.remove('dragging')});composer.addEventListener('drop',async event=>{event.preventDefault();composer.classList.remove('dragging');try{await addFiles([...event.dataTransfer.files])}catch(error){add('notice',error.message)}});
function wavBytes(samples){const buffer=new ArrayBuffer(44+samples.length*2),view=new DataView(buffer);const text=(offset,value)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i))};text(0,'RIFF');view.setUint32(4,36+samples.length*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,samples.length*2,true);for(let i=0;i<samples.length;i++)view.setInt16(44+i*2,Math.max(-1,Math.min(1,samples[i]))*32767,true);return buffer}
async function stopRecording(keep=true){const current=recording;if(!current)return;recording=null;recordStarting=true;clearTimeout(current.timer);current.node.disconnect();current.source.disconnect();current.stream.getTracks().forEach(track=>track.stop());updateAttachmentControls();try{await current.context.close();if(keep&&current.length){const samples=new Float32Array(current.length);let offset=0;for(const chunk of current.chunks){samples.set(chunk,offset);offset+=chunk.length}await addFiles([new File([wavBytes(samples)],'錄音.wav',{type:'audio/wav'})])}}finally{recordStarting=false;recordButton.setAttribute('aria-pressed','false');recordButton.setAttribute('aria-label','錄製音訊');document.querySelector('#record-status').hidden=true;updateAttachmentControls()}}
recordButton.onclick=async()=>{try{if(recording){await stopRecording();return}recordStarting=true;updateAttachmentControls();const stream=await navigator.mediaDevices.getUserMedia({audio:true});if(!modelState?.capabilities?.audio){stream.getTracks().forEach(track=>track.stop());throw Error('目前模型不支援音訊輸入')}let context;try{context=new AudioContext({sampleRate:16000});await context.resume();const source=context.createMediaStreamSource(stream),node=context.createScriptProcessor(4096,1,1);const state={context,stream,source,node,chunks:[],length:0,timer:null};node.onaudioprocess=event=>{if(recording!==state)return;const chunk=event.inputBuffer.getChannelData(0).slice();state.chunks.push(chunk);state.length+=chunk.length};source.connect(node);node.connect(context.destination);recording=state;state.timer=setTimeout(()=>stopRecording().catch(error=>add('notice',error.message)),120000);recordButton.setAttribute('aria-pressed','true');recordButton.setAttribute('aria-label','停止錄音');const status=document.querySelector('#record-status');status.textContent='正在錄音 · 點麥克風停止（最長 2 分鐘）';status.hidden=false}catch(error){stream.getTracks().forEach(track=>track.stop());if(context)await context.close();throw error}}catch(error){add('notice','錄音失敗：'+error.message)}finally{recordStarting=false;updateAttachmentControls()}};
window.addEventListener('pagehide',()=>{if(recording){clearTimeout(recording.timer);recording.stream.getTracks().forEach(track=>track.stop());recording.context.close();recording=null}});
function showMessageFiles(node,files){const host=document.createElement('div');host.className='message-media';for(const file of files){const kind=fileKind(file);if(kind==='vision'){const image=document.createElement('img');image.src='data:'+file.mime+';base64,'+file.data;image.alt=file.name;host.append(image)}else if(kind==='audio'){const audio=document.createElement('audio');audio.controls=true;audio.src='data:'+file.mime+';base64,'+file.data;audio.setAttribute('aria-label',file.name);host.append(audio)}}if(host.childNodes.length)node.append(host)}
function restoreDraft(){const thread=threads.get(session);input.value=thread?.draft||'';fitComposer();pendingFiles=thread?.files||[];workQueue=thread?.queue||[];queuePaused=thread?.queuePaused||false;refreshQueue();if(thread){effort.value=thread.effort;agent.checked=thread.agent;mode.value=thread.mode}cwdWrap.hidden=!agent.checked;renderFiles();refreshTerminal();refreshWorkspace();reviewButton.textContent='修改'+(reviewFiles.children.length?' · '+reviewFiles.children.length:'')}
rememberThread();refreshModels(true);
function add(kind,text){const node=document.createElement('div');node.className=(kind==='notice'?'notice':'msg '+kind);node.textContent=text;chat.append(node);chat.scrollTop=chat.scrollHeight;return node}
function eventBlocks(buffer){const blocks=buffer.split('\n\n');return [blocks.slice(0,-1),blocks.at(-1)]}
function clearStatus(){if(statusNode){statusNode.remove();statusNode=null}}
function renderAnswer(node){const raw=node.textContent;node.dataset.message=raw;node.replaceChildren();let code=null;for(const line of raw.split('\n')){if(line.startsWith('```')){if(code){code=null}else{const pre=document.createElement('pre');code=document.createElement('code');pre.append(code);node.append(pre)}continue}if(code){code.textContent+=line+'\n';continue}const heading=line.match(/^(#{1,3})\s+(.*)/);const item=document.createElement(heading?'h'+(heading[1].length+1):'div');const source=heading?heading[2]:line;const tokens=source.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);for(const token of tokens){const element=document.createElement(token.startsWith('`')?'code':token.startsWith('**')?'strong':'span');element.textContent=token.startsWith('`')?token.slice(1,-1):token.startsWith('**')?token.slice(2,-2):token;item.append(element)}if(!source)item.append(document.createElement('br'));node.append(item)}}
function conversationText(){
  if(!chat.querySelector('.user,.assistant,.tool-card'))throw Error('目前對話沒有可匯出的訊息。');
  const lines=['EdgeAgent 對話紀錄','匯出時間：'+new Date().toLocaleString('zh-TW'),'Workspace：'+(cwd.value||'未選擇'),'匯出時模型：'+(modelState?.active_model||'未知'),''];
  for(const node of chat.children){
    if(node.id==='welcome')continue;
    const label=node.classList.contains('user')?'使用者':node.classList.contains('assistant')?'Agent':node.classList.contains('tool-card')?'工具':node.classList.contains('approval-card')?'批准':'狀態';
    const text=node.classList.contains('tool-card')?[...node.children].map(child=>child.textContent).join('\n'):node.dataset.message??node.innerText;
    if(text.trim())lines.push('【'+label+'】',text,'');
  }
  return lines.join('\r\n');
}
document.querySelector('#export-chat').onclick=()=>{
  try{
    const text=conversationText(),blob=new Blob(['\uFEFF',text],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download='EdgeAgent-'+new Date().toISOString().replace(/[-:.]/g,'')+'-'+session.slice(0,8)+'.txt';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
    add('notice','對話 TXT 已送至瀏覽器下載，請在 Edge 的下載清單查看或另存。只有目前對話已收到的文字和工具紀錄；不含未送出的草稿及附件原檔。');
  }catch(error){add('notice','匯出失敗：'+error.message)}
};
agent.addEventListener('change',()=>{cwdWrap.hidden=!agent.checked});
fetch('/config').then(response=>response.json()).then(value=>{effort.value=value.thinking_effort;agent.checked=value.agent;cwdWrap.hidden=!agent.checked}).catch(()=>{});
document.querySelector('#save').addEventListener('click',async()=>{if(busy)return;const response=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thinking_effort:effort.value,agent:agent.checked})});if(!response.ok){add('notice','設定儲存失敗：'+await response.text());return}add('notice','Agent 與 thinking effort 設定已儲存')});
document.querySelector('#shutdown').addEventListener('click',async()=>{if(busy||!confirm('關閉 EdgeAgent 並釋放 GPU 記憶體？'))return;const response=await fetch('/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok){add('notice','關閉失敗：'+await response.text());return}clearTimeout(modelTimer);modelRefreshRequest++;clearTimeout(recordsTimer);recordsDialog.close();send.disabled=true;input.disabled=true;add('notice','EdgeAgent 已關閉；需要時再執行「開啟 EdgeAgent.cmd」')});
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
function openWorkspace(path){if(busy||recording||recordStarting||readingFiles||typeof path!=='string'||!path)return;rememberThread();cwd.value=path;session=crypto.randomUUID();workQueue=[];queuePaused=false;refreshQueue();folderDialog.close();input.value='';fitComposer();pendingFiles=[];renderFiles();refreshTerminal();refreshWorkspace();chat.replaceChildren();reviewFiles.replaceChildren();document.querySelector('#review-empty').hidden=false;add('notice','已開啟 Workspace：'+cwd.value+'；這是新的對話 context');rememberThread()}
document.querySelector('#folder-use').addEventListener('click',()=>openWorkspace(folderPath.dataset.path));
async function approve(payload){
  const details=payload.preview||JSON.stringify(payload.arguments,null,2);
  const accepted=await new Promise(resolve=>{
    const card=document.createElement('section');card.className='approval-card';
    const title=document.createElement('strong');title.textContent='需要批准 · '+payload.tool;
    const pre=document.createElement('pre');pre.textContent=details;card.append(title,pre);
    if(payload.explain){const box=document.createElement('details');box.open=true;const summary=document.createElement('summary');summary.textContent='這個指令在做什麼（PowerShell 自己解析的結果，不是模型猜的）';const text=document.createElement('pre');text.textContent=payload.explain;box.append(summary,text);card.append(box)}
    else if(payload.explain_error){const note=document.createElement('p');note.textContent=payload.explain_error;card.append(note)}
    for(const reason of [payload.explicit==='protected'?'碰到受保護檔案，需要單次批准。':'',payload.explicit==='destructive'?'這個指令會刪除或清除資料，需要單次批准。':'',payload.after_web?'讀過網頁之後的操作，需要重新決定。':''])if(reason){const note=document.createElement('p');note.textContent=reason;card.append(note)}
    const choices=payload.explicit?[['允許一次',true,'once'],['拒絕',false,'once']]:[['允許本輪工作',true,'turn'],['允許一次',true,'once'],['拒絕',false,'once']];
    const stopped=()=>finish({approved:false,scope:'once'});
    function finish(choice){document.querySelector('#stop').removeEventListener('click',stopped);card.replaceChildren(title);title.textContent=choice.approved?'已批准 · '+payload.tool:'已拒絕 · '+payload.tool;resolve(choice)}
    for(const [label,value,scope] of choices){const button=document.createElement('button');button.textContent=label;if(!value)button.className='secondary';button.onclick=()=>finish({approved:value,scope});card.append(button)}
    chat.append(card);card.scrollIntoView({block:'nearest'});document.querySelector('#stop').addEventListener('click',stopped,{once:true});
  });
  const response=await fetch('/chat/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session,call_id:payload.call_id,...accepted})});
  if(!response.ok)throw new Error(await response.text());
}
async function handleEvent(type,payload,answer){
  if(type==='delta'){clearStatus();answer.textContent+=payload.text;chat.scrollTop=chat.scrollHeight}
  else if(type==='status'){clearStatus();statusNode=add('notice',payload.message)}
  else if(type==='compacted'){add('notice',payload.message)}
  else if(type==='approval'){clearStatus();await approve(payload);statusNode=add('notice','等待 Agent 繼續…')}
  else if(type==='tool'){clearStatus();let card=toolCards.get(payload.call_id);if(payload.phase==='running'){card=document.createElement('details');card.className='tool-card';const title=document.createElement('summary');title.textContent='執行中 · '+payload.tool;const pre=document.createElement('pre');pre.textContent=JSON.stringify(payload.arguments,null,2);card.append(title,pre);chat.append(card);toolCards.set(payload.call_id,card)}else if(card){card.querySelector('summary').textContent=(payload.error?'失敗':'已結束')+' · '+payload.tool;card.classList.toggle('tool-error',!!payload.error);const pre=document.createElement('pre');pre.textContent=typeof payload.result==='string'?payload.result:(payload.result?.content?.filter(item=>item.type==='text').map(item=>item.text).join('\n')||JSON.stringify(payload.result,null,2));card.append(pre)}}
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
  busy=true;send.disabled=false;send.textContent='＋';send.setAttribute('aria-label','排入下一輪');document.querySelector('#stop').hidden=false;document.querySelector('#stop').disabled=false;document.querySelector('#welcome')?.remove();if(threads.get(session)?.title==='新對話'){threads.get(session).title=text.slice(0,28);renderThreads()}const userNode=add('user',text+(attachments.length?'\n📎 '+attachments.map(file=>file.name).join('、'):''));showMessageFiles(userNode,attachments);const answer=add('assistant','');
  try{
    const body={session_id:session,text,attachments,thinking_effort:effort.value,agent:agent.checked,mode:mode.value,working_dir:cwd.value.trim()};
    const response=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw new Error(await response.text());
    await consumeStream(response,(type,payload)=>handleEvent(type,payload,answer));
    clearStatus();if(!answer.textContent)answer.remove();else renderAnswer(answer);
  }catch(error){clearStatus();if(answer.textContent)renderAnswer(answer);else answer.remove();add('notice','錯誤：'+error.message);queuePaused=true;if(workQueue.length)add('notice','排隊工作已暫停；檢查進度後可從更多操作選擇「繼續排隊」。')}
  finally{busy=false;send.disabled=modelChanging;send.textContent='↑';send.setAttribute('aria-label','送出');document.querySelector('#stop').hidden=true;rememberThread();input.focus();if(!queuePaused&&workQueue.length){const next=workQueue.shift();ask(next.text,next.attachments)}}
}
document.querySelector('#form').addEventListener('submit',event=>{event.preventDefault();if(readingFiles||recordStarting||recording){add('notice','請等附件讀取完成或停止錄音後再送出');return}if(pendingFiles.some(file=>!mediaSupported(file))){add('notice','附件包含目前模型不支援的格式；請更換模型或移除附件');return}if(modelChanging){add('notice','模型切換中，請稍候再送出');return}const text=input.value.trim()||(pendingFiles.length?'分析附件':'');if(!text)return;const attachments=pendingFiles;pendingFiles=[];input.value='';renderFiles();if(busy){workQueue.push({text,attachments});add('notice','已排入下一輪工作 · 待處理 '+workQueue.length+' 項')}else ask(text,attachments)});
input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229&&!matchMedia('(pointer:coarse)').matches){event.preventDefault();document.querySelector('#form').requestSubmit()}});
document.querySelector('#reset').addEventListener('click',newThread);
</script></body></html>"""
