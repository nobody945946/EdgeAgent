// Stand-in smart-home server shaped like a Home Assistant MCP server, in
// English: devices by entity_id (domain.object_id) found by area, lights and
// air conditioners set by their own tools, whole-house energy by date range.
// The house keeps its state for the session: a light switched off stays off
// and get_state shows it. Reads say readOnlyHint; settings change things but
// can be set back (destructiveHint false).
import { addDays, choice, daysBetween, fail, flag, number, serve, spread, text, tool, whole } from './strict.mjs';

const AREAS = ['living_room', 'bedroom', 'kitchen', 'study', 'bathroom', 'balcony'];
const MODES = ['off', 'cool', 'heat', 'dry', 'fan_only'];
const DATA = {
  train: {
    today: '2026-09-18',
    devices: [
      ['light.living_room_ceiling', '客廳吸頂燈', 'living_room', { on: true, brightness_pct: 80 }],
      ['light.living_room_floor', '客廳落地燈', 'living_room', { on: false, brightness_pct: 0 }],
      ['light.bedroom_ceiling', '臥室主燈', 'bedroom', { on: false, brightness_pct: 0 }],
      ['climate.bedroom_ac', '臥室冷氣', 'bedroom', { hvac_mode: 'off', temperature: 26, current_temperature: 29.5 }],
      ['light.kitchen', '廚房燈', 'kitchen', { on: true, brightness_pct: 100 }],
      ['sensor.balcony_temperature', '陽台溫度', 'balcony', { value: 31.8, unit: '°C' }],
      ['sensor.living_room_humidity', '客廳濕度', 'living_room', { value: 67, unit: '%' }],
    ],
  },
  eval: {
    today: '2026-10-02',
    devices: [
      ['light.living_room_main', '客廳大燈', 'living_room', { on: true, brightness_pct: 70 }],
      ['light.tv_backlight', '電視背光燈', 'living_room', { on: true, brightness_pct: 40 }],
      ['light.study_desk', '書房檯燈', 'study', { on: false, brightness_pct: 0 }],
      ['climate.study_ac', '書房冷氣', 'study', { hvac_mode: 'off', temperature: 27, current_temperature: 30.2 }],
      ['light.bedroom_bedside', '床頭燈', 'bedroom', { on: true, brightness_pct: 30 }],
      ['sensor.bathroom_humidity', '浴室濕度', 'bathroom', { value: 84, unit: '%' }],
      ['sensor.study_temperature', '書房溫度', 'study', { value: 30.2, unit: '°C' }],
    ],
  },
};
const ENERGY_DAYS = 60;
const READ = { readOnlyHint: true, openWorldHint: false };
const SET = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false };

export default function home(variant) {
  const data = DATA[variant];
  const devices = new Map(data.devices.map(([id, name, area, state]) => [id, { name, area, state: { ...state } }]));
  const device = (id, domain) => {
    const d = devices.get(id) ?? fail(`Unknown entity_id '${id}'. Use list_devices to see entity IDs.`, { parameter: 'entity_id' });
    if (domain && !id.startsWith(`${domain}.`)) {
      fail(`'${id}' is not a ${domain} entity; this tool controls ${domain}.* entities only.`, { parameter: 'entity_id' });
    }
    return d;
  };
  const summary = d => d.state.on !== undefined ? (d.state.on ? 'on' : 'off') : d.state.hvac_mode ?? `${d.state.value} ${d.state.unit}`;
  // Whole-house kWh per day in tenths, so a total is exact.
  const kwhTenths = day => 62 + spread(variant, day, 90);
  const ENTITY = [/^[a-z_]+\.[a-z0-9_]+$/, 'domain.object_id, e.g. light.hallway'];
  const DAY = [/^\d{4}-\d{2}-\d{2}$/, 'YYYY-MM-DD, e.g. 2026-01-15'];
  const first = addDays(data.today, -ENERGY_DAYS);
  const last = addDays(data.today, -1);
  return serve([
    tool('list_devices', 'Devices in the home with entity_id, friendly name, area and current state.',
      { area: choice(AREAS, 'Only devices in this area') }, [],
      ({ area }) => ({ devices: [...devices].filter(([, d]) => !area || d.area === area)
        .map(([entity_id, d]) => ({ entity_id, name: d.name, area: d.area, state: summary(d) })) }), READ),
    tool('get_state', 'Full state of one device: lights on/off and brightness_pct, climate hvac_mode, target temperature and current_temperature (°C), sensor value and unit.',
      { entity_id: text('Entity ID, e.g. light.hallway (from list_devices)', ENTITY) }, ['entity_id'],
      ({ entity_id }) => {
        const d = device(entity_id);
        return { entity_id, name: d.name, area: d.area, ...d.state };
      }, READ),
    tool('set_light', 'Turn a light on or off, optionally at a brightness.',
      { entity_id: text('Entity ID of a light, e.g. light.hallway', ENTITY), on: flag('true = turn on, false = turn off'),
        brightness_pct: whole('Brightness 1-100 (%), only when turning on', 1, 100) },
      ['entity_id', 'on'],
      ({ entity_id, on, brightness_pct }) => {
        const d = device(entity_id, 'light');
        if (!on && brightness_pct !== undefined) fail('brightness_pct applies only when on is true.', { parameter: 'brightness_pct' });
        d.state = { on, brightness_pct: on ? brightness_pct ?? (d.state.brightness_pct || 100) : 0 };
        return { entity_id, name: d.name, ...d.state };
      }, SET),
    tool('set_climate', 'Set an air conditioner\'s mode and, for cool or heat, its target temperature.',
      { entity_id: text('Entity ID of a climate device, e.g. climate.hallway', ENTITY), hvac_mode: choice(MODES, 'Operating mode'),
        temperature: number('Target temperature in °C, 16-30 (cool or heat only)', 16, 30) },
      ['entity_id', 'hvac_mode'],
      ({ entity_id, hvac_mode, temperature }) => {
        const d = device(entity_id, 'climate');
        if (temperature !== undefined && !['cool', 'heat'].includes(hvac_mode)) {
          fail(`temperature applies only to hvac_mode cool or heat, not ${hvac_mode}.`, { parameter: 'temperature' });
        }
        Object.assign(d.state, { hvac_mode, ...(temperature === undefined ? {} : { temperature }) });
        return { entity_id, name: d.name, ...d.state };
      }, SET),
    tool('get_energy', `Whole-house electricity use per day in kWh (the last ${ENERGY_DAYS} days up to yesterday), with the total.`,
      { start_date: text('First day, YYYY-MM-DD', DAY), end_date: text('Last day (inclusive), YYYY-MM-DD', DAY) },
      ['start_date', 'end_date'],
      ({ start_date, end_date }) => {
        if (start_date > end_date) fail('start_date must not be after end_date.', { parameter: 'start_date' });
        if (start_date < first || end_date > last) {
          fail(`Energy history covers ${first} to ${last}.`, { parameter: start_date < first ? 'start_date' : 'end_date' });
        }
        const days = Array.from({ length: daysBetween(start_date, end_date) + 1 }, (_, i) => addDays(start_date, i))
          .map(date => ({ date, tenths: kwhTenths(date) }));
        return { unit: 'kWh', days: days.map(({ date, tenths }) => ({ date, kwh: tenths / 10 })),
          total_kwh: days.reduce((sum, d) => sum + d.tenths, 0) / 10 };
      }, READ),
  ], 'en');
}
