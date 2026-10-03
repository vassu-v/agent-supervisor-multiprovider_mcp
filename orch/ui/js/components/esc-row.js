// Resolved-escalation rows for the history lane of the view.
// Glyph plus word plus colour for the state; agent text stays in text nodes.
import { h, text } from '../core/h.js';
import { ago } from '../core/fmt.js';

const STATE = {
  allow: ['idle', '#k-done', 'allowed'],
  deny: ['err', '#s-dead', 'denied'],
  timeout: ['err', '#s-err', 'timed out'],
};

function badge(state) {
  const b = STATE[state] || ['wait', '#s-wait', String(state || 'open')];
  return h('span', { class: 'st ' + b[0] },
    h('svg', { class: 'i', viewBox: '0 0 12 12', 'aria-hidden': 'true', focusable: 'false' },
      h('use', { href: b[1] })),
    b[2]);
}

function metaOf(e) {
  const when = ago(e.resolved || e.ts);
  const who = e.resolved_by ? 'by ' + e.resolved_by : '';
  return [who, when].filter(Boolean).join(' · ');
}

export function createHistRow(e) {
  const agent = h('b', { class: 'mono' }, String(e.agent || ''));
  const ws = h('span', { class: 'muted' }, String(e.workspace || ''));
  const tool = h('span', { class: 'faint' }, String(e.tool || ''));
  const input = h('pre', { class: 'mono' }, String(e.input || ''));
  input.hidden = !e.input;
  const note = h('p', { class: 'muted' }, String(e.note || ''));
  note.hidden = !e.note;
  const meta = h('span', { class: 'faint' }, metaOf(e));
  const row = h('div', { class: 'hrow', 'data-id': String(e.id) },
    badge(e.state), agent, ws, tool, input, note, meta);
  row._esch = { agent, ws, tool, input, note, meta };
  return row;
}

export function updateHistRow(node, e) {
  const r = node._esch;
  if (!r) return;
  text(r.agent, String(e.agent || ''));
  text(r.ws, String(e.workspace || ''));
  text(r.tool, String(e.tool || ''));
  const cmdText = String(e.input || '');
  text(r.input, cmdText);
  r.input.hidden = !cmdText;
  const noteText = String(e.note || '');
  text(r.note, noteText);
  r.note.hidden = !noteText;
  text(r.meta, metaOf(e));
}
