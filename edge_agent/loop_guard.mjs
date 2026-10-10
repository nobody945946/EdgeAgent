// A generation stuck repeating itself, stopped as soon as it shows, so the
// Agent needs no output cap (owner, 2026-10-10: "只要不失控，輸出可以沒上限").
// The loops seen (agent-fw3, 2026-10-10) wrote one short unit over and over
// until the 8192-token cap: 'ts','ts','ts'... in a command, "cur","cur"... in
// a list. A loop is the stream's last stretch being one unit of up to
// MAX_UNIT characters repeated exactly over at least SPAN characters and at
// least TIMES times; real long output (a file, a table, a report) varies
// within such a stretch.
const MAX_UNIT = 400;
const SPAN = 2000;
const TIMES = 20;

// The length of the repeated unit ending `text`, or 0 when it is not looping.
export function looping(text) {
  for (let unit = 1; unit <= MAX_UNIT; unit++) {
    const span = Math.max(SPAN, unit * TIMES);
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

// Keeps the tail of one streamed message and says when it loops; checked
// every CHECK_EVERY new characters, the tail long enough for the largest unit.
const CHECK_EVERY = 200;
const KEEP = MAX_UNIT * (TIMES + 1);

export class LoopWatch {
  constructor() { this.reset(); }

  reset() { this.tail = ''; this.fresh = 0; }

  // True once the message has started looping.
  add(delta) {
    this.tail = (this.tail + delta).slice(-KEEP);
    this.fresh += delta.length;
    if (this.fresh < CHECK_EVERY) return false;
    this.fresh = 0;
    return looping(this.tail) > 0;
  }
}
