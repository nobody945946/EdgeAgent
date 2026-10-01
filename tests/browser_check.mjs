// Real Edge CDP interaction checks. No conversation text is persisted.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const targets = await (await fetch('http://127.0.0.1:9223/json/list')).json();
const target = targets.find(t => t.type === 'page' && t.url.startsWith('http://127.0.0.1:18087'));
assert(target, 'E4B page must be open');
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
  console.error(await evaluate('({title:document.title,threads:document.querySelectorAll(".thread").length})'));
  throw Error('Browser state timed out');
};
const click = async selector => {
  const point=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)return null;e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  assert(point, 'click target visible and enabled');
  await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
  await cdp('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...point});
  await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...point});
};
try {
  await cdp('Runtime.enable'); await cdp('Page.enable'); await cdp('Network.enable');
  await cdp('Page.bringToFront');
  await cdp('Page.reload');
  await wait('document.querySelectorAll(".thread").length===1');
  assert(await evaluate('!!document.querySelector("#sidebar")'), 'project sidebar');
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth'), 'desktop width fits');
  await click('#new-thread');
  await wait('document.querySelectorAll(".thread").length===2');
  await click('.thread');
  assert(await evaluate('document.querySelectorAll(".thread")[0].classList.contains("active")'), 'thread switching');
  await click('#open-project');
  await wait('document.querySelector("#folder-dialog").open&&document.querySelectorAll(".folder-item").length>0');
  await click('#folder-use');
  await wait('document.querySelector("#cwd").value.length>0&&!document.querySelector("#folder-dialog").open');
  assert(await evaluate('document.querySelectorAll(".thread").length===3'), 'new project conversation');
  if(process.env.E4B_TEST_WORKSPACE){
    await click('#open-project');
    await wait('document.querySelector("#folder-dialog").open');
    await click('#folder-address');
    await cdp('Input.insertText',{text:process.env.E4B_TEST_WORKSPACE});
    await click('#folder-address-form button');
    await wait(`document.querySelector('#folder-path').dataset.path===${JSON.stringify(process.env.E4B_TEST_WORKSPACE)}`);
    await click('#folder-use');
    await wait('!document.querySelector("#worktree-create").disabled');
    const original=await evaluate('document.querySelector("#cwd").value');
    await click('#worktree-create');
    await wait(`document.querySelector('#cwd').value!==${JSON.stringify(original)}`);
    await wait('document.querySelector("#branch-label").textContent.includes("e4b/")');
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
  if(process.env.E4B_TEST_PROMPT) {
    await click('#input');
    await cdp('Input.insertText',{text:process.env.E4B_TEST_PROMPT});
    await click('#send');
    await wait('busy');
    const queued=!!process.env.E4B_QUEUE_PROMPT;
    if(queued){await click('#input');await cdp('Input.insertText',{text:process.env.E4B_QUEUE_PROMPT});await click('#send');assert(await evaluate('workQueue.length===1'),'message queued during work')}
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
    if(process.env.E4B_EXPECT_SHELL) assert(await evaluate('[...document.querySelectorAll(".tool-card")].some(card=>card.querySelector("summary").textContent.includes("exec_shell_command")&&card.textContent.includes("[exit code: 0]"))'), 'actual validation command exits successfully');
    console.log('PASS: real model, inline write approval, tool results, change diff, completion');
    if(queued)console.log('PASS: queued follow-up executed after active work');
  }
  await cdp('Emulation.setDeviceMetricsOverride',{width:480,height:850,deviceScaleFactor:1,mobile:false});
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth'), 'compact width fits');
  await cdp('Emulation.clearDeviceMetricsOverride');
  await cdp('Page.reload');
  await wait('document.querySelectorAll(".thread").length===1');
  const screenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile('.test-tmp/e4b-ui.png',Buffer.from(screenshot.data,'base64'));
  if(errors.length) console.error(JSON.stringify(errors));
  assert.equal(errors.length,0,'browser errors');
  console.log('PASS: sidebar, new conversation, conversation switching, project dialog, workspace context, agent toggle, desktop and compact layout; 0 browser errors');
} finally { socket.close(); }
