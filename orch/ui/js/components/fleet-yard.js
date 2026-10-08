// One workspace yard: header counts, treegrid rows, dead fold and the last-5
// board rail (fleet-lanes.js). Yards and rows are keyed lists; the header is patched.

import { h, on, text, list } from '../core/h.js';
import { tokens as fmtTokens } from '../core/fmt.js';
import { railWidth } from './fleet-rail.js';
import { createRow, updateRow } from './fleet-row.js';
import { createBoard, updateBoard } from './fleet-lanes.js';

const setText = text;

function countSpan(num, label) {
  const b = h('b', null, String(num));
  return { el: h('span', null, b, ' ' + label), num: b };
}

function fillClients(wrap, sessions) {
  const sig = (sessions || []).map((s) => `${s.client}/${s.label || ''}`).join('\u0001');
  if (wrap._sig === sig) return;
  wrap._sig = sig;
  wrap.replaceChildren(...(sessions || []).map((s) =>
    h('span', { class: 'client' }, s.label ? `${s.client}/${s.label}` : s.client)));
}

/**
 * @param {object} y  yard view-model from buildFleet()
 * @param {number} now  Unix seconds
 * @param {{open, toggleDead, toggleYard, hl, selected}} hooks
 */
export function createYard(y, now, hooks) {
  const sec = h('section', { class: 'yard', 'aria-label': 'Workspace ' + y.name });
  const h2 = h('h2', null,
    h('svg', { 'aria-hidden': 'true', focusable: 'false' }, h('use', { href: '#k-yard' })), y.name);
  const counts = h('div', { class: 'counts' });
  const cAgents = countSpan(y.agents, y.agents === 1 ? 'agent' : 'agents');
  const cBusy = countSpan(y.busy, 'busy');
  const cWait = countSpan(y.waiting, 'waiting on you');
  const cQ = countSpan(y.questions, y.questions === 1 ? 'open question' : 'open questions');
  const cTok = countSpan(fmtTokens(y.tokens), 'tokens');
  cTok.num.removeAttribute('class');
  counts.append(cAgents.el, cBusy.el, cWait.el, cQ.el, cTok.el);
  const clients = h('div', { class: 'clients', 'aria-label': 'Attached clients' });
  fillClients(clients, y.sessions);
  const head = h('header', { class: 'yard-h' }, h2,
    y.root ? h('span', { class: 'path' }, y.root) : null,
    y.branch ? h('span', { class: 'br' }, y.branch) : null,
    counts, clients);
  sec.appendChild(head);
  const body = h('div');
  sec.appendChild(body);
  sec._y = { y, head, body, counts: { cAgents, cBusy, cWait, cQ, cTok }, clients, W: 0, collapsed: null };
  updateYard(sec, y, now, hooks);
  return sec;
}

/** Patch a yard created by createYard(). Structural switches are rare. */
export function updateYard(sec, y, now, hooks) {
  const f = sec._y;
  f.y = y;
  const c = f.counts;
  setText(c.cAgents.num, y.agents);
  setText(c.cBusy.num, y.busy);
  setText(c.cWait.num, y.waiting);
  c.cWait.el.classList.toggle('warn', y.waiting > 0);
  setText(c.cQ.num, y.questions);
  setText(c.cTok.num, fmtTokens(y.tokens));
  fillClients(f.clients, y.sessions);
  const W = railWidth(y.maxDepth || 0);
  if (f.collapsed !== y.collapsed) {
    f.collapsed = y.collapsed;
    f.body.replaceChildren();
    if (y.collapsed) {
      const btn = on(h('button', { type: 'button', 'aria-expanded': 'false' }), 'click', () => hooks.toggleYard(y.id));
      const sum = h('span', { class: 'muted' });
      f.body.appendChild(h('div', { class: 'yard-sum' }, sum, btn));
      f.sum = sum;
      f.sumBtn = btn;
    } else {
      const rows = h('div', { class: 'rows', role: 'treegrid', 'aria-label': 'Agents in ' + y.name });
      rows.appendChild(h('div', { class: 'colh', 'aria-hidden': 'true' },
        h('span', null, 'agent · goal'), h('span', null, 'provider · model'),
        h('span', { class: 'r' }, 'tokens')));
      const rowlist = h('div', { class: 'rowlist' });
      rows.appendChild(rowlist);
      const foldWrap = h('div');
      rows.appendChild(foldWrap);
      const board = createBoard(y, hooks);
      const wrap = h('div', { class: 'yard-b' }, rows, board);
      f.body.appendChild(wrap);
      f.W = 0;
      f.rows = rows;
      f.rowlist = rowlist;
      f.foldWrap = foldWrap;
      f.board = board;
    }
  }
  if (y.collapsed) {
    setText(f.sum, `${y.live} live \u00B7 ${y.busy} busy \u00B7 ${fmtTokens(y.tokens)} tokens`);
    setText(f.sumBtn, `Show ${y.live} agents`);
    return;
  }
  if (f.W !== W) { f.W = W; f.rows.style.setProperty('--rail-w', W + 'px'); }   // grid gutter follows the rail depth
  // density is part of the key: compact rows have another shape, so a switch rebuilds them
  const key = (vm) => vm.agent.id + (hooks.compact ? '|c' : '');
  list(f.rowlist, y.rows, key,
    (vm) => createRow(vm, f.W, hooks),
    (el, vm) => updateRow(el, vm, f.W, hooks));
  const foldSig = y.folded ? `fold:${y.deadCount}` : (y.canFold ? `open:${y.deadCount}` : 'none');
  if (f.foldWrap._sig !== foldSig) {
    f.foldWrap._sig = foldSig;
    f.foldWrap.replaceChildren();
    if (y.canFold) {
      const label = y.folded ? `${y.deadCount} finished` : `Hide finished (${y.deadCount})`;
      const btn = on(h('button', { type: 'button', class: 'fold', 'aria-expanded': y.folded ? 'false' : 'true' }, label), 'click', () => hooks.toggleDead(y.id));
      f.foldWrap.appendChild(btn);
    }
  }
  updateBoard(f.board, y, now, hooks);
}
