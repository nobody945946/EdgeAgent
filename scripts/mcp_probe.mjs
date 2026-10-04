// Inspect MCP annotations before enabling a server. Calls share one connection.
// node scripts/mcp_probe.mjs [--measure-idle] [--call NAME JSON ...] -- COMMAND ARGS
import { spawn, spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline';

let args = process.argv.slice(2), measureIdle = false;
const calls = [];
while (args[0] !== '--') {
  if (args[0] === '--measure-idle') { measureIdle = true; args = args.slice(1); }
  else if (args[0] === '--call' && args.length >= 3) {
    calls.push([args[1], JSON.parse(args[2])]); args = args.slice(3);
  } else throw Error('Usage: [--measure-idle] [--call NAME JSON ...] -- COMMAND ARGS');
}
args = args.slice(1);
if (!args[0]) throw Error('Server command required');
const proc = spawn(args[0], args.slice(1), {
  stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
  // Bare commands may resolve to Windows .cmd shims (for example npx).
  shell: process.platform === 'win32' && !/\.(exe|com)$/i.test(args[0]),
});
const waiting = new Map();
let errTail = '', failure = null;
const failPending = error => {
  failure = error;
  for (const item of waiting.values()) { clearTimeout(item.timer); item.reject(error); }
  waiting.clear();
};
proc.on('error', failPending);
proc.on('exit', code => failPending(Error(`server exited (${code}): ${errTail}`)));
proc.stdin.on('error', failPending);
proc.stderr.on('data', d => { errTail = (errTail + d).slice(-2000); });
const lines = createInterface({ input: proc.stdout });
lines.on('line', line => {
  let message;
  try { message = JSON.parse(line); } catch { return; }
  const item = waiting.get(message.id);
  if (!item) return;
  waiting.delete(message.id); clearTimeout(item.timer);
  if (message.error) item.reject(Error(JSON.stringify(message.error)));
  else item.resolve(message.result);
});
const rpc = (id, method, params) => new Promise((resolve, reject) => {
  if (failure) { reject(failure); return; }
  const timer = setTimeout(() => {
    waiting.delete(id); reject(Error(`timeout ${method}: ${errTail}`));
  }, 180000);
  waiting.set(id, { resolve, reject, timer });
  proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, ...(params ? { params } : {}) }) + '\n');
});
try {
  const init = await rpc(1, 'initialize', {
    protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'probe', version: '1' },
  });
  proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  let tools = [], cursor;
  for (let page = 0; page < 20; page++) {
    const result = await rpc(10 + page, 'tools/list', cursor ? { cursor } : {});
    tools = tools.concat(result.tools); cursor = result.nextCursor;
    if (!cursor) break;
  }
  if (cursor) throw Error('Tool listing exceeded 20 pages');
  const out = {
    server: init.serverInfo, count: tools.length,
    tools: tools.map(t => ({ name: t.name, readOnly: t.annotations?.readOnlyHint,
      destructive: t.annotations?.destructiveHint, openWorld: t.annotations?.openWorldHint,
      description: (t.description ?? '').slice(0, 120) })),
  };
  if (measureIdle) {
    if (process.platform !== 'win32') throw Error('--measure-idle requires Windows');
    await new Promise(resolve => setTimeout(resolve, 1000));
    const sample = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
      `$all=Get-CimInstance Win32_Process; $ids=@(${proc.pid}); do {$new=@($all | Where-Object {$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | ForEach-Object {[int]$_.ProcessId}); $ids+=$new} while($new.Count); $ps=@(Get-Process -Id $ids -ErrorAction SilentlyContinue); @{pids=@($ps.Id);working_set_bytes=[long](($ps | Measure-Object WorkingSet64 -Sum).Sum)} | ConvertTo-Json -Compress`,
    ], { encoding: 'utf8', timeout: 15000, windowsHide: true });
    if (sample.status !== 0) throw Error('Idle memory measurement failed');
    out.idle = JSON.parse(sample.stdout);
  }
  if (calls.length) {
    out.calls = [];
    for (let index = 0; index < calls.length; index++) {
      const [name, arguments_] = calls[index];
      const result = await rpc(100 + index, 'tools/call', { name, arguments: arguments_ });
      out.calls.push({ name, isError: !!result.isError, result: JSON.stringify(result).slice(0, 1500) });
    }
    if (calls.length === 1) out.call = out.calls[0].result;
    if (out.calls.some(call => call.isError)) process.exitCode = 1;
  }
  console.log(JSON.stringify(out, null, 1));
} finally {
  for (const item of waiting.values()) clearTimeout(item.timer);
  waiting.clear(); lines.close();
  if (proc.pid) {
    if (process.platform === 'win32') {
      // Windows retains ParentProcessId after a launcher exits. Discover its
      // surviving descendants even when taskkill /PID on the root would fail.
      const cleanup = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
        `$ErrorActionPreference='Stop'; $all=Get-CimInstance Win32_Process; $ids=@(${proc.pid}); do {$new=@($all | Where-Object {$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | ForEach-Object {[int]$_.ProcessId}); $ids+=$new} while($new.Count); [array]::Reverse($ids); foreach($ownedId in $ids){Stop-Process -Id $ownedId -Force -ErrorAction SilentlyContinue}; $deadline=[DateTime]::UtcNow.AddSeconds(5); do {$left=@(Get-Process -Id $ids -ErrorAction SilentlyContinue); if(!$left.Count){break}; Start-Sleep -Milliseconds 50} while([DateTime]::UtcNow -lt $deadline); if($left.Count){throw 'Owned processes remain alive'}`,
      ], { windowsHide: true, timeout: 20000, encoding: 'utf8' });
      if (cleanup.status !== 0) throw Error('Server tree cleanup failed: ' + cleanup.stderr.trim());
    } else if (proc.exitCode === null) proc.kill();
  }
}
