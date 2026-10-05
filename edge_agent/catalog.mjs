// The Agent's tool catalog search. Pi's own tool_search keeps only [a-z0-9]
// words, so a Chinese request ("把下載資料夾整理一下") finds nothing; this one
// also indexes Chinese as overlapping character pairs. BM25 as in Pi's.

const STOP_WORDS = new Set(['a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in', 'is',
  'it', 'of', 'on', 'or', 'that', 'the', 'this', 'to', 'with', 'my', 'me', 'i', 'you', 'please']);
// Chinese function characters that carry no meaning on their own.
const CJK_STOP = new Set(['的', '了', '我', '你', '他', '把', '幫', '請', '一', '下', '個', '是', '在', '要', '和', '跟']);

function stem(term) {
  if (term.length > 4 && term.endsWith('ies')) return `${term.slice(0, -3)}y`;
  if (term.length > 4 && /(ches|shes|sses|xes|zes)$/.test(term)) return term.slice(0, -2);
  if (term.length > 3 && term.endsWith('s') && !term.endsWith('ss')) return term.slice(0, -1);
  return term;
}

/** Search terms: English words (camelCase and snake_case split, light stemming)
 * and, for runs of Chinese, each character pair (a lone character on its own). */
export function terms(text) {
  const out = [];
  const spaced = String(text).replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
  for (const [word] of spaced.matchAll(/[a-z0-9]+|[㐀-鿿豈-﫿]+/g)) {
    if (/^[a-z0-9]/.test(word)) {
      if (!STOP_WORDS.has(word)) out.push(stem(word));
    } else if (word.length === 1) {
      if (!CJK_STOP.has(word)) out.push(word);
    } else {
      for (let i = 0; i + 1 < word.length; i++) out.push(word.slice(i, i + 2));
    }
  }
  return out;
}

function schemaText(schema, parts) {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return;
  if (typeof schema.description === 'string') parts.push(schema.description);
  for (const [name, property] of Object.entries(schema.properties ?? {})) {
    parts.push(name);
    schemaText(property, parts);
  }
  schemaText(schema.items, parts);
  for (const key of ['anyOf', 'oneOf', 'allOf']) for (const variant of schema[key] ?? []) schemaText(variant, parts);
}

/** What a tool is found by: its name, description, extra keywords (the
 * toolbox's Chinese and English words), parameters and its server. */
export function searchText(tool) {
  const parts = [tool.name, tool.description ?? '', tool.keywords ?? ''];
  schemaText(tool.parameters, parts);
  if (tool.namespace) parts.push(tool.namespace.name ?? '', tool.namespace.description ?? '');
  return parts.filter(Boolean).join(' ');
}

/** Rank `tools` ({name, text}) for `query`; best first, at most `limit`. */
export function rank(query, tools, limit) {
  const queryTerms = [...new Set(terms(query))];
  if (!queryTerms.length || !tools.length || limit <= 0) return [];
  const counts = tools.map(tool => {
    const found = new Map();
    for (const term of terms(tool.text)) found.set(term, (found.get(term) ?? 0) + 1);
    return found;
  });
  const lengths = counts.map(found => [...found.values()].reduce((sum, n) => sum + n, 0));
  const average = lengths.reduce((sum, n) => sum + n, 0) / tools.length || 1;
  const idf = new Map(queryTerms.map(term => {
    const frequency = counts.filter(found => found.has(term)).length;
    return [term, Math.log(1 + (tools.length - frequency + 0.5) / (frequency + 0.5))];
  }));
  const k1 = 1.2, b = 0.75;
  const scored = [];
  tools.forEach((tool, index) => {
    let score = 0;
    for (const term of queryTerms) {
      const count = counts[index].get(term);
      if (!count) continue;
      const norm = k1 * (1 - b + b * lengths[index] / average);
      score += idf.get(term) * (count * (k1 + 1)) / (count + norm);
    }
    if (score > 0) scored.push({ name: tool.name, score });
  });
  return scored.sort((x, y) => y.score - x.score).slice(0, limit);
}

/** A server's tools come together: once one of them is found, the rest of
 * that server's (when it has at most `cap`) are listed too. A query that only
 * names the server (Chinese words against English tool descriptions) ties
 * every tool of it, and a cut at the limit could drop the right one. */
export function withServers(found, tools, cap = 12) {
  const names = found.map(match => match.name);
  const serverOf = name => tools.find(tool => tool.name === name)?.server;
  for (const server of new Set(names.map(serverOf).filter(Boolean))) {
    const members = tools.filter(tool => tool.server === server).map(tool => tool.name);
    if (members.length > cap) continue;
    for (const name of members) if (!names.includes(name)) names.push(name);
  }
  return names;
}
