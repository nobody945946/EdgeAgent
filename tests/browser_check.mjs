// Real Edge CDP interaction checks. No conversation text is persisted.
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
const targets = await (await fetch('http://127.0.0.1:9223/json/list')).json();
const target = targets.find(t => t.type === 'page' && t.url.startsWith('http://127.0.0.1:18087'));
const originalTests=process.env.EDGE_AGENT_TEST_WORKSPACE&&process.env.EDGE_AGENT_EXPECT_SHELL
  ? await readFile(resolve(process.env.EDGE_AGENT_TEST_WORKSPACE,'test_calc.py')) : null;
assert(target, 'EdgeAgent page must be open');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve => socket.addEventListener('open', resolve, {once:true}));
let serial = 0;
const pending = new Map(), errors = [];
socket.addEventListener('message', event => {
  const value = JSON.parse(event.data);
  if (value.id) { pending.get(value.id)?.(value); pending.delete(value.id); }
  else if (value.method === 'Runtime.exceptionThrown' || (value.method === 'Network.loadingFailed' && !value.params.canceled)) errors.push(value);
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
const wait = async expression => {
  for(let index=0;index<100;index++) {
    if(await evaluate(expression)) return;
    await new Promise(r=>setTimeout(r,100));
  }
  console.error(JSON.stringify(errors));
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
try {
  await cdp('Runtime.enable'); await cdp('Page.enable'); await cdp('Network.enable');
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
  await wait('document.querySelector("#models-dialog").open&&document.querySelector("#model-select").options.length>0');
  assert(await evaluate('[...document.querySelector("#model-select").options].every(option=>!option.value.includes("lora"))'),'adapters excluded from model picker');
  assert(await evaluate('!!document.querySelector("#model-active").textContent'),'active model displayed');
  await mkdir('.test-tmp',{recursive:true});
  const modelScreenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile('.test-tmp/edge-agent-model-modules.png',Buffer.from(modelScreenshot.data,'base64'));
  await setViewport({width:390,height:320,deviceScaleFactor:1,mobile:false});
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'model dialog fits compact window');
  await click('#models-close');
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  if(process.env.EDGE_AGENT_TEST_MODEL_MODULES){
    await click('#actions-menu>summary');await click('#models-open');
    await fill('#model-repository','unsloth/Qwen3-0.6B-GGUF');
    await click('#model-catalog');
    await wait('!document.querySelector("#model-remote").disabled&&document.querySelector("#model-remote").options.length>0');
    assert(await evaluate('[...document.querySelector("#model-remote").options].some(option=>option.value==="Qwen3-0.6B-Q4_K_M.gguf")'),'Hub GGUF catalog');
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
  const folderFixture=resolve('.test-tmp/folder-picker-fixture');
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
  await fill('#folder-address',resolve(folderFixture,'missing'));
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
  const document=await cdp('DOM.getDocument');
  const fileNode=await cdp('DOM.querySelector',{nodeId:document.root.nodeId,selector:'#file-input'});
  await cdp('DOM.setFileInputFiles',{nodeId:fileNode.nodeId,files:[fixture]});
  await wait('document.querySelectorAll(".attachment").length===1');
  await click('.attachment');
  assert(await evaluate('pendingFiles.length===0'), 'attachment removal');
  console.log('PASS: terminal stdout, persistent state, stop, clear, attachments and removal');
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
  const phoneScreenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile('.test-tmp/edge-agent-mobile-ui.png',Buffer.from(phoneScreenshot.data,'base64'));
  await cdp('Emulation.clearDeviceMetricsOverride');
  if(errors.length) console.error(JSON.stringify(errors));
  assert.equal(errors.length,0,'browser errors');
  console.log('PASS: sidebar, new conversation, conversation switching, project dialog, workspace context, agent toggle, desktop and compact layout; 0 browser errors');
} finally { socket.close(); }
