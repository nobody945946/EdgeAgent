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

// What the model is and how its memory works, from the host's own settings:
// without it a small local model describes itself as a cloud AI that needs a
// data centre, promises to remember things next time, and says compaction is
// invisible (owner's conversations, 2026-10-07).
export function modelFacts(init) {
  const model = String(init.model_name || init.model || '').split(/[\\/]/).pop().replace(/\.gguf$/i, '');
  return `你是在這台電腦上用 llama.cpp 執行的本機模型 ${model}，不是雲端服務。`
    + `上下文視窗是 ${init.context_size} 個 token；對話太長時，主機會把較早的內容整理成摘要`
    + '（聊天畫面會出現「已整理上下文」的提示），整理前的細節可能不見了。你看不到 token 用量。'
    + '你沒有跨對話的記憶：開新對話就是空白的，所以不要答應下次還記得什麼。';
}
