// A tiny MCP server over stdio (newline-delimited JSON-RPC) for tests: a
// read-only tool, a write tool, a tool with no hints, and (with
// MCP_FIXTURE_HARDWARE=1) a tool that would move hardware. Every executed
// call is appended to MCP_FIXTURE_LOG, so a test can see what really ran.
import { appendFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const log = process.env.MCP_FIXTURE_LOG;
if (process.env.MCP_FIXTURE_LAUNCHER_EXIT === '1') {
  const child = spawn(process.execPath, [process.argv[1]], {
    stdio: 'inherit', windowsHide: true,
    env: {...process.env, MCP_FIXTURE_LAUNCHER_EXIT: '0'},
  });
  writeFileSync(process.env.MCP_FIXTURE_PID_FILE, String(child.pid));
  process.exit(1);
}
const hardware = process.env.MCP_FIXTURE_HARDWARE === '1';
const tools = hardware ? [
  { name: 'telemetry', description: 'Read the drone battery and GPS', inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: true, openWorldHint: false } },
  { name: 'arm', description: 'Arm the drone motors', inputSchema: { type: 'object', properties: {} },
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false } },
] : [
  { name: 'quote', description: 'Read the latest stock quote for a symbol',
    inputSchema: { type: 'object', properties: { symbol: { type: 'string' } }, required: ['symbol'] },
    annotations: { readOnlyHint: true, openWorldHint: false } },
  { name: 'place_order', description: 'Place a stock order',
    inputSchema: { type: 'object', properties: { symbol: { type: 'string' }, shares: { type: 'number' } } },
    annotations: { readOnlyHint: false, destructiveHint: true } },
  { name: 'note', description: 'Write a note into the portfolio journal',
    inputSchema: { type: 'object', properties: { text: { type: 'string' } } } },
];

function send(message) { process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...message }) + '\n'); }

const trace = process.env.MCP_FIXTURE_TRACE;
if (trace) appendFileSync(trace, `started ${process.argv.join(' ')}\n`);
createInterface({ input: process.stdin }).on('line', line => {
  if (trace) appendFileSync(trace, line + '\n');
  if (!line.trim()) return;
  const request = JSON.parse(line);
  if (request.id === undefined) return;  // notifications
  if (request.method === 'initialize') {
    const initialized = () => send({ id: request.id, result: { protocolVersion: request.params?.protocolVersion ?? '2025-06-18',
      capabilities: { tools: {} }, serverInfo: { name: 'fixture', version: '1.0.0' } } });
    const delay = Number(process.env.MCP_FIXTURE_INITIALIZE_DELAY_MS ?? 0);
    if (delay > 0) setTimeout(initialized, delay);
    else initialized();
  } else if (request.method === 'tools/list') {
    send({ id: request.id, result: { tools } });
  } else if (request.method === 'tools/call') {
    if (log) appendFileSync(log, JSON.stringify(request.params) + '\n');
    send({ id: request.id, result: { content: [{ type: 'text', text: `ran ${request.params.name}` }] } });
  } else {
    send({ id: request.id, result: {} });
  }
});
