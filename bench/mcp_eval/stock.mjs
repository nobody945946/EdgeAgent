// Stand-in for TWStockMCPServer (twjackysu/TWStockMCPServer 8857b2e, MIT)
// with the 11 tools the laptop picked: names, Chinese descriptions and input
// schemas published exactly as the real server's (twstock_tools.json; no
// hints, like the real one), and arguments held to what those schemas and
// descriptions say (strict.mjs). Each tool takes only the parameters it
// declares (stock_no where it says stock_no, code where it says code), dates
// as YYYYMMDD on a trading day, months as YYYYMM, years in 西元 or 民國, and
// `name` as one company name matched like the real keyword lookup.
// Prices and reports are fixed per data variant; dates follow the clock in
// Taiwan time (weekdays are trading days, holidays are ignored), so the
// latest trading day is the one a model's own "today" points to.
import { readFileSync } from 'node:fs';
import { fail, published, serve } from './strict.mjs';

const DATA = {
  eval: {
    stocks: {
      2330: { name: '台積電', close: 1085.0, change: -15.0, volume: 28417, high: 1100.0, low: 1080.0,
        revenue: [['2026-08', 3236.5, 41.2], ['2026-07', 3074.1, 37.9]], dividend: [['2026', 5.0], ['2025', 4.5]],
        eps: 15.36, gross_margin: 59.1, assets: 6891234, liabilities: 2134567, ocf: 812345 },
      2317: { name: '鴻海', close: 213.5, change: 2.5, volume: 51230, high: 214.0, low: 210.5,
        revenue: [['2026-08', 6890.2, 12.4], ['2026-07', 6512.8, 9.8]], dividend: [['2026', 5.8], ['2025', 5.4]],
        eps: 3.12, gross_margin: 6.3, assets: 4123456, liabilities: 2456789, ocf: 156789 },
    },
    closes: [1071, 1078, 1092, 1085],
    market: { foreign_net_billion: -85.3, trust_net_billion: 12.1, dealer_net_billion: -3.4 },
    byStock: { foreign_net_lots: -5210, trust_net_lots: 830, dealer_net_lots: -120 },
    taiex: [23456.78, 23310.12],
    rates: { USD: { spot_buy: 31.215, spot_sell: 31.315 }, JPY: { spot_buy: 0.2081, spot_sell: 0.2121 } },
  },
  train: {
    stocks: {
      2454: { name: '聯發科', close: 1412.0, change: 23.0, volume: 6720, high: 1420.0, low: 1395.0,
        revenue: [['2026-07', 512.3, 18.6], ['2026-06', 498.7, 15.2]], dividend: [['2026', 55.0], ['2025', 54.5]],
        eps: 22.41, gross_margin: 48.7, assets: 7123456, liabilities: 3012345, ocf: 401234 },
      2412: { name: '中華電', close: 128.5, change: -0.5, volume: 9311, high: 129.0, low: 128.0,
        revenue: [['2026-07', 186.4, 2.1], ['2026-06', 183.9, 1.7]], dividend: [['2026', 4.98], ['2025', 4.76]],
        eps: 1.21, gross_margin: 36.2, assets: 5234567, liabilities: 1123456, ocf: 210987 },
      2881: { name: '富邦金', close: 92.3, change: 1.1, volume: 30215, high: 92.8, low: 91.0,
        revenue: [['2026-07', 721.5, 9.9], ['2026-06', 700.2, 8.4]], dividend: [['2026', 3.0], ['2025', 2.5]],
        eps: 7.86, gross_margin: 0, assets: 11234567, liabilities: 10345678, ocf: 98765 },
    },
    closes: [1388, 1395, 1404, 1412],
    market: { foreign_net_billion: 42.7, trust_net_billion: -6.8, dealer_net_billion: 5.2 },
    byStock: { foreign_net_lots: 3120, trust_net_lots: -410, dealer_net_lots: 95 },
    taiex: [22987.41, 23105.66],
    rates: { USD: { spot_buy: 31.605, spot_sell: 31.705 }, JPY: { spot_buy: 0.2135, spot_sell: 0.2175 },
      EUR: { spot_buy: 35.12, spot_sell: 35.52 } },
  },
};
const PERIOD = [2026, 2]; // the latest quarter with published statements
const CURRENCIES = { USD: ['USD', '美元', '美金'], JPY: ['JPY', '日圓', '日幣', '日元'], EUR: ['EUR', '歐元'] };
const ALL_AGAINST = ['NTD', 'TWD', '新台幣', '台幣']; // every rate here is against the NT dollar

const STOCK_NO = [/^\d{4,6}[A-Z]?$/, '股票代號（數字），例如 "2330"、"0050"'];
const DAY = [/^\d{8}$/, 'YYYYMMDD，例如 "20260505"'];
const MONTH_DAY = [/^\d{8}$/, 'YYYYMMDD（該月任一天），例如 "20250101" 查 2025 年 1 月'];
const MONTH = [/^\d{6}$/, '西元 YYYYMM，例如 "202601"'];
const YEAR = [/^\d{2,4}$/, '西元（例如 "2025"）或民國（例如 "114"）年度'];
const SEASON = [/^[0-4]$/, '季別 1～4，0＝最新一季'];
const COUNT = [/^[1-9]\d*$/, '正整數'];
const SKIP = [/^\d+$/, '0 或正整數'];
const YEAR_TYPE = [/^(dividend|board)$/, '"dividend"（依股利所屬年度）或 "board"（依董事會決議年度）'];

// Days as YYYYMMDD in Taiwan time.
const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10).replaceAll('-', '');
const utc = ymd => new Date(Date.UTC(+ymd.slice(0, 4), +ymd.slice(4, 6) - 1, +ymd.slice(6, 8)));
const iso = ymd => `${ymd.slice(0, 4)}-${ymd.slice(4, 6)}-${ymd.slice(6, 8)}`;
const weekend = ymd => [0, 6].includes(utc(ymd).getUTCDay());
const dayBefore = ymd => new Date(utc(ymd) - 86400000).toISOString().slice(0, 10).replaceAll('-', '');
function latestTradingDay() {
  let day = today();
  while (weekend(day)) day = dayBefore(day);
  return day;
}
// A trading day the caller named (or the latest one), as YYYY-MM-DD.
function tradingDay(date, parameter) {
  if (!date) return iso(latestTradingDay());
  if (utc(date).toISOString().slice(0, 10) !== iso(date)) fail(`${parameter}「${date}」不是真實存在的日期`, { parameter, received: date });
  if (date > today()) fail(`${iso(date)} 的資料尚未公布（今天是 ${iso(today())}）`, { parameter, received: date });
  if (weekend(date)) fail(`${iso(date)} 不是交易日（週末休市），請改查前一個交易日`, { parameter, received: date });
  return iso(date);
}
// The month a YYYYMMDD falls in, as YYYY-MM, never after this month.
function monthOf(date, parameter) {
  const month = `${date.slice(0, 4)}-${date.slice(4, 6)}`;
  if (+date.slice(4, 6) < 1 || +date.slice(4, 6) > 12) fail(`${parameter}「${date}」的月份不對`, { parameter, received: date });
  if (month > iso(today()).slice(0, 7)) fail(`${month} 還沒到`, { parameter, received: date });
  return month;
}
const western = year => (String(year).length <= 3 ? Number(year) + 1911 : Number(year));

export default function stock(variant) {
  const data = DATA[variant];
  const real = Object.fromEntries(JSON.parse(readFileSync(new URL('./twstock_tools.json', import.meta.url), 'utf8'))
    .map(t => [t.name, t]));
  const as = (name, formats, run) => published(name, real[name].description, real[name].inputSchema, formats, run);
  const listed = (no, parameter) => data.stocks[no] ?? fail(`查無股票代號 ${no}`, { parameter, received: no });
  const row = (no, s) => ({ stock_no: no, name: s.name, close: s.close, change: s.change, volume_lots: s.volume, high: s.high, low: s.low });
  function statement(code, year, season) {
    if (year && !season) fail('指定 year 時 season 必填（1～4）', { parameter: 'season' });
    const [y, q] = year ? [western(year), season] : season ? [PERIOD[0], season] : PERIOD;
    if (y * 10 + q > PERIOD[0] * 10 + PERIOD[1]) fail(`${y} 年第 ${q} 季財報尚未公布（最新是 ${PERIOD[0]} 年第 ${PERIOD[1]} 季）`, { parameter: 'year' });
    if (y * 10 + q < PERIOD[0] * 10 + PERIOD[1]) fail(`查無代號 ${code} ${y} 年第 ${q} 季的財報資料`, { parameter: 'year' });
    return { code, period: `${y}Q${q}` };
  }
  return serve([
    as('get_stock_daily_trading', { date: DAY, stock_no: STOCK_NO, limit: COUNT, offset: SKIP },
      ({ date, stock_no, name, limit, offset }) => {
        const day = tradingDay(date, 'date');
        const key = name.trim().replaceAll('臺', '台');
        if (/[\s、,，;；|／/+]/.test(key)) {
          fail(`name 一次只能放一個公司名稱（收到「${name}」）；要查幾家公司就分開呼叫幾次，每次給一個 name 或 stock_no`,
            { parameter: 'name', received: name });
        }
        if (stock_no) listed(stock_no, 'stock_no');
        const found = Object.entries(data.stocks).filter(([no, s]) => (!stock_no || no === stock_no) && (!key || s.name.includes(key)));
        if (!found.length) fail(`查無名稱含「${name}」${stock_no ? `、代號 ${stock_no} ` : ''}的上市股票`, { parameter: 'name', received: name });
        return { date: day, total: found.length, stocks: found.slice(offset, offset + limit).map(([no, s]) => row(no, s)) };
      }),
    as('get_stock_history', { stock_no: STOCK_NO, date: MONTH_DAY },
      ({ stock_no, date }) => {
        const s = listed(stock_no, 'stock_no');
        const month = monthOf(date, 'date');
        if (month < '2010-01') fail('只能查 2010 年以後', { parameter: 'date', received: date });
        const scale = s.close / data.closes.at(-1);
        return { stock_no, name: s.name, month, closes: data.closes.map(c => Math.round(c * scale * 10) / 10) };
      }),
    as('get_twse_institutional_investors_summary', { date: DAY, limit: COUNT, offset: SKIP },
      ({ date }) => ({ date: tradingDay(date, 'date'), unit: '億元', ...data.market })),
    as('get_twse_institutional_investors_by_stock', { stock_no: STOCK_NO, date: DAY },
      ({ stock_no, date }) => {
        const s = listed(stock_no, 'stock_no');
        return { stock_no, name: s.name, date: tradingDay(date, 'date'), unit: '張', ...data.byStock };
      }),
    as('get_company_monthly_revenue', { code: STOCK_NO, start_month: MONTH, end_month: MONTH },
      ({ code, start_month, end_month }) => {
        const s = listed(code, 'code');
        const known = Object.fromEntries(s.revenue.map(([m, v, g]) => [m.replace('-', ''), { revenue: v, yoy_percent: g }]));
        if (!start_month) return { code, unit: '億元', months: s.revenue.map(([m, v, g]) => ({ month: m, revenue: v, yoy_percent: g })) };
        const end = end_month || start_month;
        if (end < start_month) fail('end_month 不能早於 start_month', { parameter: 'end_month', received: end });
        const months = [];
        for (let m = start_month; m <= end; m = +m.slice(4) === 12 ? `${+m.slice(0, 4) + 1}01` : String(+m + 1)) months.push(m);
        if (months.length > 12) fail('區間最多 12 個月', { parameter: 'end_month', received: end });
        const latest = s.revenue[0][0].replace('-', '');
        return { code, unit: '億元', months: months.map(m => ({ month: `${m.slice(0, 4)}-${m.slice(4)}`,
          ...(known[m] ?? { status: m > latest ? '尚未公告' : '查無資料' }) })) };
      }),
    as('get_company_dividend', { code: STOCK_NO, start_year: YEAR, end_year: YEAR, year_type: YEAR_TYPE },
      ({ code, start_year, end_year, year_type }) => {
        const s = listed(code, 'code');
        const from = start_year ? western(start_year) : 0;
        const to = end_year ? western(end_year) : start_year ? from : 9999;
        if (to < from) fail('end_year 不能早於 start_year', { parameter: 'end_year', received: end_year });
        if (start_year && to - from >= 10) fail('區間最多 10 年', { parameter: 'end_year', received: end_year });
        const years = s.dividend.filter(([y]) => +y >= from && +y <= to);
        if (!years.length) fail(`查無代號 ${code} 在 ${from}～${to} 年的股利分派`, { parameter: 'start_year', received: start_year });
        return { code, year_type, cash_dividend_per_share: Object.fromEntries(years) };
      }),
    as('get_company_income_statement', { code: STOCK_NO, year: YEAR, season: SEASON },
      ({ code, year, season }) => {
        const s = listed(code, 'code');
        return { ...statement(code, year, season), eps: s.eps, gross_margin_percent: s.gross_margin };
      }),
    as('get_company_balance_sheet', { code: STOCK_NO, year: YEAR, season: SEASON },
      ({ code, year, season }) => {
        const s = listed(code, 'code');
        return { ...statement(code, year, season), unit: '仟元', total_assets: s.assets, total_liabilities: s.liabilities };
      }),
    as('get_company_cash_flow_statement', { code: STOCK_NO, year: YEAR, season: SEASON },
      ({ code, year, season }) => {
        const s = listed(code, 'code');
        return { ...statement(code, year, season), unit: '仟元', operating_cash_flow: s.ocf };
      }),
    as('get_taiex_index_history', { date: MONTH_DAY },
      ({ date }) => ({ month: date ? monthOf(date, 'date') : iso(today()).slice(0, 7), closes: data.taiex })),
    as('get_exchange_rates', { start_date: DAY, end_date: DAY },
      ({ start_date, end_date, currency }) => {
        if (start_date) tradingDay(start_date, 'start_date');
        const day = tradingDay(end_date, 'end_date');
        if (start_date && end_date && end_date < start_date) fail('end_date 不能早於 start_date', { parameter: 'end_date', received: end_date });
        const wanted = new Set();
        for (const word of currency.split(/[,，、\s]+/).filter(Boolean)) {
          const upper = word.toUpperCase();
          if (ALL_AGAINST.includes(upper) || ALL_AGAINST.includes(word)) Object.keys(data.rates).forEach(c => wanted.add(c));
          else {
            const code = Object.keys(CURRENCIES).find(c => CURRENCIES[c].includes(upper) || CURRENCIES[c].includes(word));
            if (!code || !data.rates[code]) fail(`查無幣別「${word}」；可用代碼或中文名稱，例如 "USD"、"日圓"`, { parameter: 'currency', received: currency });
            wanted.add(code);
          }
        }
        const rates = wanted.size ? Object.fromEntries([...wanted].map(c => [c, data.rates[c]])) : data.rates;
        return { date: day, against: 'NTD', rates };
      }),
  ], 'zh');
}
