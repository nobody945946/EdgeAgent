// A generation stuck repeating itself, stopped as soon as it shows, so the
// Agent needs no output cap (owner, 2026-10-10: "只要不失控，輸出可以沒上限").
// The loops seen: agent-fw3 (2026-10-10) wrote one short unit over and over
// until the 8192-token cap, 'ts','ts','ts'... in a command, "cur","cur"... in
// a list; the teacher (Qwen3.8-27B, 2026-10-13) wrote one whole block of its
// thinking, 715 to 6,051 characters, again and again until its thinking
// budget cut it. A loop is the stream's last stretch being one unit of up to
// MAX_UNIT characters repeated exactly: a short unit over at least SPAN
// characters and at least TIMES times, a long one over at least LONG_SPAN
// characters and at least LONG_TIMES times. Real long output (a file, a
// table, a report) varies within such a stretch, and quotes a block again
// once or twice, not four times running.
// A thought also circles without repeating exactly: v3 (2026-10-16) went
// round the same few sentences in a shuffled order until the 65,536-token
// context was full (53,721 to 61,733 tokens in one step, 12 to 44 different
// lines in over a thousand). That is CIRCLE_LINES lines of at least LINE_MIN
// characters, the last of the stream, holding at most CIRCLE_DISTINCT
// different ones; a file, a table or a teacher's thought (none of 342,651
// recorded training messages) varies more.
const MAX_UNIT = 8000;
const SPAN = 2000;
const TIMES = 20;
const LONG_SPAN = 8000;
const LONG_TIMES = 4;

// How long the stretch repeating `unit` must be; never shorter for a longer unit.
function spanOf(unit) {
  return Math.max(SPAN, Math.min(unit * TIMES, LONG_SPAN), unit * LONG_TIMES);
}

// The length of the repeated unit ending `text`, or 0 when it is not looping.
export function looping(text) {
  for (let unit = 1; unit <= MAX_UNIT; unit++) {
    const span = spanOf(unit);
    if (text.length < span + unit) return 0;
    const start = text.length - span - unit;
    let periodic = true;
    for (let i = start; i < text.length - unit; i++) {
      if (text.charCodeAt(i) !== text.charCodeAt(i + unit)) {
        periodic = false;
        break;
      }
    }
    if (periodic) return unit;
  }
  return 0;
}

const LINE_MIN = 20;
const CIRCLE_LINES = 40;
const CIRCLE_DISTINCT = 10;

// True when the finished lines ending `text` circle round a few sentences.
export function circling(text) {
  const lines = text.slice(0, text.lastIndexOf('\n') + 1).split('\n')
    .map(line => line.trim()).filter(line => line.length >= LINE_MIN);
  return lines.length >= CIRCLE_LINES && new Set(lines.slice(-CIRCLE_LINES)).size <= CIRCLE_DISTINCT;
}

// Keeps the tail of one streamed message and says when it loops or circles;
// checked every CHECK_EVERY new characters, the tail long enough for the
// largest unit.
const CHECK_EVERY = 200;
const KEEP = spanOf(MAX_UNIT) + MAX_UNIT;

export class LoopWatch {
  constructor() { this.reset(); }

  reset() { this.tail = ''; this.fresh = 0; }

  // True once the message has started looping.
  add(delta) {
    this.tail = (this.tail + delta).slice(-KEEP);
    this.fresh += delta.length;
    if (this.fresh < CHECK_EVERY) return false;
    this.fresh = 0;
    return looping(this.tail) > 0 || circling(this.tail);
  }
}
