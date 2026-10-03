// #/audit: windowed log table, newest first. Store read-only; all values via h()/text().
import { h, on, text, list } from '../core/h.js';
import * as keys from '../core/keys.js';
import { roving } from '../core/a11y.js';

export const WINDOW = 100;
export const STEP = 100;
const VAL_MAX = 160, DETAIL_MAX = 400;

const str = (v) => (v === null || v === undefined ? '' : String(v));

export function formatIso(ts) {
  if (ts === null || ts === undefined || ts === '') return '';
  const n = Number(ts);
  if (!Number.isFinite(n)) return '';
  try {
    return new Date(n > 1e12 ? n : n * 1000).toISOString();
  } catch {
    return '';
  }
}

export function formatTs(ts) {
  const iso = formatIso(ts);
  return iso ? iso.slice(0, 10) + ' ' + iso.slice(11, 19) : '';
}

export function auditActor(r) {
  return str(r && (r.by === undefined || r.by === null || r.by === '' ? r.agent : r.by));
}

export function auditTarget(r) {
  if (!r) return '';
  return str(r.agent ?? r.escalation ?? r.provider ?? r.ws ?? r.path ?? r.file ?? '');
}

function flat(v) {
  if (v === null || v === undefined) return '';
  if (typeof v !== 'object') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return '[object]';
  }
}

// Sorted k: v line over fields with no column of their own.
export function auditDetail(r) {
  if (!r || typeof r !== 'object') return '';
  const parts = [];
  for (const k of Object.keys(r).sort()) {
    if (k === 'ts' || k === 'kind' || k === 'by' || k === 'agent') continue;
    let v = flat(r[k]);
    if (!v) continue;
    if (v.length > VAL_MAX) v = v.slice(0, VAL_MAX) + '…';
    parts.push(k + ': ' + v);
  }
  let out = parts.join(' · ');
  if (out.length > DETAIL_MAX) out = out.slice(0, DETAIL_MAX) + '…';
  return out;
}

export function auditActors(rows) {
  const seen = new Set();
  for (const r of rows || []) {
    const a = auditActor(r);
    if (a) seen.add(a);
  }
  return [...seen].sort();
}

export function sortNewest(rows) {
  return (rows || [])
    .map((r, i) => [r, i])
    .sort((a, b) => ((Number(b[0] && b[0].ts) || 0) - (Number(a[0] && a[0].ts) || 0)) || a[1] - b[1])
    .map((p) => p[0]);
}

export function filterAudit(rows, q, actor) {
  const needle = str(q).trim().toLowerCase();
  return (rows || []).filter((r) => {
    if (actor && auditActor(r) !== actor) return false;
    if (!needle) return true;
    const hay = (str(r && r.kind) + ' ' + auditActor(r) + ' ' + auditTarget(r) + ' ' + auditDetail(r)).toLowerCase();
    return hay.indexOf(needle) >= 0;
  });
}

export function prepareAudit(rows, opts) {
  const o = opts || {};
  const limit = Math.max(1, Number(o.limit) || WINDOW);
  const filtered = filterAudit(sortNewest(rows), o.q || '', o.actor || '');
  return { rows: filtered.slice(0, limit), shown: Math.min(limit, filtered.length), total: filtered.length };
}

export function auditKeys(rows) {
  const n = new Map();
  return (rows || []).map((r) => {
    const base = formatIso(r.ts) + '|' + str(r.kind) + '|' + auditActor(r) + '|' + auditTarget(r) + '|' + auditDetail(r);
    const k = n.get(base) || 0;
    n.set(base, k + 1);
    return k ? base + '#' + k : base;
  });
}

function paintRow(tr, r) {
  const tds = tr.children;
  const iso = formatIso(r.ts);
  const tm = tds[0].firstChild;
  text(tm, formatTs(r.ts) || '—');
  if (iso) tm.setAttribute('datetime', iso);
  text(tds[1].firstChild, auditActor(r) || '—');
  text(tds[2].firstChild, str(r.kind) || '—');
  text(tds[3].firstChild, auditTarget(r) || '—');
  text(tds[4].firstChild, auditDetail(r));
}

function createRow(r) {
  const tr = h('tr', { role: 'row', tabindex: '-1' },
    h('td', { class: 'c-time' }, h('time', { class: 'mono' }, '')),
    h('td', { class: 'mono' }, ''),
    h('td', null, ''),
    h('td', { class: 'mono' }, ''),
    h('td', { class: 'detail' }, ''));
  paintRow(tr, r);
  return tr;
}

export function mount(el, store, api) {
  const st = { q: '', actor: '', limit: WINDOW };
  let rows = [];

  const count = h('p', { class: 'muted audit-count' }, '');
  const search = h('input', {
    type: 'search', 'data-search': '', placeholder: 'Filter text…',
    'aria-label': 'Filter text', autocomplete: 'off', spellcheck: 'false',
  });
  const actorSel = h('select', { 'aria-label': 'Filter actor' });
  const empty = h('p', { class: 'empty', hidden: true }, 'No matching entries.');
  const body = h('tbody');
  const COLS = [['Time', 'c-time'], ['Actor', ''], ['Action', ''], ['Target', 'c-target'], ['Detail', 'detail']];
  const more = h('button', { type: 'button', class: 'audit-more', hidden: true }, 'Show more');
  el.replaceChildren(
    h('section', { class: 'audit', 'aria-label': 'Audit' },
      h('h2', null, 'Audit'),
      count,
      h('div', { class: 'audit-filters' }, search, actorSel),
      h('div', { class: 'audit-wrap' },
        h('table', { class: 'audit-table', 'aria-label': 'Audit log, newest first' },
          h('thead', null, h('tr', null,
            COLS.map((c) => h('th', { scope: 'col', class: c[1] || null }, c[0])))),
          body)),
      empty,
      more));

  const rov = roving(body, { role: 'row' });

  function render() {
    const actors = auditActors(rows);
    if (st.actor && actors.indexOf(st.actor) < 0) st.actor = '';
    list(actorSel, [''].concat(actors),
      (a) => 'a:' + a,
      (a) => h('option', { value: a, selected: a === st.actor }, a || 'All actors'),
      (node, a) => {
        text(node, a || 'All actors');
        node.selected = a === st.actor;
      });
    const prep = prepareAudit(rows, st);
    const ks = auditKeys(prep.rows);
    list(body, prep.rows, (item, i) => ks[i], createRow, paintRow);
    rov.refresh();
    text(count, rows.length
      ? 'Showing ' + prep.shown + ' of ' + prep.total + ' (' + rows.length + ' loaded)'
      : 'No audit entries yet.');
    empty.hidden = prep.rows.length > 0;
    if (prep.total > prep.shown) {
      more.hidden = false;
      text(more, 'Show more (' + (prep.total - prep.shown) + ' remaining)');
    } else {
      more.hidden = true;
    }
  }

  on(search, 'input', () => {
    st.q = search.value;
    st.limit = WINDOW;
    render();
  });
  on(actorSel, 'change', () => {
    st.actor = actorSel.value || '';
    st.limit = WINDOW;
    render();
  });
  on(more, 'click', () => {
    st.limit += STEP;
    render();
    const first = body.querySelector('[tabindex="0"]');
    if (first) first.focus();
  });

  const off = store.subscribe((s) => s.audit, (v) => {
    rows = Array.isArray(v) ? v : [];
    render();
  });
  const unbind = [
    keys.bind('j', () => rov.move(1), 'audit', 'Next audit row'),
    keys.bind('k', () => rov.move(-1), 'audit', 'Previous audit row'),
  ];
  render();

  return function unmount() {
    off();
    for (const u of unbind) u();
    rov.destroy();
  };
}

export function unmount() {}
