// Stand-in drone shaped like mavlink-mcp, in English: read-only status and
// position, flight actions that would move hardware. The vehicle keeps its
// state for the session like a real one: it takes off only once armed, lands
// or returns only from the air, and its status shows what the last command
// did (MCP_DRONE_START=airborne starts it flying, for a task that brings it
// back). A refused command is the vehicle's answer (result DENIED), not an
// error; arguments the schema does not declare are an error (strict.mjs).
import { serve, tool } from './strict.mjs';

const DATA = {
  eval: {
    status: { armed: false, mode: 'STABILIZE', battery_voltage: 16.42, battery_percent: 87, gps_fix: '3D', satellites: 14 },
    position: { lat: 25.0339, lon: 121.5645, relative_alt_m: 0.0 },
  },
  train: {
    status: { armed: false, mode: 'LOITER', battery_voltage: 15.88, battery_percent: 64, gps_fix: '3D', satellites: 11 },
    position: { lat: 24.1477, lon: 120.6736, relative_alt_m: 0.0 },
  },
};
const READ = { readOnlyHint: true, openWorldHint: false };
const MOVE = { readOnlyHint: false, destructiveHint: true };

export default function drone(variant) {
  const data = DATA[variant];
  const home = { ...data.position };
  const vehicle = { status: { ...data.status }, position: { ...data.position } };
  if (process.env.MCP_DRONE_START === 'airborne') {
    Object.assign(vehicle.status, { armed: true, mode: 'GUIDED' });
    vehicle.position.relative_alt_m = 20.0;
  }
  const flying = () => vehicle.position.relative_alt_m > 0;
  const denied = reason => ({ result: 'DENIED', reason });
  function fly(command, a) {
    const { status, position } = vehicle;
    if (command === 'arm') {
      Object.assign(status, { armed: true, mode: flying() ? status.mode : 'GUIDED' });
      return { result: 'ACCEPTED' };
    }
    if (command === 'takeoff') {
      if (!status.armed) return denied('Vehicle is not armed. Arm it first.');
      if (flying()) return denied('Vehicle is already airborne.');
      position.relative_alt_m = Number(a.altitude_m) || 0;
      status.mode = 'GUIDED';
      return { result: 'ACCEPTED', target_altitude_m: a.altitude_m };
    }
    if (!flying()) return denied('Vehicle is on the ground. Take off first.');
    if (command === 'goto') {
      Object.assign(position, { lat: a.lat, lon: a.lon, relative_alt_m: a.altitude_m ?? position.relative_alt_m });
      return { result: 'ACCEPTED', ...a };
    }
    if (command === 'return_to_launch') Object.assign(position, home);
    position.relative_alt_m = 0.0;
    Object.assign(status, { armed: false, mode: command === 'land' ? 'LAND' : 'RTL' });
    return { result: 'ACCEPTED' };
  }
  const number = { type: 'number' };
  return serve([
    tool('get_status', 'Vehicle status: armed, flight mode, battery voltage and percentage', {}, [], () => vehicle.status, READ),
    tool('get_position', 'Current GPS position and altitude above home', {}, [], () => vehicle.position, READ),
    tool('arm', 'Arm the motors', {}, [], a => fly('arm', a), MOVE),
    tool('takeoff', 'Take off to an altitude in metres above home', { altitude_m: number }, [], a => fly('takeoff', a), MOVE),
    tool('land', 'Land at the current position', {}, [], a => fly('land', a), MOVE),
    tool('return_to_launch', 'Fly back to home and land (RTL)', {}, [], a => fly('return_to_launch', a), MOVE),
    tool('goto', 'Fly to a latitude/longitude at an altitude', { lat: number, lon: number, altitude_m: number }, [],
      a => fly('goto', a), MOVE),
  ], 'en');
}
