// Stand-in transport server shaped like the TDX (Transport Data eXchange)
// API, in English like the real one: PascalCase parameters, four-digit TRA
// station IDs that keep their leading zero, dates as YYYY-MM-DD, cities by
// their English codes, bus estimates in seconds. Every tool only reads (it
// marks none; the owner vouches for them in read_only_tools).
import { addDays, choice, fail, serve, text, tool } from './strict.mjs';

const DATA = {
  train: {
    today: '2026-09-18', now: '14:05',
    stations: { '0960': '汐止', 1210: '新竹', 3160: '苗栗', 3300: '臺中', 3360: '彰化', 4080: '嘉義', 4220: '臺南', 4400: '高雄' },
    trains: [
      ['111', 'Tze-Chiang', { '0960': '06:05', 1210: '07:14', 3160: '07:49', 3300: '08:33', 3360: '08:47', 4080: '09:36', 4220: '10:20', 4400: '10:55' }],
      ['115', 'Puyuma', { '0960': '08:20', 1210: '09:22', 3300: '10:31', 4080: '11:22', 4220: '12:01', 4400: '12:33' }],
      ['2131', 'Local', { 3300: '13:05', 3360: '13:22', 4080: '14:41' }],
      ['123', 'Tze-Chiang', { '0960': '15:40', 1210: '16:49', 3160: '17:25', 3300: '18:09', 3360: '18:23', 4080: '19:13', 4220: '19:57', 4400: '20:32' }],
      ['112', 'Tze-Chiang', { 4400: '07:10', 4220: '07:45', 4080: '08:29', 3360: '09:18', 3300: '09:32', 3160: '10:16', 1210: '10:52', '0960': '12:01' }],
      ['2142', 'Local', { 4220: '16:20', 4080: '17:28' }],
      ['128', 'Chu-Kuang', { 4400: '17:35', 4220: '18:14', 4080: '19:05', 3360: '20:01', 3300: '20:16' }],
      ['136', 'Puyuma', { 4400: '19:20', 4220: '19:52', 4080: '20:31', 3300: '21:24', 1210: '22:30', '0960': '23:31' }],
    ],
    delays: { 111: [0, '高雄'], 115: [3, '高雄'], 2131: [8, '彰化'], 112: [5, '汐止'], 123: [0, null], 2142: [0, null], 128: [0, null], 136: [0, null] },
    bus: [
      ['Taichung', '300', '臺中車站', 0, '靜宜大學', 780], ['Taichung', '300', '臺中車站', 1, '臺中車站', null],
      ['Taichung', '300', '科博館', 0, '靜宜大學', 1140], ['Taichung', '300', '科博館', 1, '臺中車站', 1080],
      ['Kaohsiung', '紅30', '捷運凹子底站', 0, '高雄大學', 960], ['Kaohsiung', '紅30', '捷運凹子底站', 1, '捷運凹子底站', null],
      ['Kaohsiung', '紅30', '高雄大學', 1, '捷運凹子底站', 300],
    ],
  },
  eval: {
    today: '2026-10-02', now: '14:05',
    stations: { 1020: '板橋', 1000: '臺北', '0990': '松山', 7190: '宜蘭', 7160: '羅東', 7000: '花蓮' },
    trains: [
      ['408', 'Puyuma', { 1020: '07:10', 1000: '07:20', '0990': '07:28', 7190: '08:33', 7160: '08:47', 7000: '09:46' }],
      ['412', 'Taroko', { 1020: '09:40', 1000: '09:50', '0990': '09:58', 7160: '11:12', 7000: '12:05' }],
      ['272', 'Tze-Chiang', { 1000: '13:15', '0990': '13:23', 7190: '14:41', 7160: '14:55', 7000: '16:08' }],
      ['422', 'Puyuma', { 1020: '18:20', 1000: '18:30', '0990': '18:38', 7190: '19:40', 7160: '19:53', 7000: '20:49' }],
      ['282', 'Tze-Chiang', { 1000: '19:05', '0990': '19:13', 7190: '20:35', 7160: '20:49', 7000: '22:02' }],
      ['417', 'Taroko', { 7000: '06:25', 7160: '07:18', '0990': '08:30', 1000: '08:39', 1020: '08:48' }],
      ['4231', 'Local', { 7190: '15:02', 7160: '15:15' }],
      ['433', 'Puyuma', { 7000: '16:30', 7160: '17:24', 7190: '17:36', '0990': '18:41', 1000: '18:50', 1020: '18:59' }],
    ],
    delays: { 408: [0, '花蓮'], 412: [12, '花蓮'], 272: [4, '宜蘭'], 417: [2, '板橋'], 4231: [0, null], 422: [0, null], 282: [0, null], 433: [0, null] },
    bus: [
      ['Taipei', '307', '捷運西門站', 0, '板橋', 660], ['Taipei', '307', '捷運西門站', 1, '撫遠街', 1320],
      ['Taipei', '307', '臺北車站', 0, '板橋', 240], ['Taipei', '307', '臺北車站', 1, '撫遠街', 1680],
      ['NewTaipei', '920', '板橋車站', 0, '捷運市政府站', 660], ['NewTaipei', '920', '板橋車站', 1, '板橋車站', null],
    ],
  },
};
const CITIES = ['Taipei', 'NewTaipei', 'Taoyuan', 'Taichung', 'Tainan', 'Kaohsiung', 'Keelung', 'Hsinchu', 'Chiayi'];
const TYPES = ['Tze-Chiang', 'Chu-Kuang', 'Puyuma', 'Taroko', 'Local'];
const BOOKING_DAYS = 28;

export default function transit(variant) {
  const data = DATA[variant];
  const stationName = (id, field) => data.stations[id]
    ?? fail(`Unknown ${field} '${id}'. Look up the 4-digit StationID with find_rail_station.`, { parameter: field });
  const last = addDays(data.today, BOOKING_DAYS);
  return serve([
    tool('find_rail_station', 'Find Taiwan Railway (TRA) stations by Chinese name. Returns StationID and StationName.',
      { StationName: text('Station name or part of it in Chinese, e.g. 新烏日') }, ['StationName'],
      ({ StationName }) => {
        const key = StationName.replaceAll('台', '臺').replace(/(火車站|車站|站)$/, '');
        return { Stations: Object.entries(data.stations).filter(([, name]) => name.includes(key) || key.includes(name))
          .map(([StationID, name]) => ({ StationID, StationName: name })) };
      }),
    tool('get_rail_timetable',
      'TRA trains from one station to another on a date, by departure time. Departure and Arrival are HH:mm (24-hour).',
      { OriginStationID: text('4-digit StationID of the departure station (from find_rail_station), as a string', [/^\d{4}$/, 'a 4-digit StationID such as 1008']),
        DestinationStationID: text('4-digit StationID of the arrival station, as a string', [/^\d{4}$/, 'a 4-digit StationID such as 1008']),
        TrainDate: text(`Travel date YYYY-MM-DD, from today up to ${BOOKING_DAYS} days ahead`, [/^\d{4}-\d{2}-\d{2}$/, 'YYYY-MM-DD, e.g. 2026-01-15']),
        DepartAfter: text('Only trains departing at or after this time, HH:mm (24-hour), e.g. 18:30', [/^([01]\d|2[0-3]):[0-5]\d$/, 'HH:mm (24-hour), e.g. 18:30']),
        TrainType: choice(TYPES, 'Only this train type') },
      ['OriginStationID', 'DestinationStationID', 'TrainDate'],
      ({ OriginStationID: from, DestinationStationID: to, TrainDate, DepartAfter, TrainType }) => {
        const origin = stationName(from, 'OriginStationID');
        const destination = stationName(to, 'DestinationStationID');
        if (TrainDate < data.today || TrainDate > last) {
          fail(`TrainDate out of range: timetables cover ${data.today} to ${last}.`, { parameter: 'TrainDate' });
        }
        const trains = data.trains
          .filter(([, type, stops]) => stops[from] && stops[to] && stops[from] < stops[to]
            && (!DepartAfter || stops[from] >= DepartAfter) && (!TrainType || type === TrainType))
          .map(([TrainNo, type, stops]) => ({ TrainNo, TrainType: type, Departure: stops[from], Arrival: stops[to] }))
          .sort((a, b) => a.Departure.localeCompare(b.Departure));
        return { TrainDate, Origin: origin, Destination: destination, Trains: trains };
      }),
    tool('get_train_delay', 'Live status of a TRA train today: DelayTime in minutes and the last station it passed.',
      { TrainNo: text('Train number, e.g. 152', [/^\d{1,4}$/, 'digits only, e.g. 152']) }, ['TrainNo'],
      ({ TrainNo }) => {
        const found = data.trains.find(([no]) => no === TrainNo);
        if (!found) fail(`No train ${TrainNo} runs today.`, { parameter: 'TrainNo' });
        const [delay, passed] = data.delays[TrainNo];
        return passed ? { TrainNo, TrainType: found[1], DelayTime: delay, LastPassedStation: passed, UpdateTime: `${data.today} ${data.now}` }
          : { TrainNo, TrainType: found[1], Status: 'Not departed yet', ScheduledDeparture: Object.values(found[2]).sort()[0] };
      }),
    tool('get_bus_arrival',
      'Estimated arrival of a city bus at a stop. EstimateTime is in seconds; null means no bus is coming (last bus gone).',
      { City: choice(CITIES, 'City code in English'), RouteName: text('Route name as on the bus, e.g. 紅2, 1503'),
        StopName: text('Stop name in Chinese, as on the stop sign'),
        Direction: choice([0, 1], '0 = outbound, 1 = return; omit for both') },
      ['City', 'RouteName', 'StopName'],
      ({ City, RouteName, StopName, Direction }) => {
        const route = data.bus.filter(([city, name]) => city === City && name === RouteName);
        if (!route.length) fail(`No route '${RouteName}' in ${City}.`, { parameter: 'RouteName' });
        const at = route.filter(([, , stop, direction]) => stop === StopName && (Direction === undefined || direction === Direction));
        if (!at.length) {
          fail(`No stop '${StopName}' on route ${RouteName} (${City}). Stops: ${[...new Set(route.map(r => r[2]))].join(', ')}`,
            { parameter: 'StopName' });
        }
        return { City, RouteName, StopName, Estimates: at.map(([, , , direction, headsign, seconds]) =>
          ({ Direction: direction, Destination: headsign, EstimateTime: seconds })) };
      }),
  ], 'en');
}
