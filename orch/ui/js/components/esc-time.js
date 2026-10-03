// Pure escalation math for the bar and the history view. No DOM here,
// so node --test can cover it without a browser.
import { clock, duration } from '../core/fmt.js';

function num(v) {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Seconds left before the deadline, floored at zero; null when unknown. */
export function remainingS(e, now) {
  const d = num(e && e.deadline);
  if (d === null) return null;
  return Math.max(0, Math.floor(d - now));
}

/** Seconds since the escalation was raised, floored at zero; null when unknown. */
export function heldS(e, now) {
  const t = num(e && e.ts);
  if (t === null) return null;
  return Math.max(0, Math.floor(now - t));
}

/** Full fuse length (deadline minus raised), or null when it cannot be known. */
export function totalS(e) {
  const d = num(e && e.deadline);
  const t = num(e && e.ts);
  if (d === null || t === null || d <= t) return null;
  return Math.floor(d - t);
}

/** Fraction of the fuse left, 0..1. Unknown fuses read as full, spent ones as empty. */
export function fuseFrac(e, now) {
  const r = remainingS(e, now);
  const t = totalS(e);
  if (t === null) return r === null || r > 0 ? 1 : 0;
  return Math.min(1, Math.max(0, r / t));
}

/** Pending rows first by deadline, then by raised time, then by id. Returns a copy. */
export function sortPending(rows) {
  return (rows || [])
    .filter((e) => e && e.state === 'pending')
    .slice()
    .sort((a, b) => {
      const da = num(a.deadline);
      const db = num(b.deadline);
      const ka = da === null ? Infinity : da;
      const kb = db === null ? Infinity : db;
      if (ka !== kb) return ka - kb;
      const ta = num(a.ts) || 0;
      const tb = num(b.ts) || 0;
      if (ta !== tb) return ta - tb;
      const ia = String(a.id || '');
      const ib = String(b.id || '');
      return ia < ib ? -1 : ia > ib ? 1 : 0;
    });
}

export function heldLabel(e, now) {
  const s = heldS(e, now);
  return s === null ? '' : 'held ' + duration(s);
}

export function countdownLabel(e, now) {
  const r = remainingS(e, now);
  return r === null ? 'no deadline' : 'auto-stop in ' + clock(r);
}

export function totalLabel(e) {
  const t = totalS(e);
  return t === null ? '' : 'of ' + clock(t);
}

export function fuseVar(e, now) {
  return (fuseFrac(e, now) * 100).toFixed(1) + '%';
}
