// One pending escalation card: the signal-at-danger block from the mockup.
// Agent text (why, tool, input) only ever lands in text nodes.
import { h, on, text } from '../core/h.js';
import { reducedMotion } from '../core/a11y.js';
import { countdownLabel, fuseVar, heldLabel, totalLabel } from './esc-time.js';

function ruleOf(e) {
  return e.tool ? 'escalate · ' + e.tool : 'escalate';
}

function labelOf(e) {
  return 'Escalation from ' + (e.agent || 'unknown') + (e.tool ? ', ' + e.tool : '');
}

function headSvg() {
  return h('svg', { class: 'head', viewBox: '0 0 22 34', 'aria-hidden': 'true', focusable: 'false' },
    h('rect', { x: '2', y: '1', width: '18', height: '20', rx: '9', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' }),
    h('circle', { class: 'lamp', cx: '11', cy: '11', r: '5.5', fill: 'currentColor' }),
    h('path', { d: 'M11 21v12', stroke: 'currentColor', 'stroke-width': '2.5', fill: 'none' }));
}

function statusLine() {
  return h('span', { class: 'st wait' },
    h('svg', { class: 'i', viewBox: '0 0 12 12', 'aria-hidden': 'true', focusable: 'false' },
      h('use', { href: '#s-wait' })),
    'held · waiting on you');
}

// The node keeps its live refs on _esc so the 1 s tick can patch
// countdown text and the fuse without rebuilding anything.
export function createSignal(e, api) {
  const calm = reducedMotion();
  const rule = h('span', { class: 'rule' }, ruleOf(e));
  const agent = h('b', { class: 'mono' }, String(e.agent || ''));
  const ws = h('span', { class: 'muted' }, '');
  const held = h('span', { class: 'faint' }, '');
  const why = h('p', { class: 'muted' }, String(e.why || ''));
  const cmd = h('pre', { class: 'mono' }, String(e.input || ''));
  const deny = h('button', { type: 'button', class: 'btn-deny' }, 'Deny');
  const allow = h('button', { type: 'button', class: 'btn-allow' }, 'Allow once');
  const cd = h('span', null, '');
  const total = h('span', { class: 'faint' }, '');
  const fuse = h('i', null);
  const err = h('p', { class: 'err', role: 'alert' }, '');
  const refs = { id: e.id, rule, agent, ws, held, why, cmd, deny, allow, cd, total, fuse, err };
  const card = h('div', {
    class: calm ? 'signal' : 'signal enter',
    role: 'region',
    'aria-label': labelOf(e),
    'data-id': String(e.id),
  },
    headSvg(),
    h('div', null,
      h('div', { class: 't1' }, rule, agent, ws, held),
      statusLine(),
      why,
      cmd),
    h('div', { class: 'acts' },
      h('div', { class: 'row2' }, deny, allow),
      h('div', { class: 'fuse', 'aria-hidden': 'true' }, fuse),
      h('div', { class: 'fuse-l' }, cd, total),
      err));
  card._esc = refs;

  async function decide(choice) {
    if (refs.deny.disabled || refs.allow.disabled) return;
    refs.deny.disabled = true;
    refs.allow.disabled = true;
    text(refs.err, '');
    const r = await api.post('/api/resolve', { escalation: refs.id, decision: choice, note: '' });
    if (r && r.error) {
      refs.deny.disabled = false;
      refs.allow.disabled = false;
      text(refs.err, String(r.error));
    }
  }
  on(deny, 'click', () => decide('deny'));
  on(allow, 'click', () => decide('allow'));
  updateSignal(card, e);
  return card;
}

export function updateSignal(node, e) {
  const r = node._esc;
  if (!r) return;
  r.id = e.id;
  node.setAttribute('data-id', String(e.id));
  node.setAttribute('aria-label', labelOf(e));
  text(r.rule, ruleOf(e));
  text(r.agent, String(e.agent || ''));
  text(r.ws, e.workspace ? 'in ' + e.workspace + ' wants to run' : 'wants to run');
  text(r.why, String(e.why || ''));
  const cmdText = String(e.input || '');
  text(r.cmd, cmdText);
  r.cmd.hidden = !cmdText;
}

export function tickSignal(node, e, now) {
  const r = node._esc;
  if (!r) return;
  const t = now === undefined ? Date.now() / 1000 : now;
  text(r.held, heldLabel(e, t));
  text(r.cd, countdownLabel(e, t));
  text(r.total, totalLabel(e));
  node.style.setProperty('--fuse', fuseVar(e, t));
}
