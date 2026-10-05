// One real web_research lookup (edge_agent/web_tools.mjs), reported as numbers
// only: pages read, passages returned, and whether the passages mention 2023.
// Usage: node scripts/research_check.mjs
const { webResearch } = await import(new URL('../edge_agent/web_tools.mjs', import.meta.url));
const text = await webResearch('Python 3.12 是哪一天發布的？', ['Python 3.12 release date']);
const pages = Number(text.match(/讀了 (\d+) 個網頁/)?.[1] ?? 0);
const passages = (text.match(/^\[\d+\] /gm) ?? []).length;
console.log(JSON.stringify({ pages_read: pages, passages, mentions_2023: text.includes('2023') }));
