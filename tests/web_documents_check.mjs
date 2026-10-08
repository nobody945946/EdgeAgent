// web_fetch reads a JSON API and a PDF (spec v2, 2026-10-08: the ICPC problem
// sets were PDFs the Agent could not open). Run by tests/test_documents.py.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';

process.env.EDGE_AGENT_WEB_ALLOW_LOOPBACK = '1';
const web = await import('../edge_agent/web_tools.mjs');
const pdf = readFileSync(process.env.EDGE_TEST_PDF);
const server = createServer((req, res) => {
  if (req.url === '/api/price') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end('{"lowest": 499}'); }
  if (req.url === '/problems.pdf') { res.writeHead(200, { 'content-type': 'application/pdf' }); return res.end(pdf); }
  res.writeHead(200, { 'content-type': 'application/octet-stream' }); res.end(Buffer.from([0, 1, 2]));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
assert.match(await web.webFetch(base + '/api/price'), /"lowest": 499/);
await assert.rejects(web.webFetch(base + '/problems.pdf'), /cannot be read here/);
web.usePython(process.env.EDGE_TEST_PYTHON);
assert.match(await web.webFetch(base + '/problems.pdf'), /ICPC_PROBLEM_A/);
await assert.rejects(web.webFetch(base + '/blob'), /Unsupported content type/);
server.close();
