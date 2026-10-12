// edge_agent/loop_guard.mjs: a stream stuck on one unit is a loop; real long
// output is not. Run by tests/test_prompt.py.
import assert from 'node:assert/strict';

const { looping, LoopWatch } = await import('../edge_agent/loop_guard.mjs');
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
console.log('loop guard ok');
