// Stand-in MCP servers for the bench mcp-* tasks (in the sandbox), over
// stdio, one module per server: "stock" (TWStockMCPServer's own tools and
// schemas), "drone" (shaped like mavlink-mcp), and the everyday servers an
// owner might plug in (weather, transit, library, home, shop, calendar,
// parcel). Every tool checks its arguments like a real server and answers a
// bad call with an error result naming the parameter (strict.mjs), so a
// model learns to read whatever schema a tool declares. Each server has two
// sets of fixed data behind the same tools: "eval" for the bench mcp-* tasks,
// "train" (MCP_DATA_VARIANT=train) for training tasks, so training never sees
// an eval answer. Nothing reaches a network, a vehicle or a device.
import { createInterface } from 'node:readline';

const SERVERS = ['stock', 'drone', 'weather', 'transit', 'library', 'home', 'shop', 'calendar', 'parcel'];
const kind = process.argv[2];
const variant = process.env.MCP_DATA_VARIANT || 'eval';
if (!['eval', 'train'].includes(variant) || !SERVERS.includes(kind)) {
  process.stderr.write(`usage: servers.mjs ${SERVERS.join('|')} (MCP_DATA_VARIANT eval|train)\n`);
  process.exit(2);
}
const { tools, call } = (await import(`./${kind}.mjs`)).default(variant);

function send(message) { process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...message }) + '\n'); }
createInterface({ input: process.stdin }).on('line', line => {
  if (!line.trim()) return;
  const request = JSON.parse(line);
  if (request.id === undefined) return;
  if (request.method === 'initialize') {
    send({ id: request.id, result: { protocolVersion: request.params?.protocolVersion ?? '2025-06-18',
      capabilities: { tools: {} }, serverInfo: { name: `eval-${kind}`, version: '1.0.0' } } });
  } else if (request.method === 'tools/list') {
    send({ id: request.id, result: { tools } });
  } else if (request.method === 'tools/call') {
    const { payload, isError } = call(request.params.name, request.params.arguments ?? {});
    send({ id: request.id, result: { content: [{ type: 'text', text: JSON.stringify(payload) }], ...(isError ? { isError } : {}) } });
  } else {
    send({ id: request.id, result: {} });
  }
});
