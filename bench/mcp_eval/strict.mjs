// What every stand-in server (stock.mjs, drone.mjs, weather.mjs, ...) shares:
// parameter builders whose published inputSchema is plain JSON Schema (or a
// real server's own schema, published as it is), and the argument check a
// real server runs before a tool (pydantic in FastMCP, zod in the TypeScript
// SDK). An unknown or missing parameter, a wrong type, a value outside its
// enum or range, or a malformed value comes back as an error result
// (isError) naming the parameter, the value received and the form it
// expects: that message is what a model has to read to correct its call.
// Value formats (dates, IDs) are stated only in the description and checked
// here, as most real servers do; Pi's own validation sees types, required
// and enums.
// A schema never mentions the data, so a tool has one definition in both
// data variants (bench/dataset.py refuses rows with two).

const FORMAT = Symbol('format'); // [RegExp, expected form]: checked, never published

export const text = (description, format) => ({ type: 'string', description, ...(format ? { [FORMAT]: format } : {}) });
export const whole = (description, minimum, maximum) => ({ type: 'integer', description,
  ...(minimum === undefined ? {} : { minimum }), ...(maximum === undefined ? {} : { maximum }) });
export const number = (description, minimum, maximum) => ({ type: 'number', description,
  ...(minimum === undefined ? {} : { minimum }), ...(maximum === undefined ? {} : { maximum }) });
export const flag = description => ({ type: 'boolean', description });
export const choice = (values, description) => ({ type: typeof values[0] === 'number' ? 'integer' : 'string',
  enum: values, description });
export const list = (items, description) => ({ type: 'array', items, description });
export const withDefault = (schema, value) => ({ ...schema, default: value });

// A domain error a handler raises (no such station, order already shipped).
export class Problem extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}
export const fail = (error, extra = {}) => { throw new Problem({ error, ...extra }); };

const WORDS = {
  zh: {
    invalid: '參數錯誤', noTool: (name, names) => `沒有工具 ${name}；這個伺服器的工具：${names.join('、')}`,
    unknown: names => `這個工具沒有這個參數；它的參數只有：${names.join('、') || '（無）'}`,
    missing: '缺少必填參數', type: type => `型別應為 ${{ string: '字串', integer: '整數', number: '數字', boolean: '布林值（true/false）', array: '陣列' }[type]}`,
    enum: values => `只能是其中之一：${values.join('、')}`, min: m => `不能小於 ${m}`, max: m => `不能大於 ${m}`,
    format: form => `格式不對，應為 ${form}`,
  },
  en: {
    invalid: 'Invalid arguments', noTool: (name, names) => `Unknown tool ${name}. Tools on this server: ${names.join(', ')}`,
    unknown: names => `Unknown parameter. This tool accepts only: ${names.join(', ') || '(none)'}`,
    missing: 'Missing required parameter', type: type => `Must be ${type === 'integer' ? 'an integer' : `a ${type}`}`,
    enum: values => `Must be one of: ${values.join(', ')}`, min: m => `Must be at least ${m}`, max: m => `Must be at most ${m}`,
    format: form => `Invalid format; expected ${form}`,
  },
};

function typeOk(value, type) {
  if (type === 'integer') return Number.isInteger(value);
  if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
  if (type === 'array') return Array.isArray(value);
  return typeof value === type;
}

function problems(schema, args, words) {
  const found = [];
  const props = schema.properties;
  for (const [name, value] of Object.entries(args)) {
    if (!(name in props)) found.push({ parameter: name, message: words.unknown(Object.keys(props)), received: value });
  }
  for (const name of schema.required ?? []) {
    if (args[name] === undefined || args[name] === null || args[name] === '') {
      found.push({ parameter: name, message: words.missing,
        expected: props[name].description ?? props[name][FORMAT]?.[1] ?? props[name].title });
    }
  }
  for (const [name, value] of Object.entries(args)) {
    const spec = props[name];
    if (!spec || value === undefined || value === null || value === '') continue;
    const values = spec.type === 'array' ? value : [value];
    if (!typeOk(value, spec.type)) {
      found.push({ parameter: name, message: words.type(spec.type), received: value });
      continue;
    }
    const item = spec.type === 'array' ? spec.items : spec;
    for (const one of values) {
      if (!typeOk(one, item.type)) found.push({ parameter: name, message: words.type(item.type), received: one });
      else if (item.enum && !item.enum.includes(one)) found.push({ parameter: name, message: words.enum(item.enum), received: one });
      else if (item.minimum !== undefined && one < item.minimum) found.push({ parameter: name, message: words.min(item.minimum), received: one });
      else if (item.maximum !== undefined && one > item.maximum) found.push({ parameter: name, message: words.max(item.maximum), received: one });
      else if (item[FORMAT] && !item[FORMAT][0].test(one)) {
        found.push({ parameter: name, message: words.format(item[FORMAT][1]), received: one });
      }
    }
  }
  return found;
}

// tool(name, description, properties, required, run, annotations): run(args)
// returns the result or raises fail(); optional parameters missing from args
// get their schema default first.
export const tool = (name, description, properties, required, run, annotations) =>
  ({ name, description, properties, required, run, annotations });
// A tool whose real server's inputSchema is known (TWStockMCPServer): published
// exactly as the real one, and checked with the value formats its description
// states ({ parameter: [RegExp, expected form] }).
export const published = (name, description, schema, formats, run, annotations) => ({
  name, description, run, annotations, schema, required: schema.required ?? [],
  properties: Object.fromEntries(Object.entries(schema.properties ?? {})
    .map(([key, spec]) => [key, formats[key] ? { ...spec, [FORMAT]: formats[key] } : spec])),
});

export function serve(tools, lang) {
  const words = WORDS[lang];
  const byName = new Map(tools.map(t => [t.name, t]));
  const schemaOf = t => ({ type: 'object', properties: t.properties, ...(t.required.length ? { required: t.required } : {}) });
  return {
    tools: tools.map(t => ({ name: t.name, description: t.description, inputSchema: t.schema ?? JSON.parse(JSON.stringify(schemaOf(t))),
      ...(t.annotations ? { annotations: t.annotations } : {}) })),
    call(name, args) {
      const t = byName.get(name);
      if (!t) return { isError: true, payload: { error: words.noTool(name, [...byName.keys()]) } };
      const details = problems(schemaOf(t), args, words);
      if (details.length) return { isError: true, payload: { error: words.invalid, details } };
      const filled = { ...args };
      for (const [key, spec] of Object.entries(t.properties)) {
        if (filled[key] === undefined && spec.default !== undefined) filled[key] = spec.default;
      }
      try {
        return { payload: t.run(filled) };
      } catch (error) {
        if (error instanceof Problem) return { isError: true, payload: error.payload };
        throw error;
      }
    },
  };
}

// Calendar days as YYYY-MM-DD strings, in UTC so no local zone shifts them.
export const addDays = (day, n) => new Date(Date.parse(`${day}T00:00:00Z`) + n * 86400000).toISOString().slice(0, 10);
export const daysBetween = (from, to) => Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000);
// A deterministic stand-in for measured values: 0..modulus-1 from a seed text and an index.
export function spread(seed, index, modulus) {
  let h = 2166136261;
  for (const ch of `${seed}:${index}`) h = Math.imul(h ^ ch.codePointAt(0), 16777619) >>> 0;
  // murmur3's finalizer, so neighbouring days do not get neighbouring values
  h = Math.imul(h ^ (h >>> 16), 0x85ebca6b) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35) >>> 0;
  return ((h ^ (h >>> 16)) >>> 0) % modulus;
}
