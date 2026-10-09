// The product's own web tools against the real web: a fixed list of sites the
// owner's questions reach (Taiwanese government, news, shops, docs, security
// databases) and six searches, so two machines' numbers compare. Pages are
// read as web_fetch reads them (with the machine's browser for script-built
// pages); nothing is kept but the counts and each page's length or reason.
//
//     node bench/web_live.mjs            (the summary line last)
import { webFetch, webResearch, browser } from '../edge_agent/web_tools.mjs';

const PAGES = [
  'https://zh.wikipedia.org/wiki/臺北捷運', 'https://en.wikipedia.org/wiki/Taipei_Metro',
  'https://nvd.nist.gov/vuln/detail/CVE-2023-23397', 'https://msrc.microsoft.com/update-guide/vulnerability/CVE-2023-23397',
  'https://www.cisa.gov/known-exploited-vulnerabilities-catalog', 'https://attack.mitre.org/techniques/T1570/',
  'https://www.cwa.gov.tw/V8/C/W/County/County.html?CID=63', 'https://www.cna.com.tw/', 'https://news.ltn.com.tw/list/breakingnews',
  'https://udn.com/news/index', 'https://www.ptt.cc/bbs/Tech_Job/index.html', 'https://www.mobile01.com/',
  'https://learn.microsoft.com/zh-tw/windows/win32/', 'https://docs.python.org/3/library/pathlib.html',
  'https://github.com/python/cpython', 'https://pypi.org/pypi/requests/json', 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
  'https://www.nvidia.com/en-us/geforce/drivers/', 'https://www.tsmc.com/chinese', 'https://www.gov.tw/',
  'https://www.reuters.com/', 'https://www.bbc.com/zhongwen/trad', 'https://store.steampowered.com/app/1091500/',
  'https://www.warhammer-community.com/', 'https://www.thsrc.com.tw/', 'https://www.railway.gov.tw/tra-tip-web/tip',
  'https://www.nhi.gov.tw/', 'https://www.etax.nat.gov.tw/', 'https://24h.pchome.com.tw/', 'https://shopee.tw/',
];
const SEARCHES = [
  ['台北明天天氣', ['台北 明天 天氣預報']], ['CVE-2023-23397 CVSS', ['CVE-2023-23397 CVSS score']],
  ['RTX 5070 價格', ['RTX 5070 台灣 售價']], ['台積電股價', ['台積電 股價 今天']],
  ['Python pathlib glob 用法', ['python pathlib glob recursive']], ['戰鎚40K 第十版 規則', ['Warhammer 40k 10th edition rules']],
];
const READ = 500;  // fewer characters than this is a shell, a login wall or an error page

const lines = [];
const count = { read: 0, thin: 0, bot: 0, failed: 0, answered: 0 };
for (const url of PAGES) {
  const started = Date.now();
  try {
    const text = (await webFetch(url, 40000)).split('\n').slice(2).join('\n');
    const kind = text.length >= READ && !text.includes('builds its content with scripts') ? 'read' : 'thin';
    count[kind]++;
    lines.push(`${kind}\t${Date.now() - started} ms\t${text.length} chars\t${url}`);
  } catch (error) {
    const kind = /bot check/.test(error.message) ? 'bot' : 'failed';
    count[kind]++;
    lines.push(`${kind}\t${Date.now() - started} ms\t${error.message.slice(0, 120)}\t${url}`);
  }
}
for (const [question, queries] of SEARCHES) {
  const started = Date.now();
  try {
    const report = await webResearch(question, queries);
    const passages = (report.match(/^\[\d+\] /gm) ?? []).length;
    if (passages) count.answered++;
    lines.push(`search\t${Date.now() - started} ms\t${passages} passages\t${question}`);
  } catch (error) {
    lines.push(`search\t${Date.now() - started} ms\t${error.message.slice(0, 120)}\t${question}`);
  }
}
console.log(lines.join('\n'));
console.log(`web live: ${count.read} of ${PAGES.length} pages read, ${count.thin} thin, ${count.bot} behind a bot check, `
  + `${count.failed} failed; ${count.answered} of ${SEARCHES.length} searches answered; browser ${browser() || 'none'}`);
