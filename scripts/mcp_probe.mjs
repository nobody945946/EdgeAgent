// List an MCP server's tools with the hints EdgeAgent's gate relies on
// (readOnlyHint, destructiveHint, openWorldHint), before the server is
// enabled; optionally call one tool. Prints JSON.
//
//   node scripts/mcp_probe.mjs [--call NAME JSON] -- <command> <args...>
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
let args = process.argv.slice(2), call = null;
if (args[0] === '--call') { call = [args[1], JSON.parse(args[2])]; args = args.slice(3); }
args = args.slice(1);
// npx/uvx are .cmd shims on Windows: run them through the shell there.
const proc = spawn(args[0], args.slice(1), { stdio: ['pipe', 'pipe', 'pipe'], shell: process.platform === 'win32' });
const waiting = new Map(); let errTail = '';
proc.stderr.on('data', d => { errTail = (errTail + d).slice(-2000); });
createInterface({ input: proc.stdout }).on('line', line => {
  try { const m = JSON.parse(line); if (waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } } catch {}
});
const rpc = (id, method, params) => new Promise((ok, fail) => {
  waiting.set(id, ok); proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, ...(params ? { params } : {}) }) + '\n');
  setTimeout(() => fail(new Error(`timeout ${method}: ${errTail}`)), 180000);
});
const init = await rpc(1, 'initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'probe', version: '0' } });
proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
let tools = [], cursor;
for (let page = 0; page < 20; page++) {
  const res = await rpc(10 + page, 'tools/list', cursor ? { cursor } : {});
  tools = tools.concat(res.result.tools); cursor = res.result.nextCursor; if (!cursor) break;
}
const out = { server: init.result?.serverInfo, count: tools.length, tools: tools.map(t => ({ name: t.name,
  readOnly: t.annotations?.readOnlyHint, destructive: t.annotations?.destructiveHint, openWorld: t.annotations?.openWorldHint,
  description: (t.description ?? '').slice(0, 120) })) };
if (call) { const res = await rpc(99, 'tools/call', { name: call[0], arguments: call[1] }); out.call = JSON.stringify(res.result ?? res.error).slice(0, 1500); }
console.log(JSON.stringify(out, null, 1)); proc.kill(); process.exit(0);
