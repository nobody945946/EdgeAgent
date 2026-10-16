// edge_agent/loop_guard.mjs: a stream stuck on one unit is a loop; real long
// output is not. Run by tests/test_prompt.py.
import assert from 'node:assert/strict';

const { looping, circling, LoopWatch } = await import('../edge_agent/loop_guard.mjs');
// The loops seen in the bench.
assert.ok(looping('$exts=@(' + "'ts',".repeat(600)) === 5);
assert.ok(looping('"cur",'.repeat(400)) > 0);
assert.ok(looping('Get-ChildItem | Sort-Object Length\n'.repeat(80)) > 0);
// Real long output: a CSV of different rows, a long report, a markdown table.
const rows = Array.from({ length: 400 }, (_, i) => `${i},${i * 7 % 13},item-${i}\n`).join('');
assert.equal(looping(rows), 0);
const report = Array.from({ length: 200 }, (_, i) => `第 ${i + 1} 點：檔案 report_${i}.txt 共 ${i * 37} 行。\n`).join('');
assert.equal(looping(report), 0);
const table = '| 名稱 | 大小 |\n|---|---|\n' + Array.from({ length: 150 }, (_, i) => `| f${i}.jpg | ${i * 3} KB |\n`).join('');
assert.equal(looping(table), 0);
assert.equal(looping('short'), 0);
// A whole block written again and again (the teacher in its thinking,
// 2026-10-13: a 1,097-character plan 29 times, a 6,051-character one 7 times).
const plan = Array.from({ length: 24 }, (_, i) => `- Day ${i % 3 + 1} dinner ${i}: "Sushi ${i}" (Ginza), reservable, about ${15000 + i * 500} yen.\n`).join('');
assert.equal(looping('Let me pick restaurants:\n' + plan.repeat(29)), plan.length);
assert.equal(looping(report.repeat(7)), report.length);
// A block quoted again once or twice is not a loop.
assert.equal(looping(plan.repeat(3)), 0);
assert.equal(looping(report.repeat(2)), 0);
// Watched as it streams: caught within the stream, not after it.
const watch = new LoopWatch();
let caught = -1;
for (let i = 0; i < 2000 && caught < 0; i++) if (watch.add("'ts',")) caught = i;
assert.ok(caught > 0 && caught < 600, `caught at ${caught}`);
watch.reset();
assert.equal(rows.match(/.{1,50}/gs).some(piece => watch.add(piece)), false);
watch.reset();
caught = -1;
for (let i = 0; i < 29 && caught < 0; i++) if (plan.match(/.{1,50}/gs).some(piece => watch.add(piece))) caught = i;
assert.ok(caught > 0 && caught < 8, `caught in copy ${caught}`);
watch.reset();
assert.equal((report + report).match(/.{1,50}/gs).some(piece => watch.add(piece)), false);
// A thought circling round a few sentences in a shuffled order (v3,
// 2026-10-16, until the context was full): never periodic, still a loop.
const said = [
  "Hmm, maybe they're referring to something else. Let me try to ask for clarification.",
  'Maybe they mean the Battle of Stalingrad, which started in 1942? That is a famous siege.',
  'Actually, let me think about what they might mean by "the 1942 thing you just understood".',
  'I think it is best to ask for clarification, but first let me think about what they mean.',
  'The Siege of Leningrad is 1941 to 1944, and 1942 was a particularly brutal winter there.',
];
let seed = 7;
const next = () => (seed = (seed * 1103515245 + 12345) % 2147483648);
const circle = Array.from({ length: 300 }, () => said[next() % said.length] + '\n\n').join('');
assert.equal(looping(circle), 0);
assert.ok(circling(circle));
watch.reset();
const pieces = circle.match(/.{1,50}/gs);
caught = pieces.findIndex(piece => watch.add(piece));
assert.ok(caught > 0 && caught * 50 < 8000, `caught at ${caught * 50} characters`);
// Thinking that comes back to a point now and then, a compose file whose
// services share lines, a test file of similar asserts: not circling.
const thought = Array.from({ length: 120 }, (_, i) => i % 6 === 5
  ? 'Let me re-check the requirement before going on.'
  : `Step ${i}: file part_${i}.csv has ${i * 13 % 97} rows, so the total so far is ${i * 41}.`).join('\n');
assert.equal(circling(thought), false);
const compose = 'services:\n' + Array.from({ length: 30 }, (_, i) =>
  `  worker-${i}:\n    image: registry.local/worker:${i}\n    restart: unless-stopped\n` +
  '    env_file: ./config/shared.env\n    logging:\n      driver: json-file\n').join('');
assert.equal(circling(compose), false);
const asserts = Array.from({ length: 80 }, (_, i) =>
  `    assert parse_duration("${i}m${i % 60}s") == ${i * 60 + i % 60}\n    assert parse_duration("") is None\n`).join('');
assert.equal(circling(asserts), false);
assert.equal([thought, compose, asserts].some(text => {
  watch.reset();
  return text.match(/.{1,50}/gs).some(piece => watch.add(piece));
}), false);
console.log('loop guard ok');
