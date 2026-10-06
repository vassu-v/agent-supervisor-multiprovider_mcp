// Drawer pure logic + line builders. No store/api writes here; node-tested via tests/ui/test_drawer.mjs.
// Agent-authored strings are returned as strings; callers must place them in text nodes.
import { h } from '../core/h.js';
import { ago } from '../core/fmt.js';
import { safeId } from '../core/fmt.js';

export const STREAM_MAX = 500;

// Merge an incremental /api/events page: dedupe by seq, sort, keep the last `max`.
export function mergeStream(existing, incoming, max = STREAM_MAX) {
  const m = new Map();
  for (const e of existing || []) if (e && e.seq !== undefined && e.seq !== null) m.set(e.seq, e);
  for (const e of incoming || []) if (e && e.seq !== undefined && e.seq !== null) m.set(e.seq, e);
  return [...m.values()].sort((a, b) => a.seq - b.seq).slice(-Math.max(1, max));
}

// Agent id from the route (app.js opens the drawer when route.params.agent is set).
export function drawerAgentId(store) {
  const s = store.get(), r = s && s.ui && s.ui.route;
  return safeId(r && r.params && r.params.agent);
}
export function findAgent(store, id) {
  if (!id) return null;
  return ((store.get() && store.get().agents) || []).find((a) => a && a.id === id) || null;
}
export function capsOf(a) { return (a && a.caps) || {}; }
export function canSteer(a) { return Boolean(capsOf(a).steer); }
export function canInterrupt(a) { return Boolean(capsOf(a).interrupt); }
export function stopReasonError(reason) {
  return String(reason === undefined || reason === null ? '' : reason).trim()
    ? null : 'A reason is required to stop an agent.';
}
// One text line for an event; place in a text node.
export function lineText(ev) {
  const e = ev || {};
  switch (e.type) {
    case 'text': return String(e.text || '');
    case 'tool_start': return 'tool ' + String(e.tool || e.id || '') + ' started';
    case 'tool_end': return 'tool ' + String(e.tool || e.id || '') + (e.ok === false ? ' failed' : ' done');
    case 'status': return 'status ' + String(e.state || '') + (e.restarted ? ' (restarted)' : '');
    case 'error': return 'error ' + String(e.message || '');
    case 'guard': return 'guard ' + String(e.decision || '') + (e.why ? ' ' + String(e.why) : '');
    default: return String(e.type || 'event');
  }
}
// One-line turn summary; place in a text node.
export function describeTurn(t, i) {
  const u = t || {}, open = !('response' in u);
  const st = u.interrupted ? 'interrupted' : open ? 'running' : u.ok === false ? 'failed' : 'done';
  const x = (u.secs !== undefined ? ' ' + u.secs + 's' : '') + (u.steers && u.steers.length ? ' +' + u.steers.length + ' steer' : '');
  const sn = String(u.response || u.partial || u.prompt || '').slice(-120);
  return 'turn ' + (i + 1) + ': ' + st + x + (sn ? ' — ' + sn : '');
}
// One stream row. All agent text goes in text nodes.
export function createLine(ev) {
  return h('div', { class: 'dw-line', 'data-seq': ev.seq },
    h('span', { class: 'faint mono' }, '#' + ev.seq + ' ' + (ago(ev.ts) || '') + ' ' + String(ev.type || '')),
    h('p', { class: 'dw-text' }, lineText(ev)));
}
export function createTurn(t, i) {
  return h('div', { class: 'dw-turn mono' }, describeTurn(t, i));
}
