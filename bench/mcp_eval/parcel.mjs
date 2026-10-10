// Stand-in parcel tracking server, in Chinese: a parcel is found by carrier
// code plus its tracking number written without spaces or hyphens; the
// owner's watched parcels have nicknames; watching one more changes that list
// for the session. Fees follow fixed tables. There is no tool that redirects
// or reschedules a delivery. The server marks no tool; the owner vouches for
// the reading ones (read_only_tools).
import { choice, fail, flag, number, serve, text, tool, whole, withDefault } from './strict.mjs';

const CARRIERS = { tcat: '黑貓宅急便', hct: '新竹物流', post: '中華郵政', seven: '7-ELEVEN 交貨便', family: '全家店到店' };
const DATA = {
  train: {
    parcels: [
      ['tcat', '905588120034', '配送中', [['2026-09-16 18:20', '台北轉運中心', '已收件'], ['2026-09-17 03:10', '台中轉運中心', '轉運中'],
        ['2026-09-18 08:45', '台中西屯營業所', '配送中']], { eta: '2026-09-18' }],
      ['post', '71234560018822', '已投遞', [['2026-09-16 10:05', '臺北郵局', '交寄'], ['2026-09-17 14:02', '臺中郵局', '已投遞']], {}],
      ['seven', 'C5520184736', '已到店', [['2026-09-15 20:11', '7-ELEVEN 民生門市', '寄件'], ['2026-09-17 16:30', '7-ELEVEN 逢甲門市', '已到店']],
        { pickup_by: '2026-09-24' }],
      ['hct', '6411002947', '轉運中', [['2026-09-17 17:40', '新竹物流 桃園站', '已收件'], ['2026-09-18 05:20', '新竹物流 台中站', '轉運中']],
        { eta: '2026-09-19' }],
    ],
    watched: [['鍵盤', 'tcat', '905588120034'], ['書', 'seven', 'C5520184736'], ['媽媽寄的水果', 'post', '71234560018822']],
  },
  eval: {
    parcels: [
      ['tcat', '905601447782', '轉運中', [['2026-10-01 19:05', '新竹轉運中心', '已收件'], ['2026-10-02 04:40', '台北轉運中心', '轉運中']],
        { eta: '2026-10-03' }],
      ['hct', '6430007781', '已送達', [['2026-09-30 15:20', '新竹物流 台中站', '已收件'], ['2026-10-01 16:40', '新竹物流 台北站', '已送達，簽收：管理室']], {}],
      ['family', 'F2210047719', '已到店', [['2026-09-29 12:00', '全家 板橋文化店', '寄件'], ['2026-10-01 10:15', '全家 花蓮中華店', '已到店']],
        { pickup_by: '2026-10-08' }],
      ['post', '70015542338811', '招領中', [['2026-09-30 09:30', '花蓮郵局', '交寄'], ['2026-10-02 11:20', '臺北南海郵局', '投遞不成功，招領中']],
        { pickup_by: '2026-10-09' }],
    ],
    watched: [['耳機', 'tcat', '905601447782'], ['衣服', 'family', 'F2210047719'], ['合約文件', 'post', '70015542338811']],
  },
};
const FEES = { tcat: [[60, 130], [90, 170], [120, 210], [150, 250]], hct: [[60, 120], [90, 160], [120, 200], [150, 240]] };

export default function parcel(variant) {
  const data = DATA[variant];
  const parcels = data.parcels.map(([carrier, tracking_no, status, events, extra]) => ({ carrier, tracking_no, status, events, extra }));
  const watched = data.watched.map(([nickname, carrier, tracking_no]) => ({ nickname, carrier, tracking_no }));
  const find = (carrier, no) => parcels.find(p => p.carrier === carrier && p.tracking_no === no);
  const NUMBER = [/^[0-9A-Z]{8,20}$/, '只寫數字與大寫英文字母，不含空格與連字號，例如 905500000001'];
  const carrierCode = choice(Object.keys(CARRIERS), `物流業者代碼：${Object.entries(CARRIERS).map(([c, n]) => `${c} ${n}`).join('、')}`);
  return serve([
    tool('track', '查一件包裹的狀態與歷程。', { carrier: carrierCode, tracking_no: text('貨件單號，只寫數字與英文字母，不含空格與連字號', NUMBER) },
      ['carrier', 'tracking_no'],
      ({ carrier, tracking_no }) => {
        const p = find(carrier, tracking_no) ?? fail(`查無貨件：${CARRIERS[carrier]} 單號 ${tracking_no}`);
        return { carrier, carrier_name: CARRIERS[carrier], tracking_no, status: p.status,
          events: p.events.map(([time, location, description]) => ({ time, location, description })), ...p.extra };
      }),
    tool('list_watched', '主人追蹤中的包裹：暱稱、物流業者代碼、單號、目前狀態。', {}, [],
      () => ({ parcels: watched.map(w => ({ ...w, status: find(w.carrier, w.tracking_no)?.status ?? '查無資料' })) })),
    tool('watch', '把一件包裹加進追蹤清單。', { carrier: carrierCode, tracking_no: text('貨件單號，不含空格與連字號', NUMBER),
      nickname: text('暱稱，例如「新手機」') }, ['carrier', 'tracking_no'],
      ({ carrier, tracking_no, nickname }) => {
        if (watched.some(w => w.carrier === carrier && w.tracking_no === tracking_no)) fail('這件包裹已經在追蹤清單裡');
        const entry = { nickname: nickname ?? tracking_no, carrier, tracking_no };
        watched.push(entry);
        return { added: entry, status: find(carrier, tracking_no)?.status ?? '尚無物流資料' };
      }),
    tool('estimate_fee', '宅配運費試算（新台幣）。黑貓與新竹物流依三邊長合計分級；郵局依重量；超商寄件單一價。',
      { carrier: carrierCode, size_cm: whole('長寬高三邊合計（公分），30 到 150', 30, 150),
        weight_kg: number('重量（公斤），最多 20', 0.1, 20), outlying_island: withDefault(flag('寄往離島（運費加 50%），預設 false'), false) },
      ['carrier', 'size_cm', 'weight_kg'],
      ({ carrier, size_cm, weight_kg, outlying_island }) => {
        let fee;
        if (carrier === 'post') fee = weight_kg <= 5 ? 105 : weight_kg <= 10 ? 150 : 200;
        else if (carrier === 'seven' || carrier === 'family') {
          if (size_cm > 105 || weight_kg > 10) fail('超商寄件上限是三邊合計 105 公分、10 公斤', { parameter: size_cm > 105 ? 'size_cm' : 'weight_kg' });
          fee = carrier === 'seven' ? 60 : 65;
        } else fee = FEES[carrier].find(([limit]) => size_cm <= limit)[1];
        return { carrier, carrier_name: CARRIERS[carrier], size_cm, weight_kg, outlying_island, fee_twd: outlying_island ? Math.round(fee * 1.5) : fee };
      }),
  ], 'zh');
}
