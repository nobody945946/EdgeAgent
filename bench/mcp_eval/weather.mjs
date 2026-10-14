// Stand-in weather server shaped like the Central Weather Administration's
// open data, in Chinese like the real one: six-character station IDs found by
// name, observation elements by code (TEMP, HUMD, 24R, WDSD), county names
// written with 臺, daily rainfall over a date range. Every tool only reads.
import { addDays, choice, daysBetween, fail, list, serve, spread, text, tool, whole, withDefault } from './strict.mjs';

const COUNTIES = ['臺北市', '新北市', '基隆市', '桃園市', '新竹市', '新竹縣', '苗栗縣', '臺中市', '彰化縣', '南投縣', '雲林縣',
  '嘉義市', '嘉義縣', '臺南市', '高雄市', '屏東縣', '宜蘭縣', '花蓮縣', '臺東縣', '澎湖縣', '金門縣', '連江縣'];
const DATA = {
  train: {
    today: '2026-09-18', time: '14:00', warmth: 2,
    stations: {
      467490: { name: '臺中', county: '臺中市', TEMP: 31.2, HUMD: 64, '24R': 0.0, WDSD: 2.1 },
      467770: { name: '梧棲', county: '臺中市', TEMP: 29.9, HUMD: 74, '24R': 0.0, WDSD: 6.2 },
      467440: { name: '高雄', county: '高雄市', TEMP: 32.4, HUMD: 71, '24R': 3.5, WDSD: 3.8 },
      467080: { name: '宜蘭', county: '宜蘭縣', TEMP: 26.8, HUMD: 88, '24R': 18.5, WDSD: 1.6 },
      467480: { name: '嘉義', county: '嘉義市', TEMP: 30.6, HUMD: 68, '24R': 0.0, WDSD: 2.4 },
    },
  },
  eval: {
    today: '2026-10-02', time: '14:00', warmth: 0,
    stations: {
      466920: { name: '臺北', county: '臺北市', TEMP: 28.7, HUMD: 76, '24R': 1.5, WDSD: 2.3 },
      466880: { name: '板橋', county: '新北市', TEMP: 28.9, HUMD: 73, '24R': 2.0, WDSD: 1.9 },
      466900: { name: '淡水', county: '新北市', TEMP: 27.8, HUMD: 79, '24R': 0.5, WDSD: 3.3 },
      466990: { name: '花蓮', county: '花蓮縣', TEMP: 27.3, HUMD: 82, '24R': 12.0, WDSD: 4.1 },
      467571: { name: '新竹', county: '新竹縣', TEMP: 29.4, HUMD: 70, '24R': 0.0, WDSD: 5.6 },
    },
  },
};
const HISTORY_DAYS = 30;

export default function weather(variant) {
  const data = DATA[variant];
  const station = id => data.stations[id] ?? fail(`查無測站 station_id=${id}；測站代碼請先用 search_stations 查`);
  // Rain in tenths of a millimetre, so a total is exact.
  const rainTenths = (id, day) => {
    const roll = spread(id, day, 100);
    return roll < 55 ? 0 : spread(id, `${day}mm`, 400) + 1;
  };
  const forecast = (county, day) => {
    const i = COUNTIES.indexOf(county);
    const maxT = 31 + data.warmth - (i % 5) - ((day * 2 + i) % 3);
    const pop = [10, 20, 30, 40, 60, 70, 80][(i * 3 + day * 2 + data.warmth) % 7];
    return { max_t: maxT, min_t: maxT - 5 - ((i + day) % 3), pop,
      wx: pop >= 60 ? '短暫陣雨' : pop >= 40 ? '多雲時陰' : pop >= 20 ? '多雲' : '晴時多雲' };
  };
  const first = addDays(data.today, -HISTORY_DAYS);
  const last = addDays(data.today, -1);
  const DAY = /^\d{4}-\d{2}-\d{2}$/;
  return serve([
    tool('search_stations', '用地名找氣象測站，回傳測站代碼（station_id）、站名與所在縣市。',
      { keyword: text('地名關鍵字，例如「恆春」') }, ['keyword'],
      ({ keyword }) => {
        const key = keyword.replaceAll('台', '臺').replace(/(站|測站)$/, '');
        const stations = Object.entries(data.stations)
          .filter(([, s]) => s.name.includes(key) || s.county.includes(key) || key.includes(s.name))
          .map(([station_id, s]) => ({ station_id, name: s.name, county: s.county }));
        return stations.length ? { stations } : { stations, note: `查無符合「${keyword}」的測站` };
      }, { readOnlyHint: true, openWorldHint: true }),
    tool('get_current_observation',
      '測站最新一筆觀測。TEMP 氣溫（°C）、HUMD 相對濕度（%）、24R 當日累積雨量（mm）、WDSD 風速（m/s）。',
      { station_id: text('測站代碼，6 碼英數字，例如 "467590"（恆春）；不知道代碼時先用 search_stations 查',
        [/^[0-9A-Z]{6}$/, '6 碼測站代碼，例如 467590']),
      elements: list(choice(['TEMP', 'HUMD', '24R', 'WDSD'], '觀測項目代碼'), '只要哪些觀測項目；省略＝全部') },
      ['station_id'],
      ({ station_id, elements }) => {
        const s = station(station_id);
        const wanted = elements?.length ? elements : ['TEMP', 'HUMD', '24R', 'WDSD'];
        return { station_id, name: s.name, obs_time: `${data.today} ${data.time}`,
          ...Object.fromEntries(wanted.map(e => [e, s[e]])) };
      }, { readOnlyHint: true, openWorldHint: true }),
    tool('get_county_forecast', '縣市未來幾天的天氣預報：天氣現象、最高溫、最低溫（°C）、降雨機率（%），從今天起算。',
      { location_name: text('縣市全名，寫「臺」不寫「台」，例如「臺東縣」「臺南市」'),
        days: withDefault(whole('要幾天的預報，1 到 7，預設 3', 1, 7), 3) },
      ['location_name'],
      ({ location_name, days }) => {
        if (!COUNTIES.includes(location_name)) {
          fail(`查無縣市「${location_name}」；縣市名稱請用全名並寫「臺」，例如「臺東縣」「臺南市」`, { parameter: 'location_name' });
        }
        return { location_name, issued: `${data.today} 11:00`,
          days: Array.from({ length: days }, (_, d) => ({ date: addDays(data.today, d), ...forecast(location_name, d) })) };
      }, { readOnlyHint: true, openWorldHint: true }),
    tool('get_rainfall_history', `測站每日雨量紀錄（mm），可查最近 ${HISTORY_DAYS} 天到昨天為止。`,
      { station_id: text('測站代碼，6 碼英數字，例如 "467590"', [/^[0-9A-Z]{6}$/, '6 碼測站代碼，例如 467590']),
        from_date: text('起始日，格式 YYYY-MM-DD', [DAY, 'YYYY-MM-DD，例如 2026-01-15']),
        to_date: text('結束日（含），格式 YYYY-MM-DD', [DAY, 'YYYY-MM-DD，例如 2026-01-15']) },
      ['station_id', 'from_date', 'to_date'],
      ({ station_id, from_date, to_date }) => {
        station(station_id);
        if (from_date > to_date) fail('from_date 不能晚於 to_date', { parameter: 'from_date' });
        if (from_date < first || to_date > last) {
          fail(`只能查 ${first} 到 ${last} 的雨量`, { parameter: from_date < first ? 'from_date' : 'to_date' });
        }
        const days = Array.from({ length: daysBetween(from_date, to_date) + 1 }, (_, i) => addDays(from_date, i))
          .map(date => ({ date, tenths: rainTenths(station_id, date) }));
        return { station_id, unit: 'mm', days: days.map(({ date, tenths }) => ({ date, rain_mm: tenths / 10 })),
          total_mm: days.reduce((sum, d) => sum + d.tenths, 0) / 10 };
      }, { readOnlyHint: true, openWorldHint: true }),
  ], 'zh');
}
