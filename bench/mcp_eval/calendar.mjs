// Stand-in calendar server shaped like a Google Calendar MCP server, in
// English: events between two RFC 3339 times (with the +08:00 offset), free
// slots on a day, events created with a start and a length in minutes and
// deleted by event_id. The calendar keeps its changes for the session. There
// is no tool that edits an event in place.
import { fail, flag, list, serve, text, tool, whole, withDefault } from './strict.mjs';

const DATA = {
  train: {
    today: '2026-09-18',
    events: [
      ['evt_t02', '週會', '2026-09-21T09:30', 60, '三樓會議室'],
      ['evt_t01', '牙醫回診', '2026-09-22T10:30', 45, '仁愛牙醫診所'],
      ['evt_t04', '專案檢討會', '2026-09-22T14:00', 90, '線上'],
      ['evt_t03', '和小芳吃晚餐', '2026-09-23T19:00', 120, '信義區'],
      ['evt_t05', '健身房', '2026-09-24T07:00', 60, ''],
      ['evt_t06', '銀行辦事', '2026-09-23T11:00', 30, '中山分行'],
    ],
  },
  eval: {
    today: '2026-10-02',
    events: [
      ['evt_e02', '部門會議', '2026-10-05T10:00', 60, '五樓會議室'],
      ['evt_e01', '眼科檢查', '2026-10-06T09:00', 40, '明亮眼科'],
      ['evt_e03', '客戶簡報', '2026-10-06T14:30', 90, '內湖辦公室'],
      ['evt_e04', '瑜伽課', '2026-10-07T19:30', 60, '運動中心'],
      ['evt_e06', '午餐會報', '2026-10-07T12:00', 60, '公司餐廳'],
      ['evt_e05', '爸爸生日聚餐', '2026-10-08T18:30', 150, '鼎泰豐信義店'],
    ],
  },
};
const ZONE = '+08:00';
const HOUR = 3600000;
const STAMP = [/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$/, 'RFC 3339 date-time with offset, e.g. 2026-01-15T09:00:00+08:00'];
const READ = { readOnlyHint: true, openWorldHint: false };

// An instant as Taiwan local time, RFC 3339.
const local = ms => `${new Date(ms + 8 * HOUR).toISOString().slice(0, 19)}${ZONE}`;
const clock = ms => local(ms).slice(11, 16);

export default function calendar(variant) {
  const data = DATA[variant];
  const events = data.events.map(([event_id, summary, start, minutes, location]) => {
    const at = Date.parse(`${start}:00${ZONE}`);
    return { event_id, summary, start: at, end: at + minutes * 60000, location, attendees: [] };
  });
  let created = 0;
  const shown = e => ({ event_id: e.event_id, summary: e.summary, start: local(e.start), end: local(e.end),
    ...(e.location ? { location: e.location } : {}), ...(e.attendees.length ? { attendees: e.attendees } : {}) });
  return serve([
    tool('list_events', 'Events overlapping a time range, in start order. Times are RFC 3339 in Taiwan time (+08:00).',
      { time_min: text('Range start, RFC 3339 with offset, e.g. 2026-01-15T00:00:00+08:00', STAMP),
        time_max: text('Range end (exclusive), RFC 3339 with offset', STAMP),
        query: text('Only events whose title contains this text') },
      ['time_min', 'time_max'],
      ({ time_min, time_max, query }) => {
        const from = Date.parse(time_min);
        const to = Date.parse(time_max);
        if (Number.isNaN(from) || Number.isNaN(to)) fail('time_min or time_max is not a real date and time.');
        if (!(from < to)) fail('time_min must be before time_max.', { parameter: 'time_min' });
        return { events: events.filter(e => e.start < to && e.end > from && (!query || e.summary.includes(query)))
          .sort((a, b) => a.start - b.start).map(shown) };
      }, READ),
    tool('find_free_time', 'Free slots of at least a given length on one day, as HH:MM local time.',
      { date: text('Day, YYYY-MM-DD', [/^\d{4}-\d{2}-\d{2}$/, 'YYYY-MM-DD, e.g. 2026-01-15']),
        duration_minutes: whole('Minimum slot length in minutes, 15-480', 15, 480),
        work_hours_only: withDefault(flag('Only 09:00-18:00 (default true); false = 08:00-22:00'), true) },
      ['date', 'duration_minutes'],
      ({ date, duration_minutes, work_hours_only }) => {
        const [open, close] = work_hours_only ? ['09:00', '18:00'] : ['08:00', '22:00'];
        const dayStart = Date.parse(`${date}T${open}:00${ZONE}`);
        const dayEnd = Date.parse(`${date}T${close}:00${ZONE}`);
        const busy = events.filter(e => e.start < dayEnd && e.end > dayStart).sort((a, b) => a.start - b.start);
        const free = [];
        let cursor = dayStart;
        for (const e of [...busy, { start: dayEnd, end: dayEnd }]) {
          if (e.start - cursor >= duration_minutes * 60000) free.push({ start: clock(cursor), end: clock(e.start) });
          cursor = Math.max(cursor, e.end);
        }
        return { date, free };
      }, READ),
    tool('create_event', 'Add an event to the calendar.',
      { summary: text('Event title'), start: text('Start, RFC 3339 with offset, e.g. 2026-01-15T09:00:00+08:00', STAMP),
        duration_minutes: whole('Length in minutes, 5-720', 5, 720), location: text('Where'),
        attendees: list(text('Email address', [/^[^@\s]+@[^@\s]+\.[^@\s]+$/, 'an email address']), 'Guests to invite, by email') },
      ['summary', 'start', 'duration_minutes'],
      ({ summary, start, duration_minutes, location, attendees }) => {
        const at = Date.parse(start);
        if (Number.isNaN(at)) fail(`start '${start}' is not a real date and time.`, { parameter: 'start' });
        created += 1;
        const event = { event_id: `evt_n${String(created).padStart(2, '0')}`, summary, start: at, end: at + duration_minutes * 60000,
          location: location ?? '', attendees: attendees ?? [] };
        const overlaps = events.filter(e => e.start < event.end && e.end > event.start).map(e => e.summary);
        events.push(event);
        return { ...shown(event), ...(overlaps.length ? { warning: `Overlaps with: ${overlaps.join(', ')}` } : {}) };
      }, { readOnlyHint: false, destructiveHint: false, openWorldHint: false }),
    tool('delete_event', 'Delete an event by its event_id (from list_events). This cannot be undone.',
      { event_id: text('The event_id from list_events, e.g. evt_a01'), notify_attendees: withDefault(flag('Email the guests about it (default false)'), false) },
      ['event_id'],
      ({ event_id, notify_attendees }) => {
        const index = events.findIndex(e => e.event_id === event_id);
        if (index < 0) fail(`No event with event_id '${event_id}'. Use list_events to find it.`, { parameter: 'event_id' });
        const [gone] = events.splice(index, 1);
        return { deleted: event_id, summary: gone.summary, notified: notify_attendees && gone.attendees.length > 0 };
      }, { readOnlyHint: false, destructiveHint: true, openWorldHint: false }),
  ], 'en');
}
