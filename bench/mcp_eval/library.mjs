// Stand-in public library server, in Chinese: catalogue search by field,
// copies per branch by a 13-digit ISBN without hyphens, the owner's loans by
// item barcode, renewals and holds. Renewing and placing a hold change the
// owner's record for the session; the server marks no tool, so the owner
// vouches for the reading ones (read_only_tools).
import { addDays, choice, fail, serve, text, tool, whole, withDefault } from './strict.mjs';

const BRANCHES = { MAIN: '總館', N01: '北區分館', E02: '東區分館', S03: '南區分館', W04: '西區分館' };
const RENEW_DAYS = 14;
const DATA = {
  train: {
    today: '2026-09-18', patron: '讀者證 A2231',
    books: [
      ['9789863598701', '山海之間：台灣步道散策', '林嘉文', 2023, { MAIN: '在架', N01: '2026-09-30', S03: '在架' }],
      ['9789571386220', '一個人的料理時光', '陳若薇', 2021, { MAIN: '2026-09-26', E02: '在架' }],
      ['9789865027315', 'Python 自動化實戰', '黃志明', 2024, { MAIN: '2026-10-01', W04: '2026-09-24' }],
      ['9789579542115', '海風與燈塔', '吳佳穎', 2019, { MAIN: '2026-09-21', N01: '2026-10-02' }],
      ['9789863847216', '夜市經濟學', '王建宏', 2022, { N01: '在架', W04: '在架', E02: '2026-09-29' }],
      ['9789864457829', '林間小屋手記', '林嘉文', 2020, { S03: '在架' }],
    ],
    loans: [
      ['3110045821', '9789579542115', '2026-09-21', 1, false],
      ['3110067340', '9789571386220', '2026-09-26', 0, false],
      ['3110071195', '9789865027315', '2026-10-01', 2, true],
    ],
  },
  eval: {
    today: '2026-10-02', patron: '讀者證 B5098',
    books: [
      ['9789864798193', '城市裡的小森林', '張雅婷', 2022, { MAIN: '2026-10-09', N01: '在架', E02: '在架' }],
      ['9789571394812', '給新手的理財課', '劉家豪', 2023, { MAIN: '2026-10-04', S03: '2026-10-12' }],
      ['9789869912358', '島嶼的聲音', '許文彥', 2020, { MAIN: '2026-10-07', W04: '2026-10-20' }],
      ['9789864346615', 'JavaScript 從零開始', '蔡明翰', 2024, { W04: '在架' }],
      ['9789573297041', '貓咪的日常', '鄭心怡', 2021, { E02: '在架', S03: '在架' }],
      ['9789865581190', '深海探險記', '周以安', 2018, { MAIN: '2026-10-11', N01: '在架' }],
    ],
    loans: [
      ['3120018834', '9789571394812', '2026-10-04', 1, false],
      ['3120026607', '9789865581190', '2026-10-11', 2, false],
      ['3120039951', '9789869912358', '2026-10-07', 0, false],
    ],
  },
};

export default function library(variant) {
  const data = DATA[variant];
  const books = data.books.map(([isbn13, title, author, year, copies]) => ({ isbn13, title, author, year, copies: { ...copies } }));
  const loans = data.loans.map(([item_barcode, isbn13, due_date, renewals_left, held]) => ({ item_barcode, isbn13, due_date, renewals_left, held }));
  const holds = [];
  const book = isbn13 => books.find(b => b.isbn13 === isbn13)
    ?? fail(`查無 ISBN ${isbn13} 的館藏`, { parameter: 'isbn13' });
  const ISBN = [/^97[89]\d{10}$/, '13 位數字、不含連字號與空格，例如 9789570000001'];
  return serve([
    tool('search_catalog', '查館藏書目。回傳每筆的 isbn13、書名、作者、出版年。',
      { q: text('查詢字詞'), field: withDefault(choice(['title', 'author', 'isbn'], '查哪個欄位：title 書名、author 作者、isbn ISBN；預設 title'), 'title'),
        limit: withDefault(whole('最多幾筆，1 到 20，預設 5', 1, 20), 5) },
      ['q'],
      ({ q, field, limit }) => {
        const key = q.replaceAll('-', '').replace(/[《》「」]/g, '').trim();
        const hit = books.filter(b => (field === 'author' ? b.author : field === 'isbn' ? b.isbn13 : b.title).includes(key));
        return { total: hit.length, records: hit.slice(0, limit).map(({ isbn13, title, author, year }) => ({ isbn13, title, author, year })) };
      }),
    tool('get_holdings', '一本書在各分館的館藏與狀態：「在架」可直接借；借出中的顯示到期日。',
      { isbn13: text('13 碼 ISBN，只寫數字，不含連字號，例如 9789570000001', ISBN) }, ['isbn13'],
      ({ isbn13 }) => {
        const b = book(isbn13);
        return { isbn13, title: b.title, holdings: Object.entries(b.copies).map(([code, state]) => ({
          branch_code: code, branch: BRANCHES[code], status: state === '在架' ? '在架' : '借出', ...(state === '在架' ? {} : { due_date: state }) })) };
      }),
    tool('list_loans', '主人目前借閱中的書（讀者證已設定好）：館藏條碼、書名、到期日、還能續借幾次。', {}, [],
      () => ({ patron: data.patron, today: data.today, loans: loans.map(l => ({ item_barcode: l.item_barcode, title: book(l.isbn13).title,
        due_date: l.due_date, renewals_left: l.renewals_left })) })),
    tool('renew_item', `續借一本借閱中的書，到期日延後 ${RENEW_DAYS} 天。`,
      { item_barcode: text('館藏條碼，10 碼數字（list_loans 的 item_barcode），例如 3100000001', [/^\d{10}$/, '10 碼數字，例如 3100000001']) },
      ['item_barcode'],
      ({ item_barcode }) => {
        const loan = loans.find(l => l.item_barcode === item_barcode)
          ?? fail(`條碼 ${item_barcode} 不是你借閱中的書；請用 list_loans 查條碼`, { parameter: 'item_barcode' });
        if (loan.held) fail(`《${book(loan.isbn13).title}》已有其他讀者預約，不能續借`);
        if (loan.renewals_left < 1) fail(`《${book(loan.isbn13).title}》已達續借次數上限`);
        loan.due_date = addDays(loan.due_date, RENEW_DAYS);
        loan.renewals_left -= 1;
        return { item_barcode, title: book(loan.isbn13).title, due_date: loan.due_date, renewals_left: loan.renewals_left };
      }),
    tool('place_hold', '預約一本書，到館後在指定分館取書。',
      { isbn13: text('13 碼 ISBN，只寫數字，不含連字號', ISBN),
        pickup_branch: choice(Object.keys(BRANCHES), `取書分館代碼：${Object.entries(BRANCHES).map(([c, n]) => `${c} ${n}`).join('、')}`) },
      ['isbn13', 'pickup_branch'],
      ({ isbn13, pickup_branch }) => {
        const b = book(isbn13);
        if (loans.some(l => l.isbn13 === isbn13)) fail(`《${b.title}》你正在借閱中，不需要預約`);
        if (holds.some(h => h.isbn13 === isbn13)) fail(`《${b.title}》你已經預約過了`);
        // A copy on a shelf is sent over first; otherwise the owner waits behind every copy out.
        const out = Object.values(b.copies).filter(s => s !== '在架').length;
        const hold = { hold_id: `H${isbn13.slice(-6)}`, isbn13, title: b.title, pickup_branch, branch: BRANCHES[pickup_branch],
          queue_position: out < Object.keys(b.copies).length ? 1 : out + 1 };
        holds.push(hold);
        return hold;
      }),
  ], 'zh');
}
