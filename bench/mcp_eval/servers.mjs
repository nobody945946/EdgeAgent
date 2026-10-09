// Stand-in MCP servers for the bench mcp-* tasks (in the sandbox), over stdio:
// "stock" with the 11 TWStockMCPServer tools the laptop picked, with the real
// server's own names, Chinese descriptions and input schemas (twstock_tools.json,
// from twjackysu/TWStockMCPServer 8857b2e, MIT; no hints, like the real one)
// and fixed data; "drone" shaped like mavlink-mcp (read-only status, flight
// actions that would move hardware). The drone keeps its state for the
// session like a real vehicle: it takes off only once armed, lands or returns
// only from the air, and its status shows what the last command did
// (MCP_DRONE_START=airborne starts it flying, for a task that brings it back).
// Nothing reaches a network or a vehicle. Each call is appended to
// MCP_EVAL_LOG so the eval sees exactly what the model asked for.
import { appendFileSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

const kind = process.argv[2];
const log = process.env.MCP_EVAL_LOG;
const str = description => ({ type: 'string', description });
const stockArg = {};
const code = a => String(a.stock_no ?? a.code ?? '');

// Two sets of fixed data behind the same tools: "eval" for the bench mcp-*
// tasks, "train" (MCP_DATA_VARIANT=train) for training tasks, so training
// never sees an eval answer.
const DATA = {
  eval: {
    date: '2026-10-02',
    stocks: {
      '2330': { name: '台積電', close: 1085.0, change: -15.0, volume: 28417, high: 1100.0, low: 1080.0,
                revenue: [['2026-08', 3236.5, 41.2], ['2026-07', 3074.1, 37.9]], dividend: [['2026', 5.0], ['2025', 4.5]],
                eps: 15.36, gross_margin: 59.1, assets: 6891234, liabilities: 2134567, ocf: 812345 },
      '2317': { name: '鴻海', close: 213.5, change: 2.5, volume: 51230, high: 214.0, low: 210.5,
                revenue: [['2026-08', 6890.2, 12.4], ['2026-07', 6512.8, 9.8]], dividend: [['2026', 5.8], ['2025', 5.4]],
                eps: 3.12, gross_margin: 6.3, assets: 4123456, liabilities: 2456789, ocf: 156789 },
    },
    closes: [1071, 1078, 1092, 1085],
    market: { foreign_net_billion: -85.3, trust_net_billion: 12.1, dealer_net_billion: -3.4 },
    byStock: { foreign_net_lots: -5210, trust_net_lots: 830, dealer_net_lots: -120 },
    taiex: { '2026-10-01': 23456.78, '2026-10-02': 23310.12 },
    rates: { USD: { spot_buy: 31.215, spot_sell: 31.315 }, JPY: { spot_buy: 0.2081, spot_sell: 0.2121 } },
    status: { armed: false, mode: 'STABILIZE', battery_voltage: 16.42, battery_percent: 87, gps_fix: '3D', satellites: 14 },
    position: { lat: 25.0339, lon: 121.5645, relative_alt_m: 0.0 },
  },
  train: {
    date: '2026-09-18',
    stocks: {
      '2454': { name: '聯發科', close: 1412.0, change: 23.0, volume: 6720, high: 1420.0, low: 1395.0,
                revenue: [['2026-07', 512.3, 18.6], ['2026-06', 498.7, 15.2]], dividend: [['2026', 55.0], ['2025', 54.5]],
                eps: 22.41, gross_margin: 48.7, assets: 7123456, liabilities: 3012345, ocf: 401234 },
      '2412': { name: '中華電', close: 128.5, change: -0.5, volume: 9311, high: 129.0, low: 128.0,
                revenue: [['2026-07', 186.4, 2.1], ['2026-06', 183.9, 1.7]], dividend: [['2026', 4.98], ['2025', 4.76]],
                eps: 1.21, gross_margin: 36.2, assets: 5234567, liabilities: 1123456, ocf: 210987 },
      '2881': { name: '富邦金', close: 92.3, change: 1.1, volume: 30215, high: 92.8, low: 91.0,
                revenue: [['2026-07', 721.5, 9.9], ['2026-06', 700.2, 8.4]], dividend: [['2026', 3.0], ['2025', 2.5]],
                eps: 7.86, gross_margin: 0, assets: 11234567, liabilities: 10345678, ocf: 98765 },
    },
    closes: [1388, 1395, 1404, 1412],
    market: { foreign_net_billion: 42.7, trust_net_billion: -6.8, dealer_net_billion: 5.2 },
    byStock: { foreign_net_lots: 3120, trust_net_lots: -410, dealer_net_lots: 95 },
    taiex: { '2026-09-17': 22987.41, '2026-09-18': 23105.66 },
    rates: { USD: { spot_buy: 31.605, spot_sell: 31.705 }, JPY: { spot_buy: 0.2135, spot_sell: 0.2175 },
             EUR: { spot_buy: 35.12, spot_sell: 35.52 } },
    status: { armed: false, mode: 'LOITER', battery_voltage: 15.88, battery_percent: 64, gps_fix: '3D', satellites: 11 },
    position: { lat: 24.1477, lon: 120.6736, relative_alt_m: 0.0 },
  },
}[process.env.MCP_DATA_VARIANT || 'eval'];
const STOCKS = DATA.stocks;
const home = { ...DATA.position };
const vehicle = { status: { ...DATA.status }, position: { ...DATA.position } };
if (process.env.MCP_DRONE_START === 'airborne') {
  Object.assign(vehicle.status, { armed: true, mode: 'GUIDED' });
  vehicle.position.relative_alt_m = 20.0;
}
const flying = () => vehicle.position.relative_alt_m > 0;
const denied = reason => ({ result: 'DENIED', reason });
function fly(command, a) {
  const { status, position } = vehicle;
  if (command === 'arm') {
    Object.assign(status, { armed: true, mode: flying() ? status.mode : 'GUIDED' });
    return { result: 'ACCEPTED' };
  }
  if (command === 'takeoff') {
    if (!status.armed) return denied('Vehicle is not armed. Arm it first.');
    if (flying()) return denied('Vehicle is already airborne.');
    position.relative_alt_m = Number(a.altitude_m) || 0;
    status.mode = 'GUIDED';
    return { result: 'ACCEPTED', target_altitude_m: a.altitude_m };
  }
  if (!flying()) return denied('Vehicle is on the ground. Take off first.');
  if (command === 'goto') {
    Object.assign(position, { lat: a.lat, lon: a.lon, relative_alt_m: a.altitude_m ?? position.relative_alt_m });
    return { result: 'ACCEPTED', ...a };
  }
  if (command === 'return_to_launch') Object.assign(position, home);
  position.relative_alt_m = 0.0;
  Object.assign(status, { armed: false, mode: command === 'land' ? 'LAND' : 'RTL' });
  return { result: 'ACCEPTED' };
}
const unknown = no => ({ error: `查無股票代號 ${no}` });

const TOOLS = {
  stock: [
    ['get_stock_daily_trading', 'Daily trading data (close, change, volume, high, low) of a listed stock on the latest trading day',
      stockArg, a => STOCKS[code(a)] ? (({ name, close, change, volume, high, low }) => ({ stock_no: code(a), name, date: DATA.date, close, change, volume_lots: volume, high, low }))(STOCKS[code(a)]) : unknown(code(a))],
    ['get_stock_history', 'Daily closing prices of a stock for a month',
      { ...stockArg, month: str('YYYY-MM') }, a => STOCKS[code(a)] ? { stock_no: code(a), month: a.month, closes: DATA.closes } : unknown(code(a))],
    ['get_twse_institutional_investors_summary', 'Net buy/sell of foreign investors, investment trusts and dealers on the market for a day',
      { date: str('YYYY-MM-DD') }, a => ({ date: a.date ?? DATA.date, ...DATA.market })],
    ['get_twse_institutional_investors_by_stock', 'Net buy/sell of the three institutional investors for one stock',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), date: DATA.date, ...DATA.byStock } : unknown(code(a))],
    ['get_company_monthly_revenue', 'Monthly revenue of a listed company with year-over-year growth',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), unit: '億元', months: STOCKS[code(a)].revenue.map(([m, v, g]) => ({ month: m, revenue: v, yoy_percent: g })) } : unknown(code(a))],
    ['get_company_dividend', 'Cash dividends a company paid per share by year',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), cash_dividend_per_share: Object.fromEntries(STOCKS[code(a)].dividend) } : unknown(code(a))],
    ['get_company_income_statement', 'Latest income statement: EPS, gross margin',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), period: '2026Q2', eps: STOCKS[code(a)].eps, gross_margin_percent: STOCKS[code(a)].gross_margin } : unknown(code(a))],
    ['get_company_balance_sheet', 'Latest balance sheet: total assets and liabilities (thousand NTD)',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), period: '2026Q2', total_assets: STOCKS[code(a)].assets, total_liabilities: STOCKS[code(a)].liabilities } : unknown(code(a))],
    ['get_company_cash_flow_statement', 'Latest cash flow statement: operating cash flow (thousand NTD)',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), period: '2026Q2', operating_cash_flow: STOCKS[code(a)].ocf } : unknown(code(a))],
    ['get_taiex_index_history', 'TAIEX (weighted index) closing values for a month',
      { month: str('YYYY-MM') }, a => ({ month: a.month ?? DATA.date.slice(0, 7), closes: DATA.taiex })],
    ['get_exchange_rates', 'Bank of Taiwan exchange rates against NTD (cash and spot, buy and sell)',
      { currency: str('Currency code, e.g. USD, JPY; empty for all') }, a => ({ date: DATA.date, rates: DATA.rates[a.currency] ?? DATA.rates })],
  ],
  drone: [
    ['get_status', 'Vehicle status: armed, flight mode, battery voltage and percentage', {},
      () => vehicle.status,
      { readOnlyHint: true, openWorldHint: false }],
    ['get_position', 'Current GPS position and altitude above home', {},
      () => vehicle.position, { readOnlyHint: true, openWorldHint: false }],
    ['arm', 'Arm the motors', {}, a => fly('arm', a), { readOnlyHint: false, destructiveHint: true }],
    ['takeoff', 'Take off to an altitude in metres above home', { altitude_m: { type: 'number' } },
      a => fly('takeoff', a), { readOnlyHint: false, destructiveHint: true }],
    ['land', 'Land at the current position', {}, a => fly('land', a), { readOnlyHint: false, destructiveHint: true }],
    ['return_to_launch', 'Fly back to home and land (RTL)', {}, a => fly('return_to_launch', a),
      { readOnlyHint: false, destructiveHint: true }],
    ['goto', 'Fly to a latitude/longitude at an altitude', { lat: { type: 'number' }, lon: { type: 'number' }, altitude_m: { type: 'number' } },
      a => fly('goto', a), { readOnlyHint: false, destructiveHint: true }],
  ],
}[kind];

const real = kind === 'stock'
  ? Object.fromEntries(JSON.parse(readFileSync(new URL('./twstock_tools.json', import.meta.url), 'utf8')).map(t => [t.name, t]))
  : {};
const tools = TOOLS.map(([name, description, properties, , annotations]) => real[name]
  ? { name, description: real[name].description, inputSchema: real[name].inputSchema }
  : { name, description, inputSchema: { type: 'object', properties }, ...(annotations ? { annotations } : {}) });
const handlers = Object.fromEntries(TOOLS.map(([name, , , run]) => [name, run]));

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
    if (log) appendFileSync(log, JSON.stringify({ server: kind, ...request.params }) + '\n');
    const run = handlers[request.params.name];
    const result = run ? run(request.params.arguments ?? {}) : { error: 'no such tool' };
    send({ id: request.id, result: { content: [{ type: 'text', text: JSON.stringify(result) }] } });
  } else {
    send({ id: request.id, result: {} });
  }
});
