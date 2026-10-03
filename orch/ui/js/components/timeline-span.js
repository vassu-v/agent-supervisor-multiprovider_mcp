import { SPANS } from '../core/router.js';
import { MAX_MARKS } from './timeline-layout.js';
export const SPAN_SECS = { '15m': 900, '1h': 3600, '6h': 21600 };
export const DEFAULT_SPAN = '15m';
export const normSpan = (s) => (SPANS.includes(s) ? s : DEFAULT_SPAN);
export const spanSecs = (s) => SPAN_SECS[normSpan(s)];
export const windowFor = (s, n) => ({ from: n - spanSecs(s), to: n });
export function timelineUrl(ws, s, n) {
  const w = windowFor(s, n);
  return '/api/timeline?' + (ws ? 'ws=' + encodeURIComponent(ws) : 'all=1') +
    '&since=' + Math.floor(w.from) + '&until=' + Math.floor(w.to) + '&max=' + MAX_MARKS;
}
