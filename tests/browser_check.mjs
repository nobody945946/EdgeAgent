// Real Edge CDP interaction checks. No conversation text is persisted.
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile,readdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
const targets = await (await fetch('http://127.0.0.1:9223/json/list')).json();
const appOrigin=new URL(process.env.EDGE_AGENT_BROWSER_URL||'http://127.0.0.1:18087').origin;
const target = targets.find(t => t.type === 'page' && t.url.startsWith(appOrigin))
  ?? targets.find(t => t.type === 'page' && t.url === 'about:blank');
const folderFixture=resolve('.test-tmp/folder-picker-fixture');
const missingDirectory=resolve(folderFixture,'missing');
const originalTests=process.env.EDGE_AGENT_TEST_WORKSPACE&&process.env.EDGE_AGENT_EXPECT_SHELL
  ? await readFile(resolve(process.env.EDGE_AGENT_TEST_WORKSPACE,'test_calc.py')) : null;
assert(target, 'EdgeAgent page must be open');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(resolve => socket.addEventListener('open', resolve, {once:true}));
let serial = 0;
const pending = new Map(), errors = [], expectedErrors = [], expectedDirectoryStatuses = [], chatModes = [];
const requestUrls=[];
const expectedDirectoryRequests = new Set(), expectedRecordRequests = new Set(), expectedRecordStatuses = [];
socket.addEventListener('message', event => {
  const value = JSON.parse(event.data);
  if(value.method==='Network.requestWillBeSent')requestUrls.push(value.params.request.url);
  if (value.id) { pending.get(value.id)?.(value); pending.delete(value.id); }
  else if(value.method==='Network.requestWillBeSent'&&value.params.request.url===appOrigin+'/chat') chatModes.push(JSON.parse(value.params.request.postData||'{}').mode);
  else if(value.method==='Network.requestWillBeSent'&&value.params.request.url===appOrigin+'/directories'&&JSON.parse(value.params.request.postData||'{}').path===missingDirectory) expectedDirectoryRequests.add(value.params.requestId);
  else if(value.method==='Network.requestWillBeSent'&&value.params.request.url===appOrigin+'/records/prepare'&&JSON.parse(value.params.request.postData||'{}').folder===folderFixture) expectedRecordRequests.add(value.params.requestId);
  else if(value.method==='Network.responseReceived'&&expectedRecordRequests.has(value.params.requestId)) expectedRecordStatuses.push(value.params.response.status);
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
async function auditLive(){
  const checks=[];
  const check=async(name,action)=>{try{await action();checks.push({name,status:'passed'});console.log('PASS: '+name)}catch(error){checks.push({name,status:'failed',reason:error.message});console.log('FAIL: '+name+' — '+error.message)}};
  const openMenu=async()=>{if(!await evaluate('actionsMenu.open'))await click('#actions-menu>summary')};
  const openFolder=async path=>{await click('#open-project');await wait('folderDialog.open&&folderList.getAttribute("aria-busy")==="false"');await fill('#folder-address',path);await click('#folder-address-form button');await wait(`!document.querySelector('#folder-use').disabled&&folderPath.dataset.path===${JSON.stringify(path)}`);await click('#folder-use');await wait(`!folderDialog.open&&cwd.value===${JSON.stringify(path)}`)};
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  await cdp('Page.navigate',{url:appOrigin+'/'});await wait('document.readyState==="complete"&&!!modelState?.active_model');
  const original=await evaluate("fetch('/config').then(r=>r.json())");
  const workspace=resolve(folderFixture,'live-ui');await mkdir(workspace,{recursive:true});
  const downloads=resolve(workspace,'downloads');await mkdir(downloads,{recursive:true});
  await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
  await writeFile(resolve(workspace,'probe.txt'),'UI_AUDIT_FIXTURE_42\n');
  let auditSession='';
  try{
    await check('模型清單與目前租用狀態',async()=>{await openMenu();await click('#models-open');await wait('modelDialog.open&&!modelSelect.disabled');assert(await evaluate('!!document.querySelector("#model-active").textContent&&modelSelect.options.length>0'))});
    await check('模型重新掃描取得真實清單',async()=>{await click('#model-refresh');await wait('!modelSelect.disabled');assert(await evaluate('modelState.models.length>0'))});
    await check('租用時切換限制可理解',async()=>{if(await evaluate('modelState.mode==="character"')){assert(await evaluate('document.querySelector("#model-switch").disabled'));assert(await evaluate('document.querySelector("#model-active").textContent.includes("租用中")'))}else assert(await evaluate('!document.querySelector("#model-switch").disabled'))});
    await check('模型庫查詢實際回傳 GGUF 清單',async()=>{await fill('#model-repository','unsloth/Qwen3.5-4B-GGUF');await click('#model-catalog');await wait('document.querySelector("#model-remote").options.length>0',60000);assert(await evaluate('!document.querySelector("#model-download").disabled'))});
    await click('#models-close');
    await check('權限讀取與原值儲存',async()=>{await openMenu();await click('#permissions-open');await wait('!document.querySelector("#permissions-save").disabled');await click('#permissions-save');await wait('document.querySelector("#permissions-result").textContent.includes("已儲存")');assert.deepEqual(await evaluate("fetch('/config').then(r=>r.json()).then(c=>c.permissions)"),original.permissions)});
    await click('#permissions-close');
    await check('紀錄設定及實際狀態',async()=>{await openMenu();await click('#records-open');await wait('!document.querySelector("#records-save").disabled');assert(await evaluate('document.querySelector("#records-status").textContent.length>0'))});
    await click('#records-close');
    await check('手動對話 TXT 匯出入口',async()=>assert(await evaluate('!!document.querySelector("#export-chat")')));
    await check('專案資料夾選擇與 Workspace 生效',async()=>{await openFolder(workspace);assert.equal(await evaluate('cwd.value'),workspace)});
    auditSession=await evaluate('session');
    await check('非 Git 專案的分支入口',async()=>{await wait('document.querySelector("header h1").textContent==="live-ui"');assert(await evaluate('document.querySelector("#worktree-create").disabled'))});
    await check('終端真的執行並回傳結果',async()=>{await click('#terminal-toggle');await fill('#terminal-command',"$auditValue=41; Write-Output ($auditValue+1)");await click('#terminal-run');await wait('terminalOutput.textContent.includes("[exit code: 0]")');assert(await evaluate('terminalOutput.textContent.includes("\\n42\\n")'))});
    await check('終端保留狀態',async()=>{await fill('#terminal-command','Write-Output $auditValue');await click('#terminal-run');await wait('(terminalOutput.textContent.match(/exit code: 0/g)||[]).length===2');assert(await evaluate('terminalOutput.textContent.includes("\\n41\\n")'))});
    await check('終端停止長操作',async()=>{await fill('#terminal-command','Start-Sleep -Seconds 30');await click('#terminal-run');await wait('!document.querySelector("#terminal-stop").disabled');await click('#terminal-stop');await wait('!document.querySelector("#terminal-run").disabled')});
    await check('終端清除及收起',async()=>{await click('#terminal-clear');assert.equal(await evaluate('terminalOutput.textContent'),'');await click('#terminal-hide');assert(await evaluate('terminalPane.hidden'))});
    await check('文件附件加入及移除',async()=>{await cdp('Page.setInterceptFileChooserDialog',{enabled:true});await click('#attach');await click('#attach-document');const doc=await cdp('DOM.getDocument');const node=await cdp('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#file-input'});await cdp('DOM.setFileInputFiles',{nodeId:node.nodeId,files:[resolve(workspace,'probe.txt')]});await wait('pendingFiles.length===1');await click('.attachment button');assert.equal(await evaluate('pendingFiles.length'),0)});
    await check('圖片音訊入口對應實際模型能力',async()=>{assert(await evaluate('document.querySelector("#attach-image").disabled===!modelState.capabilities.vision&&document.querySelector("#attach-audio").disabled===!modelState.capabilities.audio'))});
    await check('三種模式能切換',async()=>{for(const value of ['interview','discussion','work'])await choose('#mode',value)});
    await check('新對話及切回恢復草稿與 Workspace',async()=>{await fill('#input','UI_AUDIT_UNSENT_DRAFT');const id=await evaluate('session');await click('#new-thread');assert.notEqual(await evaluate('session'),id);const index=await evaluate(`[...threads.keys()].indexOf(${JSON.stringify(id)})+1`);await click('.thread:nth-child('+index+')');assert.equal(await evaluate('input.value'),'UI_AUDIT_UNSENT_DRAFT');assert.equal(await evaluate('cwd.value'),workspace);await fill('#input','')});
    await check('Agent 讀檔、工具結果與完成狀態',async()=>{await fill('#input','Use the read tool to read probe.txt. Return only its first line. Do not modify files.');await click('#send');await wait('!busy&&completedTurns>0',120000);assert(await evaluate('[...chat.querySelectorAll(".tool-card")].some(card=>card.querySelector("summary").textContent.includes("read")&&card.textContent.includes("UI_AUDIT_FIXTURE_42"))'))});
    await check('工具紀錄展開顯示參數與結果',async()=>{await click('.tool-card summary');assert(await evaluate('document.querySelector(".tool-card").open&&document.querySelectorAll(".tool-card pre").length>=2'))});
    await check('目前合成測試對話實際匯出 TXT',async()=>{
      const before=new Set(await readdir(downloads));await openMenu();await click('#export-chat');
      let filename;
      for(let index=0;index<100;index++){
        filename=(await readdir(downloads)).find(name=>name.endsWith('.txt')&&!before.has(name));
        if(filename)break;await new Promise(resolve=>setTimeout(resolve,100));
      }
      assert(filename,'TXT download completed');
      const bytes=await readFile(resolve(downloads,filename));
      assert.deepEqual([...bytes.subarray(0,3)],[0xef,0xbb,0xbf],'UTF-8 BOM');
      const text=bytes.toString('utf8');
      assert(text.includes('UI_AUDIT_FIXTURE_42')&&text.includes('【使用者】')&&text.includes('【工具】'),'current conversation and tool result exported');
      assert(!text.includes('UI_AUDIT_UNSENT_DRAFT'),'unsent draft excluded');
    });
    await check('修改審閱顯示真實空狀態',async()=>{await click('#review-toggle');assert(await evaluate('!review.hidden&&!document.querySelector("#review-empty").hidden'));await click('#review-close')});
    await check('一般設定按鈕實際儲存',async()=>{await openMenu();await click('#save');await wait('[...chat.querySelectorAll(".notice")].some(node=>node.textContent.includes("設定已儲存"))');const saved=await evaluate("fetch('/config').then(r=>r.json())");assert.equal(saved.agent,original.agent);assert.equal(saved.thinking_effort,original.thinking_effort)});
    await check('窄視窗側欄及更多操作可達',async()=>{await setViewport({width:360,height:640,deviceScaleFactor:1,mobile:true});await click('#nav-toggle');assert(await evaluate('document.body.classList.contains("nav-open")'));await click('#nav-close');await openMenu();assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'));await click('#actions-menu>summary');await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false})});
    await check('瀏覽器沒有非預期錯誤',async()=>assert.equal(errors.length,0));
  }finally{
    if(await evaluate('busy')){await click('#stop');await wait('!busy',30000)}
    if(auditSession)await evaluate(`modelPost('/chat/reset',{session_id:${JSON.stringify(auditSession)}})`);
    assert.deepEqual(await evaluate("fetch('/config').then(r=>r.json())"),original,'audit leaves user settings unchanged');
    await cdp('Browser.setDownloadBehavior',{behavior:'default'});
  }
  const result={event:'live_ui_audit',passed:checks.filter(item=>item.status==='passed').length,total:checks.length,checks,untested:['模型實際下載及切換（保留正式租用）','麥克風錄音（headless 無真人音訊）','關閉正式服務（保留 SilentTalk）','Agent 寫檔與審閱差異（本輪只有唯讀工作）','工作佇列實際連續執行']};
  await writeFile('.test-tmp/edge-agent-live-ui-audit.json',JSON.stringify(result,null,2));
  await writeFile('.test-tmp/edge-agent-live-ui-audit.md',checks.map(item=>`${item.status==='passed'?'PASS':'FAIL'} ${item.name}${item.reason?' — '+item.reason:''}`).join('\n')+'\n未測：'+result.untested.join('；'));
  console.log(JSON.stringify(result));
  assert.equal(result.passed,result.total,'all live UI inventory items');
}
async function checkRecordingSettings(){
  await mkdir(folderFixture,{recursive:true});
  const originalRecords=await (await fetch(appOrigin+'/records')).json();
  if(!await evaluate('actionsMenu.open'))await click('#actions-menu>summary');await click('#records-open');
  await wait('document.querySelector("#records-dialog").open&&!document.querySelector("#records-save").disabled');
  assert.equal(await evaluate('document.querySelector("#records-status").textContent'),originalRecords.status);
  try{
    await fill('#records-folder',folderFixture);await click('#records-prepare');
    await wait('document.querySelector("#records-result").textContent.includes("已設為紀錄資料夾")');
    if(!await evaluate('document.querySelector("#records-enabled").checked'))await click('#records-enabled');
    await click('#records-save');await wait('document.querySelector("#records-status").textContent==="記錄中"');
    assert.equal((await (await fetch(appOrigin+'/records')).json()).record_folder,folderFixture);
    await fill('#records-folder',missingDirectory);await click('#records-save');
    await wait('document.querySelector("#records-status").textContent.startsWith("暫停")');
    await fill('#records-folder',folderFixture);await click('#records-save');
    await wait('document.querySelector("#records-status").textContent==="記錄中"');
    await setViewport({width:390,height:320,deviceScaleFactor:1,mobile:false});
    assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'records dialog fits compact viewport');
    const recordsScreenshot=await cdp('Page.captureScreenshot',{format:'png'});
    await writeFile('.test-tmp/edge-agent-records.png',Buffer.from(recordsScreenshot.data,'base64'));
  }finally{
    await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
    await fill('#records-folder',originalRecords.record_folder);
    if(await evaluate('document.querySelector("#records-enabled").checked')!==originalRecords.record_sessions)await click('#records-enabled');
    await click('#records-save');await wait('document.querySelector("#records-status").textContent==='+JSON.stringify(originalRecords.status));
    assert.deepEqual(await (await fetch(appOrigin+'/records')).json(),originalRecords,'records configuration restored');
    await click('#records-close');
  }
  console.log('PASS: records dialog, local SSD registration, save, unavailable-folder pause, resume, compact layout and restoration');
  assert.deepEqual(expectedRecordStatuses,[200],'local SSD folder registration succeeds');
}
async function uiRegression(){
  const checks=[];
  let workspace=process.env.EDGE_AGENT_TEST_WORKSPACE;const downloads=process.env.EDGE_AGENT_TEST_DOWNLOADS;
  assert(workspace&&downloads,'isolated workspace and download folder required');
  await cdp('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  await cdp('Page.navigate',{url:appOrigin+'/'});await wait('document.readyState==="complete"&&!!modelState?.active_model');
  await checkRecordingSettings();checks.push('local SSD recording setup, pause, resume and settings restoration');
  const menu=async()=>{if(!await evaluate('actionsMenu.open'))await click('#actions-menu>summary')};
  await menu();await click('#export-chat');
  assert(await evaluate('chat.textContent.includes("沒有可匯出的訊息")'),'empty export explains why');
  assert.deepEqual(await readdir(downloads),[],'empty export makes no file');
  checks.push('empty export explains why and makes no file');
  await click('#open-project');await wait('folderDialog.open&&folderList.getAttribute("aria-busy")==="false"');await fill('#folder-address',workspace);await click('#folder-address-form button');await wait(`!document.querySelector('#folder-use').disabled&&folderPath.dataset.path===${JSON.stringify(workspace)}`);await click('#folder-use');await wait(`!folderDialog.open&&cwd.value===${JSON.stringify(workspace)}`);
  await wait('!worktreeButton.disabled');const originalWorkspace=workspace;await click('#worktree-create');await wait(`cwd.value!==${JSON.stringify(originalWorkspace)}&&!worktreeButton.disabled`);workspace=await evaluate('cwd.value');assert(await evaluate('branchLabel.textContent.includes("edge-agent/")'));checks.push('actual isolated Git worktree through UI');
  await fill('#input','UI_FIRST_ONLY');await click('#send');
  await wait('!!document.querySelector(".approval-card button")',30000);await click('.approval-card button:nth-of-type(2)');
  await wait('!busy&&completedTurns===1',30000);
  assert(await evaluate('document.querySelector(".tool-error summary").textContent.includes("失敗 · read")'),'native failed read is displayed as failed');
  checks.push('native tool errors displayed');
  await click('#review-toggle');assert(await evaluate('reviewFiles.children.length===1&&reviewFiles.textContent.includes("UI_FILE_42")'),'approved write is in review');await click('#review-close');
  const output=await readFile(resolve(workspace,'created.txt'),'utf8');assert.equal(output,'UI_FILE_42\n','approved tool really wrote file');
  checks.push('approved write and review match actual file');
  let names=[];
  const download=async()=>{const before=await readdir(downloads);await menu();await click('#export-chat');for(let i=0;i<100;i++){names=(await readdir(downloads)).filter(name=>name.endsWith('.txt')&&!before.includes(name));if(names.length)break;await new Promise(resolve=>setTimeout(resolve,100))}assert.equal(names.length,1,'actual TXT download completes');return readFile(resolve(downloads,names[0]))};
  await fill('#input','UI_UNSENT_DRAFT_SECRET');
  const first=await download();assert.deepEqual([...first.subarray(0,3)],[239,187,191],'UTF-8 BOM for Windows');
  checks.push('actual UTF-8 TXT download');
  const text=first.toString('utf8');assert(text.includes('UI_FIRST_ONLY')&&text.includes('UI_FILE_42')&&text.includes('失敗 · read')&&text.includes('not-here.txt'),'conversation, arguments, results and failures exported');
  checks.push('messages, tool arguments, results and errors included');
  assert(text.includes('# UI_MARKDOWN\n繁體第一段\n\n```text\nline_a\nline_b\n```'),'original Markdown and newlines preserved');
  checks.push('original Markdown, Traditional Chinese and newlines preserved');
  assert(!text.includes('UI_UNSENT_DRAFT_SECRET'),'unsent draft excluded');
  checks.push('unsent draft excluded');
  const id=await evaluate('session');await click('#new-thread');await fill('#input','UI_SECOND_ONLY');await click('#send');await wait('busy');await fill('#input','UI_THIRD_QUEUED');await click('#send');await wait('!busy&&queuePaused&&workQueue.length===1',30000);
  assert(await evaluate('chat.textContent.includes("錯誤：")&&!!document.querySelector(".assistant")'),'partial reply survives backend output-length error');
  checks.push('partial response survives backend error');
  const second=(await download()).toString('utf8');assert(second.includes('UI_SECOND_ONLY')&&!second.includes('UI_FIRST_ONLY')&&!second.includes('UI_FILE_42'),'export includes only selected conversation');
  assert(!second.includes('UI_THIRD_QUEUED'),'queued but unsent content excluded');
  checks.push('conversation exports are isolated');
  assert.equal(chatModes.length,2,'failed turn does not automatically run queued work');await menu();await click('#queue-resume');await wait('!busy&&!queuePaused&&workQueue.length===0',30000);assert(await evaluate('chat.textContent.includes("UI_THIRD_QUEUED")'));checks.push('failed work pauses queue; resume actually runs queued work');
  const index=await evaluate(`[...threads.keys()].indexOf(${JSON.stringify(id)})+1`);await click('.thread:nth-child('+index+')');
  const restored=(await download()).toString('utf8');assert(restored.includes('UI_FIRST_ONLY')&&!restored.includes('UI_SECOND_ONLY'),'switching back exports correct conversation');
  checks.push('switching back exports correct conversation');
  assert.equal(chatModes.length,3,'export never invokes model or tools');checks.push('export makes no model requests');
  assert.equal(errors.length,0,'browser errors');checks.push('no unexpected browser errors');
  await setViewport({width:360,height:640,deviceScaleFactor:1,mobile:false});await menu();
  assert(await evaluate('document.querySelector("#export-chat").getBoundingClientRect().right<=innerWidth+1'),'export fits compact viewport');
  checks.push('export reachable in compact viewport');
  await writeFile('.test-tmp/edge-agent-export-menu.png',Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  const acceptDialog=event=>{if(JSON.parse(event.data).method==='Page.javascriptDialogOpening')void cdp('Page.handleJavaScriptDialog',{accept:true})};socket.addEventListener('message',acceptDialog);
  await menu();await click('#shutdown');await wait('input.disabled&&chat.textContent.includes("EdgeAgent 已關閉")');socket.removeEventListener('message',acceptDialog);
  const afterShutdown=requestUrls.length;await new Promise(resolve=>setTimeout(resolve,3500));assert.equal(requestUrls.length,afterShutdown,'shutdown stops polling a stopped service');assert.equal(errors.length,0);checks.push('actual shutdown and no further polling');
  console.log(JSON.stringify({event:'ui_export_regression',passed:checks.length,total:checks.length,checks,downloads:3,unexpected_errors:errors.length,model_requests:chatModes.length,fixture_model:true}));
}
try {
  await cdp('Runtime.enable'); await cdp('Page.enable'); await cdp('Network.enable'); await cdp('Log.clear'); await cdp('Log.enable');
    if(target.url==='about:blank')await cdp('Page.navigate',{url:appOrigin+'/'});
    if(process.argv.includes('--ui-regression')) {
      await uiRegression();
    } else if(process.argv.includes('--audit-live')) {
      await auditLive();
    } else if(process.argv.includes('--lease-status')) {
      await cdp('Page.navigate',{url:appOrigin+'/'});
      await wait('document.readyState==="complete"&&!!modelState?.active_model');
      await click('#actions-menu>summary');await click('#models-open');
      await wait('modelDialog.open&&modelState?.mode==="character"');
      const label=await evaluate('document.querySelector("#model-active").textContent');
      const state=await evaluate('({active:modelState.active_model,lora:modelState.active_lora,stored:modelState.config.model})');
      assert(label.includes('Agent 自動共用'),'lease sharing explained');
      assert(label.includes(state.active)&&state.active!==state.stored,'leased model differs from standalone selection');
      assert(label.includes('LoRA '+(state.lora||'不使用')),'effective adapter displayed');
      assert(label.includes('歸還後恢復 '+state.stored),'restoration model displayed');
      assert(await evaluate('document.querySelector("#model-switch").disabled'),'manual switch disabled during lease');
      await setViewport({width:390,height:320,deviceScaleFactor:1,mobile:false});
      assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'lease status fits compact viewport');
      await writeFile('.test-tmp/edge-agent-lease-status.png',Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
      assert.equal(errors.length,0,'lease UI browser errors');
      console.log(JSON.stringify({event:'lease_status_browser',passed:6,total:6,unexpected_errors:errors.length}));
    } else {
  await cdp('Emulation.setFocusEmulationEnabled',{enabled:true});
  await setViewport({width:1280,height:850,deviceScaleFactor:1,mobile:false});
  await cdp('Page.bringToFront');
  await evaluate('window.__edgeAgentCheckReload=true');
  await cdp('Page.reload');
  await wait('!window.__edgeAgentCheckReload&&document.readyState==="complete"&&document.querySelectorAll(".thread").length===1');
  assert(await evaluate('!!document.querySelector("#sidebar")'), 'project sidebar');
  assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'desktop width fits');
  await checkRecordingSettings();
  assert.deepEqual(await evaluate('[...document.querySelector("#mode").options].map(option=>[option.value,option.textContent])'),[['work','工作'],['interview','求知儀式'],['discussion','推演儀式']],'the work, interview and discussion modes exist');
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
    assert((await (await fetch(appOrigin+'/status')).json()).ready,'original Agent ready');
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
    await choose('#permission-discord-shell','routine');
    await choose('#permission-ui-shell','allow');
    await fill('#permission-discord-workspaces',folderFixture);
    await click('#permissions-save');
    await wait('document.querySelector("#permissions-result").textContent.includes("已儲存")');
    const saved=await evaluate("fetch('/config').then(r=>r.json()).then(c=>c.permissions)");
    assert.equal(saved.discord.enabled,!originalPermissions.discord.enabled);
    assert.equal(saved.discord.edit,'deny');assert.equal(saved.ui.shell,'allow');
    assert.equal(saved.discord.shell,'routine');
    assert.deepEqual(saved.discord.workspaces,[folderFixture]);
    for(const [width,height] of [[1280,850],[1280,600],[768,1024],[390,844],[360,640],[320,568],[480,360],[844,390]]){
      await setViewport({width,height,deviceScaleFactor:1,mobile:false});
      assert(await evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'permissions panel fits viewport');
      await click('#permissions-save');
      assert(await evaluate('document.querySelector("#permissions-save").getBoundingClientRect().bottom<=innerHeight+1'),'permission save reachable');
    }
    await setViewport({width:360,height:640,deviceScaleFactor:1,mobile:false});
    await writeFile('.test-tmp/edge-agent-routine-permissions.png',Buffer.from((await cdp('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('PASS: routine shell permission saved and restored; eight permission panel viewports');
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
    // A shell command's approval shows EdgeAgent's own reading of it (edge_agent/lingua), or why not.
    await evaluate(`window.__approvalPromise=approve({tool:'powershell',call_id:'fixture',arguments:{command:'Remove-Item x'},explain:'影響：會刪除：x'});true`);
    assert(await evaluate('document.querySelector(".approval-card:last-child details[open] summary").textContent.includes("這個指令在做什麼")'),'approval explains the command');
    assert(await evaluate('document.querySelector(".approval-card:last-child details pre").textContent==="影響：會刪除：x"'),'explanation shown as it is');
    await click('.approval-card:last-child button:last-child');await evaluate('window.__approvalPromise');
    await evaluate(`window.__approvalPromise=approve({tool:'powershell',call_id:'fixture',arguments:{command:'x'},explain_error:'無法翻譯這個指令：找不到 PowerShell'});true`);
    assert(await evaluate('!document.querySelector(".approval-card:last-child details")&&document.querySelector(".approval-card:last-child").textContent.includes("無法翻譯這個指令")'),'a failed explanation is only a note');
    await click('.approval-card:last-child button:last-child');await evaluate('window.__approvalPromise');
  }finally{await evaluate('window.fetch=window.__approvalFetch;delete window.__approvalFetch;document.querySelectorAll(".approval-card").forEach(node=>node.remove())')}
  console.log('PASS: permission profile persistence, eight viewports, explicit single approvals, web reminder, command explanations');
  await click('#new-thread');
  await wait('document.querySelectorAll(".thread").length===2');
  await click('.thread');
  assert(await evaluate('document.querySelectorAll(".thread")[0].classList.contains("active")'), 'thread switching');
  await click('#open-project');
  await wait('document.querySelector("#folder-dialog").open&&folderList.getAttribute("aria-busy")==="false"&&document.querySelectorAll(".folder-item").length>0');
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
  const interviewRequestStart=chatModes.length;
  await choose('#mode','interview');
  await fill('#input','做一個記帳小工具');await click('#send');
  await wait('busy');
  for(let index=0;index<100&&chatModes.length===interviewRequestStart;index++)await new Promise(resolve=>setTimeout(resolve,100));
  assert.deepEqual(chatModes.slice(interviewRequestStart),['interview'],'the real /chat request carries mode:interview');
  // Wait for the stream to start before exercising cancellation; receiving
  // the HTTP request alone does not mean Pi has opened the session yet.
  await wait('!busy||document.querySelector(".assistant")?.textContent.length>0',90000);
  if(await evaluate('busy')){await click('#stop');await wait('!busy',30000)}
  await choose('#mode','work');
  await click('#actions-menu>summary');await click('#reset');
  assert.equal(await evaluate('document.querySelectorAll(".user,.assistant,.tool-card").length'),0,'interview history cleared before work checks');
  console.log('PASS: three modes and an actual mode:interview HTTP request; interview history cleared');
  if(process.env.EDGE_AGENT_TEST_WORKSPACE){
    await click('#open-project');
    await wait('folderDialog.open&&folderList.getAttribute("aria-busy")==="false"');
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
        if((await (await fetch(appOrigin+'/models')).json()).active_model!==original.model){
          const keys=['model','lora','mmproj','context_size','max_output_tokens','gpu_layers','low_vram'];
          const response=await fetch(appOrigin+'/models/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(keys.map(key=>[key,original[key]])))});
          assert(response.ok,'cleanup restore accepted');
          for(let i=0;i<600;i++){const state=await (await fetch(appOrigin+'/models')).json();if(state.job.state==='done'&&state.active_model===original.model)break;if(state.job.state==='failed')throw Error('cleanup restore failed');assert(i<599,'cleanup restore timeout');await new Promise(resolve=>setTimeout(resolve,1000))}
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
    }
} finally { socket.close(); }
