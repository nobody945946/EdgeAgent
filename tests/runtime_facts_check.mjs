// The Agent's runtime section (prompt_sections.mjs): hardware, permissions and
// the registered APIs, never a key. Run by tests/test_prompt.py.
import assert from 'node:assert/strict';

const { modelFacts } = await import('../edge_agent/prompt_sections.mjs');
const facts = modelFacts({ model_name: 'C:/m/gemma-4-E4B.gguf', context_size: 65536, agent: true,
  hardware: 'Windows、RTX 3050 Ti（4 GB）、8 GB RAM', permissions: { edit: 'reversible', shell: 'reversible', web: 'allow' },
  apis: [{ name: '富果', purpose: '台股行情', base_url: 'https://api.example/v1', docs: 'https://api.example/docs',
    key_in: 'header', key_name: 'X-API-KEY', key_env: 'EDGE_API_FUGLE' }], api_env: { EDGE_API_FUGLE: 'secret-123456' } });
assert.match(facts, /本機模型 gemma-4-E4B，不是雲端服務/);
// The host's machine, not the one node runs on (the bench's sandbox is Linux).
assert.match(facts, /這台電腦：Windows、RTX 3050 Ti.*指令用 PowerShell 執行/);
assert.ok(!facts.includes('Linux'));
assert.match(facts, /主機會自己跳出來問他；你直接呼叫工具，不用先問/);
assert.match(facts, /改檔案前主機會先備份，主人可以在 EdgeAgent 網頁的「還原」復原/);
assert.match(facts, /富果：台股行情.*\$env:EDGE_API_FUGLE，放在標頭 X-API-KEY/);
assert.ok(!facts.includes('secret-123456'));
const asked = modelFacts({ model_name: 'm', context_size: 8192, agent: true, permissions: { edit: 'ask', shell: 'ask' } });
assert.ok(!asked.includes('還原'));
const publicFacts = modelFacts({ model_name: 'm', context_size: 8192, agent: true, public: true, apis: [{ name: 'x' }] });
assert.ok(!publicFacts.includes('權限') && !publicFacts.includes('API'));
