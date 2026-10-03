// Real Edge CDP interaction checks. No conversation text is persisted.
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
const targets = await (await fetch('http://127.0.0.1:9223/json/list')).json();
const target = targets.find(t => t.type === 'page' && t.url.startsWith('http://127.0.0.1:18087'))
  ?? targets.find(t => t.type === 'page' && t.url === 'about:blank');
const folderFixture=resolve('.test-tmp/folder-picker-fixture');
const missingDirectory=resolve(folderFixture,'missing');
const originalTests=process.env.EDGE_AGENT_TEST_WORKSPACE&&process.env.EDGE_AGENT_EXPECT_SHELL
  ? await readFile(resolve(process.env.EDGE_AGENT_TEST_WORKSPACE,'test_calc.py')) : null;
assert(target, 'EdgeAgent page must be open');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve => socket.addEventListener('open', resolve, {once:true}));
let serial = 0;
const pending = new Map(), errors = [], expectedErrors = [], expectedDirectoryStatuses = [];
const expectedDirectoryRequests = new Set();
socket.addEventListener('message', event => {
  const value = JSON.parse(event.data);
  if (value.id) { pending.get(value.id)?.(value); pending.delete(value.id); }
  else if(value.method==='Network.requestWillBeSent'&&value.params.request.url==='http://127.0.0.1:18087/directories'&&JSON.parse(value.params.request.postData||'{}').path===missingDirectory) expectedDirectoryRequests.add(value.params.requestId);
  else if(value.method==='Network.responseReceived'&&expectedDirectoryRequests.has(value.params.requestId)) expectedDirectoryStatuses.push(value.params.response.status);
  else if(value.method==='Log.entryAdded'&&value.params.entry.source==='network'&&expectedDirectoryRequests.has(value.params.entry.networkRequestId)) expectedErrors.push(value);
  else if (value.method === 'Runtime.exceptionThrown' || (value.method === 'Network.loadingFailed' && !value.params.canceled) || (value.method === 'Log.entryAdded' && value.params.entry.level === 'error') || (value.method === 'Runtime.consoleAPICalled' && value.params.type === 'error')) errors.push(value);
});
const cdp = (method, params={}) => new Promise((resolve,reject) => {
  const id=++serial;
  pending.set(id, v => v.error ? reject(Error(v.error.message)) : resolve(v.result));
  socket.send(JSON.stringify({id,method,params}));
});
const evaluate = async expression => {
  const response=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  assert(!response.exceptionDetails, 'page evaluation failed');
  return response.result.value;
};
const wait = async (expression,timeout=10000) => {
  for(let index=0;index<timeout/100;index++) {
    if(await evaluate(expression)) return;
    await new Promise(r=>setTimeout(r,100));
  }
  console.error(JSON.stringify(errors));
  console.error(await evaluate('({menu:actionsMenu.open,models:modelDialog.open,options:modelSelect.options.length,mainInert:document.querySelector("main").inert,active:document.activeElement.id,modelState:!!modelState,summary:[...Object.values(document.querySelector("#actions-menu>summary").getBoundingClientRect().toJSON())]})'));
  if(await evaluate('!document.querySelector(".user")')){
    const screenshot=await cdp('Page.captureScreenshot',{format:'png'});
    await writeFile('.test-tmp/edge-agent-browser-failure.png',Buffer.from(screenshot.data,'base64'));
  }
  console.error(await evaluate('({title:document.title,threads:document.querySelectorAll(".thread").length,dialog:document.querySelector("#folder-dialog").open,folderError:document.querySelector("#folder-error").textContent,folderRows:document.querySelectorAll(".folder-item").length,viewport:[innerWidth,innerHeight]})'));
  throw Error('Browser state timed out');
};
const click = async selector => {
  const point=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  assert(point, 'click target visible and enabled');
  await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
  await cdp('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...point});
  await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...point});
};
const fill = async (selector,text) => {
  await click(selector);
  await cdp('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,nativeVirtualKeyCode:65,modifiers:2});
  await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,nativeVirtualKeyCode:65,modifiers:2});
  await cdp('Input.insertText',{text});
};
const tap = async selector => {
  await evaluate('Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{})))');
  const point=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)return null;const r=e.getBoundingClientRect();if(!r.width||!r.height)return null;return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  assert(point,'touch target is available');
  await cdp('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...point,radiusX:1,radiusY:1,force:1}]});
  await cdp('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
};
const setViewport = async metrics => {
  await cdp('Emulation.setDeviceMetricsOverride',metrics);
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
};
const choose = async (selector,value) => {
  const options=await evaluate(`[...document.querySelector(${JSON.stringify(selector)}).options].map(option=>option.value)`);
  const index=options.indexOf(value);assert(index>=0,'model option exists');
  await click(selector);
  await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Home',code:'Home',windowsVirtualKeyCode:36});
  await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Home',code:'Home',windowsVirtualKeyCode:36});
  for(let i=0;i<index;i++){await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowDown',code:'ArrowDown',windowsVirtualKeyCode:40})}
  await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(selector)}).value`),value);
};
const waitJob=async(model=null)=>{for(let index=0;index<600;index++){const state=await evaluate('modelState?.job.state');if(state==='done'&&(!model||await evaluate('modelState.active_model==='+JSON.stringify(model))))return;if(state==='failed')throw Error(await evaluate('modelState.job.error'));await new Promise(resolve=>setTimeout(resolve,1000))}throw Error('Model operation timeout')};
try {
  await cdp('Runtime.enable'); await cdp('Page.enable'); await cdp('Network.enable'); await cdp('Log.clear'); await cdp('Log.enable');
  if(target.url==='about:blank')await cdp('Page.navigate',{url:'http://127.0.0.1:18087/'});
  await cdp('Emulation.setFocusEmulationEnabled',{enabled:true});
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  await cdp('Page.bringToFront');
  await evaluate('window.__edgeAgentCheckReload=true');
  await cdp('Page.reload');
  await wait('!window.__edgeAgentCheckReload&&document.readyState==="complete"&&document.querySelectorAll(".thread").length===1');
  assert(await evaluate('!!document.querySelector("#sidebar")'), 'project sidebar');
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'desktop width fits');
  await click('#actions-menu>summary');
  await click('#models-open');
  await wait('document.querySelector("#models-dialog").open&&document.querySelector("#model-select").options.length>0',60000);
  assert(await evaluate('[...document.querySelector("#model-select").options].every(option=>!option.value.includes("lora"))'),'adapters excluded from model picker');
  assert(await evaluate('!!document.querySelector("#model-active").textContent'),'active model displayed');
  assert(await evaluate('Number(document.querySelector("#model-output").value)===modelState.config.max_output_tokens&&document.querySelector("#model-active").textContent.includes("輸出上限 "+modelState.max_output_tokens)'),'separate configured and effective output budgets displayed');
  assert(await evaluate('Number(document.querySelector("#model-working").value)===modelState.config.working_context_size&&document.querySelector("#model-active").textContent.includes("工作目標 "+modelState.working_context_size)'),'working target displayed separately from engine capacity');
  if(process.env.EDGE_AGENT_TEST_MEMORY){
    const original=await evaluate('({...modelState.config})');
    const before=await evaluate('({session,cwd:cwd.value,threads:threads.size})');
    try{
      await fill('#model-working','65536');await click('#model-switch');
      await wait('modelState?.job.action==="switch"');await waitJob(original.model);
      assert(await evaluate('modelState.working_context_size===65536&&modelState.context_size===131072&&modelState.max_output_tokens===8192'),'working target changed without reducing engine/output capacity');
      assert.deepEqual(await evaluate('({session,cwd:cwd.value,threads:threads.size})'),before,'memory setting preserves conversation and workspace');
      console.log('PASS: real UI working-target change, 128K capacity, 8K output and session retention');
    }finally{
      await fill('#model-working',String(original.working_context_size));await click('#model-switch');
      await wait('modelState?.job.state==="running"||modelState?.config.working_context_size==='+original.working_context_size);await waitJob(original.model);
      assert.deepEqual(await evaluate('({...modelState.config})'),original,'memory configuration restored');
    }
  }
  // Exercise polling with a synthetic adapter catalog in RAM; no private
  // adapter is selected, loaded, changed or downloaded.
  await evaluate(`(()=>{const original=window.fetch;window.__restoreModelFetch=()=>{window.fetch=original;delete window.__restoreModelFetch};window.fetch=async(...args)=>{const response=await original(...args);if(args[0]!=='/models')return response;const data=await response.clone().json(),base=data.models.find(item=>item.filename===data.config.model);data.config.lora='fixture-adapter.gguf';data.models.push({filename:'fixture-adapter.gguf',architecture:base.architecture,adapter:true,bytes:1});return new Response(JSON.stringify(data),{status:response.status,headers:response.headers})}})()`);
  try{
    await evaluate('refreshModels(true)');
    await choose('#model-lora','');await evaluate('refreshModels()');
    assert.equal(await evaluate('document.querySelector("#model-lora").value'),'','no-adapter choice survives model polling');
  }finally{await evaluate('window.__restoreModelFetch();refreshModels(true)')}
  await mkdir('.test-tmp',{recursive:true});
  const modelScreenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile('.test-tmp/edge-agent-model-modules.png',Buffer.from(modelScreenshot.data,'base64'));
  await setViewport({width:390,height:320,deviceScaleFactor:1,mobile:false});
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'model dialog fits compact window');
  await click('#models-close');
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  if(process.env.EDGE_AGENT_TEST_ADAPTER_FILE){
    const adapter=process.env.EDGE_AGENT_TEST_ADAPTER_FILE;
    const base=process.env.EDGE_AGENT_TEST_ADAPTER_BASE;
    assert(base,'adapter test requires its compatible base');
    await click('#actions-menu>summary');await click('#models-open');
    await wait('!document.querySelector("#model-select").disabled');
    const original=await evaluate('({...modelState.config})');
    assert.equal(original.lora,'','empty adapter default');
    await choose('#model-select',base);
    assert(await evaluate('[...document.querySelector("#model-lora").options].every(option=>!option.value||modelState.models.some(item=>item.filename===option.value&&item.adapter&&item.architecture===modelState.models.find(model=>model.filename===document.querySelector("#model-select").value).architecture))'),'adapter architecture filtering');
    await choose('#model-lora',adapter);await fill('#model-context','8192');
    await click('#model-switch');await wait('modelState?.job.action==="switch"');await waitJob(base);
    assert.equal(await evaluate('modelState.config.lora'),adapter,'selected adapter loaded');
    console.log('PASS: optional adapter empty default, architecture filtering and real UI loading');
    await choose('#model-lora','');await click('#model-switch');
    await wait('modelState?.job.state==="running"||modelState?.config.lora===""');await waitJob(base);
    assert.equal(await evaluate('modelState.config.lora'),'','adapter removed');
    console.log('PASS: real UI adapter removal');
    await choose('#model-lora',adapter);await choose('#model-select',original.model);
    assert.equal(await evaluate('document.querySelector("#model-lora").value'),'','base change clears old adapter');
    await fill('#model-context',String(original.context_size));await click('#model-switch');
    await wait('modelState?.job.state==="running"||modelState?.active_model==='+JSON.stringify(original.model));await waitJob(original.model);
    assert.deepEqual(await evaluate('({...modelState.config})'),original,'original Agent configuration restored');
    assert((await (await fetch('http://127.0.0.1:18087/status')).json()).ready,'original Agent ready');
    await setViewport({width:390,height:320,deviceScaleFactor:1,mobile:false});
    await click('#model-lora');
    await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'adapter controls fit compact window');
    await click('#models-close');await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
    console.log('PASS: base-change adapter reset, original Agent restoration and compact adapter controls');
  }
  if(process.env.EDGE_AGENT_TEST_MODEL_MODULES){
    await click('#actions-menu>summary');await click('#models-open');
    await fill('#model-repository','unsloth/Qwen3-0.6B-GGUF');
    await click('#model-catalog');
    await wait('!document.querySelector("#model-remote").disabled&&document.querySelector("#model-remote").options.length>0');
    assert(await evaluate('[...document.querySelector("#model-remote").options].some(option=>option.value==="Qwen3-0.6B-Q4_K_M.gguf")'),'Hub GGUF catalog');
    const alreadyInstalled=await evaluate('[...document.querySelector("#model-select").options].some(option=>option.value==="Qwen3-0.6B-Q4_K_M.gguf")');
    if(!alreadyInstalled){await choose('#model-remote','Qwen3-0.6B-Q4_K_M.gguf');await click('#model-download');await wait('modelState?.job.action==="download"');await waitJob();assert(await evaluate('document.querySelector("#model-result").textContent.includes("下載完成")'),'verified download finished');console.log('PASS: real Hub catalog and SHA256-verified GGUF download')}
    const before=await evaluate('({session,cwd:cwd.value,threads:threads.size})');
    const original=await evaluate('modelState.config.model');const originalContext=await evaluate('modelState.config.context_size');
    await choose('#model-select','Qwen3-0.6B-Q4_K_M.gguf');await fill('#model-context','8192');
    await click('#model-switch');await wait('modelState?.job.action==="switch"');await waitJob('Qwen3-0.6B-Q4_K_M.gguf');
    assert(await evaluate('modelState.active_model==="Qwen3-0.6B-Q4_K_M.gguf"&&modelState.context_size===8192'),'replacement model loaded');
    assert.deepEqual(await evaluate('({session,cwd:cwd.value,threads:threads.size})'),before,'hot swap retains conversation and workspace');
    await click('#models-close');
    await click('#agent');await fill('#input','Reply with only OK.');await click('#send');
    for(let index=0;index<180;index++){if(await evaluate('!busy&&completedTurns>=1'))break;await new Promise(resolve=>setTimeout(resolve,1000))}
    assert(await evaluate('!busy&&completedTurns>=1'),'real replacement model inference through existing chat');
    await click('#actions-menu>summary');await click('#models-open');await wait('!document.querySelector("#model-select").disabled');await choose('#model-select',original);await fill('#model-context',String(originalContext));
    await click('#model-switch');await wait('modelState?.job.state==="running"||modelState?.active_model==='+JSON.stringify(original));await waitJob(original);
    assert.equal(await evaluate('modelState.active_model'),original,'original model restored');
    await click('#models-close');await click('#agent');
    console.log('PASS: cross-architecture hot swap, real reply, RAM conversation/workspace retention, and original model restoration');
    await evaluate('window.__edgeAgentCheckReload=true');await cdp('Page.reload');await wait('!window.__edgeAgentCheckReload&&document.readyState==="complete"&&document.querySelectorAll(".thread").length===1');
  }
  console.log('PASS: model dialog, active model, adapter filtering, compact layout');
  const originalPermissions=await evaluate("fetch('/config').then(r=>r.json()).then(c=>c.permissions)");
  await click('#actions-menu>summary');await click('#permissions-open');
  await wait('!!document.querySelector("#permission-discord-workspaces")&&!document.querySelector("#permissions-save").disabled');
  try{
    await click('#permission-discord-enabled');
    await choose('#permission-discord-edit','deny');
    await choose('#permission-ui-shell','allow');
    await fill('#permission-discord-workspaces',folderFixture);
    await click('#permissions-save');
    await wait('document.querySelector("#permissions-result").textContent.includes("已儲存")');
    const saved=await evaluate("fetch('/config').then(r=>r.json()).then(c=>c.permissions)");
    assert.equal(saved.discord.enabled,!originalPermissions.discord.enabled);
    assert.equal(saved.discord.edit,'deny');assert.equal(saved.ui.shell,'allow');
    assert.deepEqual(saved.discord.workspaces,[folderFixture]);
    for(const [width,height] of [[1280,850],[1280,600],[768,1024],[390,844],[360,640],[320,568],[480,360],[844,390]]){
      await setViewport({width,height,deviceScaleFactor:1,mobile:false});
      assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'permissions panel fits viewport');
      await click('#permissions-save');
      assert(await evaluate('document.querySelector("#permissions-save").getBoundingClientRect().bottom<=innerHeight+1'),'permission save reachable');
    }
  }finally{
    await evaluate(`modelPost('/config',{permissions:${JSON.stringify(originalPermissions)}})`);
    await click('#permissions-close');await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  }
  await evaluate("window.__approvalFetch=window.fetch;window.fetch=(url,...args)=>url==='/chat/approve'?Promise.resolve(new Response('{\"accepted\":true}',{status:200})):window.__approvalFetch(url,...args)");
  try{
    for(const explicit of ['protected','destructive']){
      await evaluate(`window.__approvalPromise=approve({tool:'powershell',call_id:'fixture',arguments:{},explicit:${JSON.stringify(explicit)},after_web:true});true`);
      assert.equal(await evaluate('document.querySelectorAll(".approval-card:last-child button").length'),2);
      assert(await evaluate('document.querySelector(".approval-card:last-child").textContent.includes("讀過網頁之後的操作")'));
      await click('.approval-card:last-child button:last-child');await evaluate('window.__approvalPromise');
    }
  }finally{await evaluate('window.fetch=window.__approvalFetch;delete window.__approvalFetch;document.querySelectorAll(".approval-card").forEach(node=>node.remove())')}
  console.log('PASS: permission profile persistence, eight viewports, explicit single approvals and web reminder');
  await click('#new-thread');
  await wait('document.querySelectorAll(".thread").length===2');
  await click('.thread');
  assert(await evaluate('document.querySelectorAll(".thread")[0].classList.contains("active")'), 'thread switching');
  await click('#open-project');
  await wait('document.querySelector("#folder-dialog").open&&document.querySelectorAll(".folder-item").length>0');
  await setViewport({width:1280,height:600,deviceScaleFactor:1,mobile:false});
  assert(await evaluate(`(()=>{const dialog=document.querySelector('#folder-dialog').getBoundingClientRect(),button=document.querySelector('#folder-use').getBoundingClientRect();return button.bottom<=dialog.bottom&&button.bottom<=innerHeight+1})()`), 'folder confirmation stays visible in a short window');
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  await click('#folder-use');
  await wait('document.querySelector("#cwd").value.length>0&&!document.querySelector("#folder-dialog").open');
  assert(await evaluate('document.querySelectorAll(".thread").length===3'), 'new project conversation');
  const folderChild=resolve(folderFixture,'子資料夾');
  await mkdir(folderChild,{recursive:true});
  await click('#browse');
  await wait('!document.querySelector("#folder-use").disabled');
  await fill('#folder-address',folderFixture);
  await click('#folder-address-form button');
  await wait(`document.querySelector('#folder-path').dataset.path===${JSON.stringify(folderFixture)}`);
  const childIndex=await evaluate('[...document.querySelectorAll(".folder-item")].findIndex(node=>node.textContent.includes("子資料夾"))+1');
  assert(childIndex>0,'Unicode child directory is listed');
  await click(`.folder-item:nth-child(${childIndex})`);
  await wait(`document.querySelector('#folder-path').dataset.path===${JSON.stringify(folderChild)}`);
  assert(await evaluate(`document.querySelector('#folder-address').value===${JSON.stringify(folderChild)}`),'address follows child navigation');
  await fill('#folder-address',missingDirectory);
  await click('#folder-address-form button');
  await wait('!document.querySelector("#folder-error").hidden');
  assert(await evaluate('document.querySelector("#folder-use").disabled&&!document.querySelector("#folder-path").dataset.path'),'failed navigation cannot select stale directory');
  await fill('#folder-address',folderChild);
  await click('#folder-address-form button');
  await wait('!document.querySelector("#folder-use").disabled&&document.querySelector("#folder-error").hidden');
  await setViewport({width:480,height:360,deviceScaleFactor:1,mobile:false});
  assert(await evaluate(`(()=>{const dialog=document.querySelector('#folder-dialog').getBoundingClientRect(),button=document.querySelector('#folder-use').getBoundingClientRect();return button.bottom<=dialog.bottom&&button.bottom<=innerHeight+1})()`),'folder confirmation visible in compact short window');
  await click('#folder-use');
  await wait(`!document.querySelector('#folder-dialog').open&&document.querySelector('#cwd').value===${JSON.stringify(folderChild)}`);
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  console.log('PASS: short-window folder selection, Unicode child navigation, inline errors, stale-selection protection and recovery');
  if(process.env.EDGE_AGENT_TEST_WORKSPACE){
    await click('#open-project');
    await wait('document.querySelector("#folder-dialog").open');
    await fill('#folder-address',process.env.EDGE_AGENT_TEST_WORKSPACE);
    await click('#folder-address-form button');
    await wait(`document.querySelector('#folder-path').dataset.path===${JSON.stringify(process.env.EDGE_AGENT_TEST_WORKSPACE)}`);
    await click('#folder-use');
    await wait('!document.querySelector("#worktree-create").disabled');
    const original=await evaluate('document.querySelector("#cwd").value');
    await click('#worktree-create');
    await wait(`document.querySelector('#cwd').value!==${JSON.stringify(original)}`);
    await wait('document.querySelector("#branch-label").textContent.includes("edge-agent/")');
    console.log('PASS: native folder address, Git branch display, real isolated worktree');
  }
  await click('#agent');
  assert(await evaluate('!document.querySelector("#agent").checked'), 'agent toggle');
  await click('#agent');
  await click('#review-toggle');
  assert(await evaluate('!document.querySelector("#review").hidden'), 'change review opens');
  await click('#review-close');
  await click('#terminal-toggle');
  assert(await evaluate('!document.querySelector("#terminal-pane").hidden'), 'terminal opens');
  await click('#terminal-command');
  await cdp('Input.insertText',{text:"$taskNumber=17; Write-Output 'terminal-pass'"});
  await click('#terminal-run');
  await wait('document.querySelector("#terminal-output").textContent.includes("[exit code: 0]")');
  assert(await evaluate('document.querySelector("#terminal-output").textContent.includes("terminal-pass")'), 'terminal stdout');
  await click('#terminal-command');
  await cdp('Input.insertText',{text:'Write-Output ($taskNumber + 1)'});
  await click('#terminal-run');
  await wait('(document.querySelector("#terminal-output").textContent.match(/exit code: 0/g)||[]).length===2');
  assert(await evaluate('document.querySelector("#terminal-output").textContent.includes("\\n18\\n")'), 'persistent terminal variables');
  await click('#terminal-command');
  await cdp('Input.insertText',{text:'Start-Sleep -Seconds 30'});
  await click('#terminal-run');
  await wait('!document.querySelector("#terminal-stop").disabled');
  await click('#terminal-stop');
  await wait('!document.querySelector("#terminal-run").disabled');
  await click('#terminal-clear');
  assert(await evaluate('document.querySelector("#terminal-output").textContent===""'), 'terminal clear');
  await click('#terminal-hide');
  const fixture=resolve('.test-tmp/attachment-fixture.py');
  await writeFile(fixture,'fixture_value = 42\n');
  await cdp('Page.setInterceptFileChooserDialog',{enabled:true});
  await click('#attach');
  await click('#attach-document');
  const document=await cdp('DOM.getDocument');
  const fileNode=await cdp('DOM.querySelector',{nodeId:document.root.nodeId,selector:'#file-input'});
  await cdp('DOM.setFileInputFiles',{nodeId:fileNode.nodeId,files:[fixture]});
  await wait('document.querySelectorAll(".attachment").length===1');
  await click('.attachment button');
  assert(await evaluate('pendingFiles.length===0'), 'attachment removal');
  console.log('PASS: terminal stdout, persistent state, stop, clear, attachments and removal');
  if(process.env.EDGE_AGENT_TEST_MULTIMODAL){
    const original=await evaluate('({...modelState.config})');
    let mediaFailure=null;
    const mediaChecks=[];
    const speech=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',
      'Add-Type -AssemblyName System.Speech; $speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer; $audioStream=New-Object IO.MemoryStream; $speaker.SetOutputToWaveStream($audioStream); $speaker.Speak((6*7).ToString()); $speaker.Dispose(); [Convert]::ToBase64String($audioStream.ToArray()); $audioStream.Dispose()'
    ],{encoding:'utf8',timeout:30000});
    assert.equal(speech.status,0,'synthetic speech created entirely in RAM');
    const spokenAudio=speech.stdout.trim();
    const check=(name,value)=>{assert(value,name);mediaChecks.push(name)};
    const switchModel=async(config)=>{
      await click('#actions-menu>summary');await click('#models-open');
      await wait('!document.querySelector("#model-select").disabled');
      await choose('#model-select',config.model);await choose('#model-lora',config.lora||'');
      await choose('#model-mmproj',config.mmproj||'');
      await fill('#model-output',String(config.max_output_tokens));
      await fill('#model-context',String(config.context_size));await fill('#model-gpu',String(config.gpu_layers));
      if(await evaluate('document.querySelector("#model-low-vram").checked')!==config.low_vram)await click('#model-low-vram');
      await click('#model-switch');await wait('modelState?.job.action==="switch"');await waitJob(config.model);
      await click('#models-close');
    };
    const selectMemoryFile=async(kind)=>{
      await click('#attach');await click('#attach-'+kind);
      // Synthetic media stays in RAM. Exercise the actual input change handler.
      await evaluate(`(async()=>{const kind=${JSON.stringify(kind)},transfer=new DataTransfer();let file;if(kind==='image'){const canvas=document.createElement('canvas');canvas.width=32;canvas.height=32;const context=canvas.getContext('2d');context.fillStyle='red';context.fillRect(0,0,32,32);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));file=new File([blob],'fixture.png',{type:'image/png'})}else{const bytes=Uint8Array.from(atob(${JSON.stringify(spokenAudio)}),char=>char.charCodeAt(0));file=new File([bytes],'fixture.wav',{type:'audio/wav'})}transfer.items.add(file);const field=document.querySelector('#'+kind+'-input');field.files=transfer.files;field.dispatchEvent(new Event('change',{bubbles:true}));})()`);
      await wait('!readingFiles&&pendingFiles.length>0');
    };
    try{
      await click('#attach');
      check('Qwen image enabled',await evaluate('!document.querySelector("#attach-image").disabled'));
      check('Qwen audio and microphone disabled',await evaluate('document.querySelector("#attach-audio").disabled&&document.querySelector("#record").disabled'));
      await click('#attach');
      await switchModel({...original,model:'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf',lora:'',mmproj:'',context_size:131072,gpu_layers:-1});
      check('E4B engine reports both modalities',await evaluate('modelState.capabilities.vision&&modelState.capabilities.audio'));
      check('E4B retains 128K context',await evaluate('modelState.context_size===131072'));
      await click('#new-thread');if(await evaluate('agent.checked'))await click('#agent');
      await selectMemoryFile('image');
      check('Image preview loaded',await evaluate('document.querySelector(".attachment img").naturalWidth===32'));
      await click('.attachment button');check('Image removed',await evaluate('pendingFiles.length===0'));
      await selectMemoryFile('audio');check('Audio file selected',await evaluate('pendingFiles[0].mime==="audio/wav"'));
      await click('.attachment button');
      await click('#record');await wait('!!recording&&recording.length>0');
      check('Recording cannot switch conversations',await evaluate('document.querySelector("#record").getAttribute("aria-pressed")==="true"'));
      await click('#new-thread');check('Recording remains in its conversation',await evaluate('!!recording'));
      const recordingSession=await evaluate('session');
      await evaluate('(()=>{const original=recording.context.close.bind(recording.context);recording.context.close=async()=>{await new Promise(resolve=>window.__releaseRecording=resolve);return original()}})()');
      await click('#record');await wait('!!window.__releaseRecording');await click('#new-thread');
      check('Stopping recording cannot move draft to another conversation',await evaluate('session')===recordingSession);
      await evaluate('window.__releaseRecording();delete window.__releaseRecording');
      await wait('!recording&&!recordStarting&&!readingFiles&&pendingFiles.length===1');
      check('Recording becomes WAV in RAM',await evaluate('pendingFiles[0].mime==="audio/wav"&&document.querySelector("#record").getAttribute("aria-pressed")==="false"'));
      await click('.attachment button');
      await selectMemoryFile('image');await selectMemoryFile('audio');
      assert(process.env.EDGE_AGENT_TEST_MEDIA_PROMPT,'multimodal inference instruction must be supplied in RAM');
      await fill('#input',process.env.EDGE_AGENT_TEST_MEDIA_PROMPT);
      await choose('#effort','off');
      await evaluate('(()=>{const original=handleEvent;handleEvent=(type,payload,answer)=>{if(type==="error"){const text=answer.textContent;window.__mediaFailure={characters:text.length,uniqueCharacters:new Set(text).size,unusedToken:text.includes("<unused"),color:/red|紅/i.test(text),number:/42|四十二/.test(text),effort:effort.value}}return original(type,payload,answer)}})()');
      await click('#send');
      for(let i=0;i<1200;i++){if(await evaluate('!busy&&completedTurns>0'))break;if(await evaluate('queuePaused')){console.error(await evaluate('window.__mediaFailure'));throw Error('E4B media inference failed: '+await evaluate('[...document.querySelectorAll(".assistant")].map(node=>node.textContent).find(text=>text.startsWith("錯誤："))'))}await new Promise(resolve=>setTimeout(resolve,250));if(i===1199)throw Error('E4B media inference timeout')}
      check('E4B mixed image and audio inference completed',await evaluate('completedTurns>0&&!queuePaused&&document.querySelector(".message-media img").naturalWidth===32&&!!document.querySelector(".message-media audio")'));
      check('E4B image color and spoken number recognized',await evaluate('[...document.querySelectorAll(".assistant")].some(node=>/red|紅/i.test(node.textContent)&&/42|四十二/.test(node.textContent))'));
      await click('#new-thread');await selectMemoryFile('image');
      await switchModel({...original,model:'Qwen3-0.6B-Q4_K_M.gguf',lora:'',mmproj:'',context_size:8192});
      await click('#attach');
      check('Text model media controls disabled',await evaluate('document.querySelector("#attach-image").disabled&&document.querySelector("#attach-audio").disabled&&document.querySelector("#record").disabled'));
      check('Document option remains enabled',await evaluate('!document.querySelector("#attach-document").disabled'));
      await click('#attach');await click('#send');
      check('Unsupported draft stays unsent and removable',await evaluate('!busy&&pendingFiles.length===1&&!!document.querySelector(".attachment.unsupported")'));
      await click('.attachment button');
      await switchModel(original);
      check('Original configuration restored',await evaluate('modelState.active_model===modelState.config.model&&modelState.capabilities.vision&&!modelState.capabilities.audio'));
      await mkdir('.test-tmp',{recursive:true});
      await writeFile('.test-tmp/multimodal-report.json',JSON.stringify({event:'multimodal_ui_verification',checks:mediaChecks.map(event=>({event,passed:true})),passed:mediaChecks.length,total:mediaChecks.length}));
      console.log('PASS: multimodal checks '+mediaChecks.length+'/'+mediaChecks.length);
    }catch(error){mediaFailure=error;throw error}finally{
      let cleanupFailure=null;
      try{if(await evaluate('!!recording'))await evaluate('stopRecording(false)')}catch(error){cleanupFailure=error;console.error({event:'recording_cleanup_failed'})}
      try{
        if((await (await fetch('http://127.0.0.1:18087/models')).json()).active_model!==original.model){
          const keys=['model','lora','mmproj','context_size','max_output_tokens','gpu_layers','low_vram'];
          const response=await fetch('http://127.0.0.1:18087/models/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(keys.map(key=>[key,original[key]])))});
          assert(response.ok,'cleanup restore accepted');
          for(let i=0;i<600;i++){const state=await (await fetch('http://127.0.0.1:18087/models')).json();if(state.job.state==='done'&&state.active_model===original.model)break;if(state.job.state==='failed')throw Error('cleanup restore failed');assert(i<599,'cleanup restore timeout');await new Promise(resolve=>setTimeout(resolve,1000))}
        }
        await evaluate('window.__edgeAgentCheckReload=true');await cdp('Page.reload');
        await wait('!window.__edgeAgentCheckReload&&document.readyState==="complete"&&!!modelState?.active_model');
      }catch(error){cleanupFailure??=error;console.error({event:'multimodal_cleanup_failed'})}
      if(!mediaFailure&&cleanupFailure)throw cleanupFailure;
    }
  }
  if(process.env.EDGE_AGENT_TEST_PROMPT) {
    await click('#input');
    await cdp('Input.insertText',{text:process.env.EDGE_AGENT_TEST_PROMPT});
    await click('#send');
    await wait('busy');
    const queued=!!process.env.EDGE_AGENT_QUEUE_PROMPT;
    if(queued){await click('#input');await cdp('Input.insertText',{text:process.env.EDGE_AGENT_QUEUE_PROMPT});await click('#send');assert(await evaluate('workQueue.length===1'),'message queued during work')}
    for(let attempt=0;attempt<1200;attempt++) {
      const state=await evaluate('({busy,approval:!!document.querySelector(".approval-card button"),errors:[...document.querySelectorAll(".assistant")].some(e=>e.textContent.startsWith("錯誤："))})');
      assert(!state.errors,'agent stream succeeds');
      if(state.approval) await click('.approval-card button');
      if(!state.busy && (!queued || await evaluate('completedTurns>=2&&workQueue.length===0'))) break;
      await new Promise(r=>setTimeout(r,100));
      if(attempt===1199) throw Error('Agent UI test timed out');
    }
    assert(await evaluate('document.querySelectorAll(".tool-card").length>0'), 'actual tool cards');
    assert(await evaluate('document.querySelectorAll("#review-files details").length>0'), 'actual change review');
    if(process.env.EDGE_AGENT_EXPECT_SHELL) assert(await evaluate('[...document.querySelectorAll(".tool-card")].some(card=>card.querySelector("summary").textContent.includes("powershell")&&card.textContent.includes("passed"))'), 'actual validation command exits successfully');
    if(originalTests){
      const workspace=await evaluate('cwd.value');
      assert((await readFile(resolve(workspace,'test_calc.py'))).equals(originalTests),'Agent preserves tests');
      const check=spawnSync(resolve('.venv/Scripts/python.exe'),['-m','pytest','-q'],{cwd:workspace,encoding:'utf8'});
      assert.equal(check.status,0,'independent tests pass');
      console.log('PASS: independent verification; tests unchanged');
    }
    console.log('PASS: real model, inline write approval, tool results, change diff, completion');
    if(queued)console.log('PASS: queued follow-up executed after active work');
  }
  for(const [width,height] of [[1920,1080],[1024,768],[768,1024],[390,844],[320,568],[844,390],[390,320],[320,280]]){
    await setViewport({width,height,deviceScaleFactor:1,mobile:false});
    assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'no page overflow at '+width+'×'+height);
    assert(await evaluate(`(()=>{const send=document.querySelector('#send').getBoundingClientRect(),header=document.querySelector('header').getBoundingClientRect();return send.width>0&&send.left>=0&&send.right<=innerWidth+1&&send.bottom<=innerHeight+1&&header.right<=innerWidth+1+1})()`),'composer and header fit at '+width+'×'+height);
    assert(await evaluate(`['#send','#attach','#terminal-toggle','#review-toggle','#actions-menu>summary','#effort','#agent','#mode'].every(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1})`),'every main action remains accessible at '+width+'×'+height);
    await click('#terminal-toggle');
    assert(await evaluate(`['#terminal-run','#terminal-stop','#terminal-hide','#send'].every(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1})`),'terminal fits at '+width+'×'+height);
    await click('#terminal-hide');
    console.log('PASS: viewport '+width+'×'+height);
  }
  await setViewport({width:390,height:844,deviceScaleFactor:2,mobile:true});
  await cdp('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5});
  await wait('matchMedia("(pointer:coarse)").matches');
  await tap('#nav-toggle');
  await wait('document.body.classList.contains("nav-open")');
  assert(await evaluate('document.querySelector("main").inert&&!document.querySelector("#sidebar").inert'),'mobile sidebar contains focus');
  await tap('#new-thread');
  await wait('!document.body.classList.contains("nav-open")');
  await tap('#nav-toggle');
  await tap('#open-project');
  await wait('document.querySelector("#folder-dialog").open&&!document.querySelector("#folder-use").disabled');
  await tap('#folder-use');
  await wait('!document.querySelector("#folder-dialog").open');
  await tap('#review-toggle');
  assert(await evaluate('!document.querySelector("#review").hidden&&document.querySelector("main").inert'),'mobile review covers the workspace');
  await tap('#review-close');
  assert(await evaluate('!document.querySelector("main").inert'),'review restores the workspace');
  await tap('#actions-menu>summary');
  assert(await evaluate('document.querySelector("#actions-menu").open'),'mobile actions menu opens');
  await tap('#reset');
  assert(await evaluate('!document.querySelector("#actions-menu").open'),'menu action closes the menu');
  await tap('#input');
  await cdp('Input.insertText',{text:'viewport-fixture'});
  await cdp('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'});
  await cdp('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  assert(await evaluate('!busy&&document.querySelector("#input").value.includes("\\n")'),'touch Enter creates a new line without submitting');
  assert(await evaluate(`['#send','#attach','#nav-toggle','#review-toggle','#terminal-toggle','#actions-menu>summary'].every(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return r.width>=43.9&&r.height>=43.9})`),'mobile touch targets are at least 44 pixels');
  await setViewport({width:390,height:320,deviceScaleFactor:2,mobile:true});
  await wait('document.body.classList.contains("compact-height")');
  assert(await evaluate(`(()=>{const r=document.querySelector('#send').getBoundingClientRect();return r.bottom<=visualViewport.height&&r.right<=innerWidth+1})()`),'composer fits after available-height reduction');
  console.log('PASS: touch sidebar, folder confirmation, review, actions menu, multiline Enter, 44-pixel targets and keyboard-height adaptation');
  await cdp('Emulation.setTouchEmulationEnabled',{enabled:false});
  await cdp('Emulation.clearDeviceMetricsOverride');
  await evaluate('window.__edgeAgentCheckReload=true');
  await cdp('Page.reload');
  await wait('!window.__edgeAgentCheckReload&&document.readyState==="complete"&&document.querySelectorAll(".thread").length===1');
  await wait('!!modelState?.active_model');
  const screenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile('.test-tmp/edge-agent-ui.png',Buffer.from(screenshot.data,'base64'));
  await setViewport({width:390,height:844,deviceScaleFactor:1,mobile:true});
  await new Promise(resolve=>setTimeout(resolve,300));
  const phoneScreenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile('.test-tmp/edge-agent-mobile-ui.png',Buffer.from(phoneScreenshot.data,'base64'));
  await cdp('Emulation.clearDeviceMetricsOverride');
  if(errors.length) console.error(JSON.stringify(errors));
  assert.deepEqual(expectedDirectoryStatuses,[400],'only the deliberate missing-directory request returns an expected error');
  assert.equal(errors.length,0,'browser errors');
  console.log(JSON.stringify({event:'edge_browser_verification',passed:true,memory_checks:process.env.EDGE_AGENT_TEST_MEMORY?5:1,adapter_checks:process.env.EDGE_AGENT_TEST_ADAPTER_FILE?8:0,viewports:8,unexpected_errors:errors.length,expected_directory_http400:expectedDirectoryStatuses.length,expected_error_logs:expectedErrors.length}));
  console.log('PASS: sidebar, new conversation, conversation switching, project dialog, workspace context, agent toggle, desktop and compact layout; 0 unexpected browser errors');
} finally { socket.close(); }
