// Stand-in MCP servers for the bench mcp-* tasks (in the sandbox), over stdio:
// "stock" with the 11 TWStockMCPServer tools the laptop picked, with the real
// server's own names, Chinese descriptions and input schemas (twstock_tools.json,
// from twjackysu/TWStockMCPServer 8857b2e, MIT; no hints, like the real one)
// and fixed data; "drone" shaped like mavlink-mcp (read-only status, flight
// actions that would move hardware).
// Nothing reaches a network or a vehicle. Each call is appended to
// MCP_EVAL_LOG so the eval sees exactly what the model asked for.
import { appendFileSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

const kind = process.argv[2];
const log = process.env.MCP_EVAL_LOG;
const str = description => ({ type: 'string', description });
const stockArg = {};
const code = a => String(a.stock_no ?? a.code ?? '');

const STOCKS = {
  '2330': { name: '台積電', close: 1085.0, change: -15.0, volume: 28417, high: 1100.0, low: 1080.0,
            revenue: [['2026-08', 3236.5, 41.2], ['2026-07', 3074.1, 37.9]], dividend: [['2026', 5.0], ['2025', 4.5]],
            eps: 15.36, gross_margin: 59.1, assets: 6891234, liabilities: 2134567, ocf: 812345 },
  '2317': { name: '鴻海', close: 213.5, change: 2.5, volume: 51230, high: 214.0, low: 210.5,
            revenue: [['2026-08', 6890.2, 12.4], ['2026-07', 6512.8, 9.8]], dividend: [['2026', 5.8], ['2025', 5.4]],
            eps: 3.12, gross_margin: 6.3, assets: 4123456, liabilities: 2456789, ocf: 156789 },
};
const unknown = no => ({ error: `查無股票代號 ${no}` });

const TOOLS = {
  stock: [
    ['get_stock_daily_trading', 'Daily trading data (close, change, volume, high, low) of a listed stock on the latest trading day',
      stockArg, a => STOCKS[code(a)] ? (({ name, close, change, volume, high, low }) => ({ stock_no: code(a), name, date: '2026-10-02', close, change, volume_lots: volume, high, low }))(STOCKS[code(a)]) : unknown(code(a))],
    ['get_stock_history', 'Daily closing prices of a stock for a month',
      { ...stockArg, month: str('YYYY-MM') }, a => STOCKS[code(a)] ? { stock_no: code(a), month: a.month, closes: [1071, 1078, 1092, 1085] } : unknown(code(a))],
    ['get_twse_institutional_investors_summary', 'Net buy/sell of foreign investors, investment trusts and dealers on the market for a day',
      { date: str('YYYY-MM-DD') }, a => ({ date: a.date ?? '2026-10-02', foreign_net_billion: -85.3, trust_net_billion: 12.1, dealer_net_billion: -3.4 })],
    ['get_twse_institutional_investors_by_stock', 'Net buy/sell of the three institutional investors for one stock',
      stockArg, a => STOCKS[code(a)] ? { stock_no: code(a), date: '2026-10-02', foreign_net_lots: -5210, trust_net_lots: 830, dealer_net_lots: -120 } : unknown(code(a))],
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
      { month: str('YYYY-MM') }, a => ({ month: a.month ?? '2026-10', closes: { '2026-10-01': 23456.78, '2026-10-02': 23310.12 } })],
    ['get_exchange_rates', 'Bank of Taiwan exchange rates against NTD (cash and spot, buy and sell)',
      { currency: str('Currency code, e.g. USD, JPY; empty for all') }, a => ({ date: '2026-10-02', rates: { USD: { spot_buy: 31.215, spot_sell: 31.315 }, JPY: { spot_buy: 0.2081, spot_sell: 0.2121 } }[a.currency] ?? { USD: { spot_buy: 31.215, spot_sell: 31.315 }, JPY: { spot_buy: 0.2081, spot_sell: 0.2121 } } })],
  ],
  drone: [
    ['get_status', 'Vehicle status: armed, flight mode, battery voltage and percentage', {},
      () => ({ armed: false, mode: 'STABILIZE', battery_voltage: 16.42, battery_percent: 87, gps_fix: '3D', satellites: 14 }),
      { readOnlyHint: true, openWorldHint: false }],
    ['get_position', 'Current GPS position and altitude above home', {},
      () => ({ lat: 25.0339, lon: 121.5645, relative_alt_m: 0.0 }), { readOnlyHint: true, openWorldHint: false }],
    ['arm', 'Arm the motors', {}, () => ({ result: 'ACCEPTED' }), { readOnlyHint: false, destructiveHint: true }],
    ['takeoff', 'Take off to an altitude in metres above home', { altitude_m: { type: 'number' } },
      a => ({ result: 'ACCEPTED', target_altitude_m: a.altitude_m }), { readOnlyHint: false, destructiveHint: true }],
    ['land', 'Land at the current position', {}, () => ({ result: 'ACCEPTED' }), { readOnlyHint: false, destructiveHint: true }],
    ['return_to_launch', 'Fly back to home and land (RTL)', {}, () => ({ result: 'ACCEPTED' }),
      { readOnlyHint: false, destructiveHint: true }],
    ['goto', 'Fly to a latitude/longitude at an altitude', { lat: { type: 'number' }, lon: { type: 'number' }, altitude_m: { type: 'number' } },
      a => ({ result: 'ACCEPTED', ...a }), { readOnlyHint: false, destructiveHint: true }],
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
