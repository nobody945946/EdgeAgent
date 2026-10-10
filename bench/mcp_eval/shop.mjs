// Stand-in back office of a small online shop, in Chinese: products by SKU,
// stock per warehouse code, orders by order number (SO + date + serial),
// order dates as YYYY/MM/DD, shipping and cancelling. Orders keep their state
// for the session. The server marks no tool; the owner vouches for the
// reading ones (read_only_tools). There is no refund or return tool.
import { choice, fail, serve, text, tool } from './strict.mjs';

const WAREHOUSES = { TPE: '台北倉', TXG: '台中倉', KHH: '高雄倉' };
const CARRIERS = { tcat: '黑貓宅急便', hct: '新竹物流', post: '中華郵政' };
const DATA = {
  train: {
    products: [
      ['TEA-OOL-150', '凍頂烏龍茶 150g', 680, { TPE: 42, TXG: 15, KHH: 0 }],
      ['TEA-BLK-100', '日月潭紅茶 100g', 520, { TPE: 8, TXG: 33, KHH: 12 }],
      ['CAKE-PIN-12', '鳳梨酥 12 入', 450, { TPE: 120, TXG: 64, KHH: 75 }],
      ['SOAP-LAV-3', '薰衣草手工皂 3 入', 360, { TPE: 0, TXG: 0, KHH: 6 }],
    ],
    orders: [
      ['SO20260914-0031', '王小姐', '2026/09/14 10:12', [['CAKE-PIN-12', 2]], 'shipped', ['tcat', '905512340871']],
      ['SO20260916-0042', '陳先生', '2026/09/16 21:40', [['TEA-OOL-150', 1], ['TEA-BLK-100', 2]], 'paid'],
      ['SO20260917-0045', '林小姐', '2026/09/17 08:05', [['SOAP-LAV-3', 2]], 'unpaid'],
      ['SO20260917-0047', '張先生', '2026/09/17 19:33', [['CAKE-PIN-12', 4]], 'paid'],
      ['SO20260918-0050', '黃先生', '2026/09/18 09:58', [['TEA-OOL-150', 3]], 'unpaid'],
    ],
  },
  eval: {
    products: [
      ['COF-ETH-227', '衣索比亞咖啡豆 227g', 520, { TPE: 25, TXG: 9, KHH: 14 }],
      ['COF-HUA-227', '花蓮瑞穗咖啡豆 227g', 780, { TPE: 0, TXG: 17, KHH: 23 }],
      ['NOU-ALM-20', '杏仁牛軋糖 20 入', 380, { TPE: 88, TXG: 40, KHH: 52 }],
      ['BAG-CVS-01', '帆布托特包', 650, { TPE: 12, TXG: 0, KHH: 4 }],
    ],
    orders: [
      ['SO20260928-0112', '李小姐', '2026/09/28 13:20', [['NOU-ALM-20', 3]], 'shipped', ['hct', '6420018853']],
      ['SO20260930-0118', '周先生', '2026/09/30 22:47', [['COF-ETH-227', 2], ['BAG-CVS-01', 1]], 'paid'],
      ['SO20261001-0121', '吳小姐', '2026/10/01 07:55', [['COF-HUA-227', 1]], 'unpaid'],
      ['SO20261001-0124', '蔡先生', '2026/10/01 18:09', [['NOU-ALM-20', 5]], 'paid'],
      ['SO20261002-0127', '鄭小姐', '2026/10/02 11:31', [['COF-ETH-227', 1]], 'cancelled'],
    ],
  },
};
const STATUS = { unpaid: '未付款', paid: '已付款', shipped: '已出貨', cancelled: '已取消' };

export default function shop(variant) {
  const data = DATA[variant];
  const products = new Map(data.products.map(([sku, name, price, stock]) => [sku, { name, price, stock }]));
  const orders = data.orders.map(([order_no, buyer, ordered_at, items, status, shipment]) => ({ order_no, buyer, ordered_at,
    items: items.map(([sku, qty]) => ({ sku, name: products.get(sku).name, qty, price: products.get(sku).price })),
    status, shipment: shipment ? { carrier: shipment[0], tracking_no: shipment[1] } : null }));
  const total = o => o.items.reduce((sum, i) => sum + i.qty * i.price, 0);
  const ORDER = [/^SO\d{8}-\d{4}$/, 'SO + 8 碼下單日 + - + 4 碼流水號，例如 SO20260101-0007'];
  const order = no => orders.find(o => o.order_no === no) ?? fail(`查無訂單 ${no}`, { parameter: 'order_no' });
  const brief = o => ({ order_no: o.order_no, buyer: o.buyer, ordered_at: o.ordered_at, total: total(o), status: o.status });
  const DAY = [/^\d{4}\/\d{2}\/\d{2}$/, 'YYYY/MM/DD，例如 2026/01/15'];
  return serve([
    tool('search_products', '用名稱關鍵字找商品，回傳貨號（sku）、品名、售價。',
      { keyword: text('品名關鍵字') }, ['keyword'],
      ({ keyword }) => ({ products: [...products].filter(([sku, p]) => p.name.includes(keyword) || sku.includes(keyword.toUpperCase()))
        .map(([sku, p]) => ({ sku, name: p.name, price: p.price })) })),
    tool('get_stock', `商品在各倉庫的庫存數量。倉庫代碼：${Object.entries(WAREHOUSES).map(([c, n]) => `${c} ${n}`).join('、')}。`,
      { sku: text('商品貨號，例如 TEA-GRN-300（用 search_products 查）', [/^[A-Z]+-[A-Z]+-\d+$/, '貨號，例如 TEA-GRN-300']),
        warehouse: choice(Object.keys(WAREHOUSES), '只查這個倉庫；省略＝全部倉庫') },
      ['sku'],
      ({ sku, warehouse }) => {
        const p = products.get(sku) ?? fail(`查無貨號 ${sku}；請用 search_products 查`, { parameter: 'sku' });
        const stock = warehouse ? { [warehouse]: p.stock[warehouse] } : p.stock;
        return { sku, name: p.name, stock, ...(warehouse ? {} : { total: Object.values(p.stock).reduce((a, b) => a + b, 0) }) };
      }),
    tool('list_orders', `列出訂單，可依狀態與下單日篩選。狀態：${Object.entries(STATUS).map(([c, n]) => `${c} ${n}`).join('、')}。`,
      { status: choice(Object.keys(STATUS), '只列這個狀態'), date_from: text('下單日起，格式 YYYY/MM/DD', DAY),
        date_to: text('下單日迄（含），格式 YYYY/MM/DD', DAY) },
      [],
      ({ status, date_from, date_to }) => {
        if (date_from && date_to && date_from > date_to) fail('date_from 不能晚於 date_to', { parameter: 'date_from' });
        const found = orders.filter(o => (!status || o.status === status) && (!date_from || o.ordered_at.slice(0, 10) >= date_from)
          && (!date_to || o.ordered_at.slice(0, 10) <= date_to));
        return { count: found.length, orders: found.map(brief) };
      }),
    tool('get_order', '一張訂單的明細：品項、數量、金額、狀態、出貨資訊。',
      { order_no: text('訂單編號，格式 SO + 8 碼下單日 + - + 4 碼流水號，例如 SO20260101-0007', ORDER) }, ['order_no'],
      ({ order_no }) => {
        const o = order(order_no);
        return { ...brief(o), items: o.items, ...(o.shipment ? { shipment: { ...o.shipment, carrier_name: CARRIERS[o.shipment.carrier] } } : {}) };
      }),
    tool('ship_order', '把已付款的訂單標成已出貨並填上物流單號（會通知買家）。',
      { order_no: text('訂單編號，例如 SO20260101-0007', ORDER),
        carrier: choice(Object.keys(CARRIERS), `物流：${Object.entries(CARRIERS).map(([c, n]) => `${c} ${n}`).join('、')}`),
        tracking_no: text('物流單號，只寫數字', [/^\d{8,14}$/, '8 到 14 位數字']) },
      ['order_no', 'carrier', 'tracking_no'],
      ({ order_no, carrier, tracking_no }) => {
        const o = order(order_no);
        if (o.status !== 'paid') fail(`訂單 ${order_no} 目前是 ${o.status}（${STATUS[o.status]}），只有已付款（paid）的訂單能出貨`);
        Object.assign(o, { status: 'shipped', shipment: { carrier, tracking_no } });
        return { order_no, status: o.status, shipment: { carrier, carrier_name: CARRIERS[carrier], tracking_no } };
      }),
    tool('cancel_order', '取消一張還沒出貨的訂單（會通知買家）。已出貨的訂單不能取消。',
      { order_no: text('訂單編號，例如 SO20260101-0007', ORDER), reason: text('取消原因，會寄給買家') },
      ['order_no', 'reason'],
      ({ order_no, reason }) => {
        const o = order(order_no);
        if (o.status === 'shipped') fail(`訂單 ${order_no} 已出貨，不能取消`);
        if (o.status === 'cancelled') fail(`訂單 ${order_no} 已經取消了`);
        o.status = 'cancelled';
        return { order_no, status: o.status, reason };
      }),
  ], 'zh');
}
