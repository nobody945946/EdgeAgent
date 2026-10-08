// The system-prompt sections the host writes per machine (Pi renders each as
// <name>...</name>): today's date and what the model itself is. Per machine,
// not a product version (bench/dataset.py PER_TASK).

// Today's date in this computer's time zone, e.g. "Today is 2026-10-05
// (Monday), time zone Asia/Taipei (UTC+08:00). ..."
export function today(now = new Date()) {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const parts = options => Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: zone, ...options })
    .formatToParts(now).map(({ type, value }) => [type, value]));
  const { year, month, day, weekday } = parts({ year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'long' });
  const offset = parts({ timeZoneName: 'longOffset' }).timeZoneName.replace('GMT', 'UTC');
  return `Today is ${year}-${month}-${day} (${weekday}), time zone ${zone} (${offset === 'UTC' ? 'UTC+00:00' : offset}). `
    + 'Use this date for anything time-sensitive; never assume a date from training data.';
}

// What the model is, where it runs, how its memory works, what it may do and
// which APIs it has, from the host's own settings: without them a small local
// model describes itself as a cloud AI, promises to remember things, probes
// the machine for its hardware and invents a "safety protocol" when asked why
// it keeps asking for approval (owner's conversations, 2026-10-07/08).
const PERMISSION = {
  reversible: '直接進行，每次改動前主機都會備份，主人可以在 EdgeAgent 網頁的「還原」一步步復原',
  allow: '直接進行',
  ask: '每次都要先經主人批准',
  read: '只能讀，不能改',
  deny: '不允許',
};

export function modelFacts(init) {
  const model = String(init.model_name || init.model || '').split(/[\\/]/).pop().replace(/\.gguf$/i, '');
  const system = process.platform === 'win32' ? 'Windows' : process.platform === 'darwin' ? 'macOS' : 'Linux';
  const facts = [
    `你是在這台電腦上用 llama.cpp 執行的本機模型 ${model}，不是雲端服務。`
      + (init.hardware ? `這台電腦：${init.hardware}。` : '')
      + `作業系統是 ${system}，指令用 PowerShell 執行。`,
    `上下文視窗是 ${init.context_size} 個 token；對話太長時，主機會把較早的內容整理成摘要`
      + '（聊天畫面會出現「已整理上下文」的提示），整理前的細節可能不見了，要找回原話用 search_history。'
      + '你看不到 token 用量。你沒有跨對話的記憶：開新對話就是空白的，所以不要答應下次還記得什麼。',
  ];
  const rules = init.permissions ?? {};
  if (init.agent && !init.public) {
    const edit = PERMISSION[rules.edit] ?? PERMISSION.ask, shell = PERMISSION[rules.shell] ?? PERMISSION.ask;
    facts.push('權限（主人在 EdgeAgent 網頁的「權限」設定）：'
      + (edit === shell ? `改檔案和執行指令${edit}。` : `改檔案${edit}；執行指令${shell}。`) + '刪除東西、改系統設定、安裝軟體、送資料出去，'
      + '以及讀過網頁之後執行程式或連網，一律先問主人。主人嫌批准太多，就告訴他可以在那裡調整。');
  }
  const apis = init.public ? [] : (init.apis ?? []);
  if (apis.length) {
    facts.push('主人登錄的 API（金鑰在環境變數裡，用變數名稱引用，不要印出金鑰）：\n' + apis.map(api =>
      `- ${api.name}：${api.purpose}。網址 ${api.base_url}`
      + (api.docs ? `；說明文件 ${api.docs}` : '')
      + (api.key_env ? `；金鑰 $env:${api.key_env}，放在${api.key_in === 'query' ? `查詢參數 ${api.key_name}` : `標頭 ${api.key_name}`}` : '；不用金鑰'))
      .join('\n'));
  }
  return facts.join('\n');
}
