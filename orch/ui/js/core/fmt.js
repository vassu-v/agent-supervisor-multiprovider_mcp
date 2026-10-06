// Small display formatters. Timestamps from the daemon are Unix seconds (float); now defaults to the clock.

import { ID_RE } from './router.js';

const nowS = () => Date.now() / 1000;
// accept milliseconds too, so a caller passing Date.now() does not get "55 years"
const toS = (t) => (t > 1e12 ? t / 1000 : t);

/**
 * Compact elapsed time since ts: "now", "45s", "3m", "2h", "5d".
 * @param {number|null|undefined} ts  Unix seconds (or ms)
 * @param {number} [now]
 * @returns {string} '' when ts is missing
 */
export function ago(ts, now = nowS()) {
  if (ts === null || ts === undefined || !Number.isFinite(Number(ts))) return '';
  const s = Math.max(0, toS(now) - toS(Number(ts)));
  if (s < 5) return 'now';
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}

/**
 * Duration in seconds: "12s", "1m12s", "4m", "2h05m", "3d4h".
 * @param {number} s
 * @returns {string}
 */
export function duration(s) {
  if (!Number.isFinite(s) || s < 0) return '';
  const t = Math.floor(s);
  if (t < 60) return t + 's';
  const m = Math.floor(t / 60), sec = t % 60;
  if (m < 10) return sec ? `${m}m${String(sec).padStart(2, '0')}s` : m + 'm';
  if (m < 60) return m + 'm';
  const hr = Math.floor(m / 60), min = m % 60;
  if (hr < 24) return min ? `${hr}h${String(min).padStart(2, '0')}m` : hr + 'h';
  const d = Math.floor(hr / 24), rh = hr % 24;
  return rh ? `${d}d${rh}h` : d + 'd';
}

/**
 * Countdown clock "m:ss" for deadlines; negative values clamp to "0:00".
 * @param {number} s
 * @returns {string}
 */
export function clock(s) {
  const t = Math.max(0, Math.floor(Number.isFinite(s) ? s : 0));
  return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0');
}

function short(n, unit, div) {
  const v = n / div;
  return (v < 10 ? (Math.round(v * 10) / 10).toString() : Math.round(v).toString()) + unit;
}

/**
 * Token counts: 950, "1.2k", "118k", "3.4M".
 * @param {number} n
 * @returns {string}
 */
export function tokens(n) {
  const v = Number(n) || 0;
  if (v < 1000) return String(Math.round(v));
  if (v < 999500) return short(v, 'k', 1e3);
  return short(v, 'M', 1e6);
}

/**
 * Byte sizes: "512 B", "1.2 KB", "34 MB".
 * @param {number} n
 * @returns {string}
 */
export function bytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return Math.round(v) + ' B';
  if (v < 1024 * 1024) return short(v, ' KB', 1024);
  if (v < 1024 ** 3) return short(v, ' MB', 1024 ** 2);
  return short(v, ' GB', 1024 ** 3);
}

/**
 * Return s when it is a valid id (^[A-Za-z0-9_-]{1,64}$), else null. Use before building ids, keys or routes.
 * @param {*} s
 * @returns {string|null}
 */
export function safeId(s) {
  return typeof s === 'string' && ID_RE.test(s) ? s : null;
}
