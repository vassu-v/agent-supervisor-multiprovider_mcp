// Escalations: the pinned bar (every view) plus the full view.
// Reads the store, writes only through api.post, never announces
// (sync.js owns the assertive live region) and binds no keys:
// app.js already jumps to the first bar button on "e", and allow
// and deny deliberately have no single-key shortcut.
import { h, list, on, text } from '../core/h.js';
import { sortPending } from '../components/esc-time.js';
import { createSignal, tickSignal, updateSignal } from '../components/esc-card.js';
import { createHistRow, updateHistRow } from '../components/esc-row.js';

let barEl = null;
let barStore = null;
let barApi = null;
let barOff = null;
let barTimer = 0;

function paintBar() {
  if (!barEl || !barStore || !barApi) return;
  const now = Date.now() / 1000;
  const items = sortPending(barStore.get().escalations);
  list(barEl, items, (e) => e.id, (e) => {
    const n = createSignal(e, barApi);
    tickSignal(n, e, now);
    return n;
  }, (n, e) => {
    updateSignal(n, e);
    tickSignal(n, e, now);
  });
}

export function mountBar(el, store, api) {
  unmountBar();
  barEl = el;
  barStore = store;
  barApi = api;
  barOff = store.subscribe((s) => s.escalations, paintBar);
  paintBar();
  barTimer = setInterval(paintBar, 1000);
  return unmountBar;
}

export function unmountBar() {
  if (barOff) barOff();
  barOff = null;
  if (barTimer) clearInterval(barTimer);
  barTimer = 0;
  barEl = null;
  barStore = null;
  barApi = null;
}

let vRoot = null;
let vStore = null;
let vApi = null;
let vOff = null;
let vTimer = 0;
let vAlive = false;
let vHist = [];
let vHistSeq = 0;
let vPend = null;
let vPendN = null;
let vPendEmpty = null;
let vHistEl = null;
let vHistStat = null;

function paintPending() {
  if (!vAlive) return;
  const now = Date.now() / 1000;
  const items = sortPending(vStore.get().escalations);
  text(vPendN, items.length ? String(items.length) : '');
  vPendEmpty.hidden = items.length > 0;
  list(vPend, items, (e) => e.id, (e) => {
    const n = createSignal(e, vApi);
    tickSignal(n, e, now);
    return n;
  }, (n, e) => {
    updateSignal(n, e);
    tickSignal(n, e, now);
  });
}

function paintHist() {
  if (!vAlive) return;
  list(vHistEl, vHist, (e) => e.id, createHistRow, updateHistRow);
}

// The store keeps pending rows only, so history is fetched here.
// Guarded by a request sequence: rapid successive changes fire one GET
// each, but only the latest reply paints (stale replies are dropped).
async function loadHist() {
  if (!vAlive) return;
  const my = ++vHistSeq;
  text(vHistStat, 'loading…');
  const r = await vApi.get('/api/escalations?all=1');
  if (!vAlive || my !== vHistSeq) return;
  if (Array.isArray(r)) {
    vHist = r
      .filter((e) => e && e.state !== 'pending')
      .sort((a, b) => (Number(b.ts) || 0) - (Number(a.ts) || 0));
    text(vHistStat, vHist.length ? vHist.length + ' decided' : 'nothing decided yet');
  } else {
    text(vHistStat, 'history unavailable: ' + String((r && r.error) || 'unknown'));
  }
  paintHist();
}

export function mount(el, store, api) {
  unmount();
  vStore = store;
  vApi = api;
  vAlive = true;
  vHist = [];
  vPend = h('div', { class: 'esc-pending' });
  vPendN = h('span', { class: 'faint' }, '');
  vPendEmpty = h('p', { class: 'empty' }, 'No pending escalations.');
  vHistEl = h('div', { class: 'esc-history' });
  vHistStat = h('span', { class: 'faint' }, '');
  const refresh = h('button', { type: 'button' }, 'Refresh history');
  on(refresh, 'click', loadHist);
  vRoot = h('section', { class: 'esc-view', 'aria-label': 'Escalations' },
    h('h2', null, 'Escalations'),
    h('p', { class: 'muted' }, 'Pending items need a decision. History shows what was already decided.'),
    h('h3', null, 'Pending ', vPendN),
    vPendEmpty,
    vPend,
    h('h3', null, 'History'),
    h('div', { class: 'hctl' }, refresh, vHistStat),
    vHistEl);
  el.appendChild(vRoot);
  vOff = store.subscribe((s) => s.escalations, () => {
    paintPending();
    loadHist();
  });
  paintPending();
  loadHist();
  vTimer = setInterval(paintPending, 1000);
  return unmount;
}

export function unmount() {
  vAlive = false;
  vHistSeq++; // drop any in-flight history reply
  if (vOff) vOff();
  vOff = null;
  if (vTimer) clearInterval(vTimer);
  vTimer = 0;
  vRoot = null;
  vStore = null;
  vApi = null;
  vPend = null;
  vPendN = null;
  vPendEmpty = null;
  vHistEl = null;
  vHistStat = null;
  vHist = [];
}
