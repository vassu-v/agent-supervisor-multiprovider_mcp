// Fleet view (default #/fleet): one yard per workspace, treegrid rows with a
// rail gutter, provider/model chips, spark bars, badges and a board rail.
// Reads the shared store, writes only through router params (drawer opens via
// ?agent=<id>). Announcements stay with core/sync.js: nothing is re-announced.

import { h, on, text, list } from '../core/h.js';
import { memo } from '../core/store.js';
import * as router from '../core/router.js';
import * as keys from '../core/keys.js';
import { roving } from '../core/a11y.js';
import { safeId } from '../core/fmt.js';
import { buildFleet } from '../components/fleet-model.js';
import { createYard, updateYard } from '../components/fleet-yard.js';

let current = null;

const ST_OPTS = [['', 'All statuses'], ['live', 'live'], ['busy', 'busy'], ['idle', 'idle'], ['dead', 'dead']];

function syncOptions(sel, opts, cur) {
  const sig = opts.map((o) => o[0]).join(' ');
  if (sel._sig !== sig) {
    sel._sig = sig;
    sel.replaceChildren(...opts.map((o) => h('option', { value: o[0] }, o[1])));
  }
  if (sel.value !== cur) sel.value = cur;
}

function legend() {
  const glyph = (id, cls) => h('svg', { class: 'i ' + cls, 'aria-hidden': 'true', focusable: 'false' }, h('use', { href: '#' + id }));
  return h('footer', { class: 'key', 'aria-label': 'Key' },
    h('span', null, h('b', null, 'Key')),
    h('span', null, glyph('s-busy', ''), 'busy (track occupied)'),
    h('span', null, glyph('s-idle', ''), 'idle (route set)'),
    h('span', null, glyph('s-dead', ''), 'finished (siding)'),
    h('span', null, glyph('s-wait', ''), 'held at a signal (needs you)'),
    h('span', null, glyph('g-agy', 'p-agy'), 'agy ', glyph('g-claude', 'p-claude'), 'claude ',
      glyph('g-opencode', 'p-opencode'), 'opencode ', glyph('g-codex', 'p-codex'), 'codex'));
}

export function mount(el, store, api) {
  void api;
  if (current) current.cleanup(); // defensive: never leave two refresh timers alive
  const openYards = new Set();
  const openDead = new Set();
  const hooks = { selected: null, compact: false, open: null, toggleDead: null, toggleYard: null, hl: null, board: null };

  const root = h('section', { class: 'fleet', 'aria-label': 'Fleet' });
  const tabs = h('nav', { class: 'tabs', role: 'tablist', 'aria-label': 'Workspaces' });
  const search = h('input', { type: 'search', 'data-search': '', placeholder: 'Filter agents\u2026', 'aria-label': 'Filter agents' });
  const stSel = h('select', { 'aria-label': 'Status filter' });
  const provSel = h('select', { 'aria-label': 'Provider filter' });
  const ownerSel = h('select', { 'aria-label': 'Owner filter' });
  const bar = h('div', { class: 'fleet-bar' }, tabs, search, stSel, provSel, ownerSel);
  const yardsEl = h('div', { class: 'yards' });
  const emptyEl = h('div', { hidden: true });
  const status = h('p', { class: 'fleet-status muted' });
  root.append(bar, yardsEl, emptyEl, status, legend());
  el.appendChild(root);

  const rov = roving(root, { role: 'row', onActivate: (row) => hooks.open(row.getAttribute('data-id')) });

  hooks.open = (id) => { if (safeId(id)) router.patch({ agent: id }); };
  hooks.board = (ws) => { if (safeId(ws)) router.go('board', { ws }); };
  hooks.toggleDead = (id) => { if (openDead.has(id)) openDead.delete(id); else openDead.add(id); render(); };
  hooks.toggleYard = (id) => { if (openYards.has(id)) openYards.delete(id); else openYards.add(id); render(); };
  hooks.hl = (id) => {
    for (const r of root.querySelectorAll('.row.hl')) r.classList.remove('hl');
    if (!safeId(id)) return;
    const row = root.querySelector(`[data-id="${id}"]`);
    if (row) row.classList.add('hl');
  };

  let debounce = null;
  on(search, 'input', () => {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => { debounce = null; router.patch({ q: search.value.trim() || null }); }, 150);
  });
  on(stSel, 'change', () => router.patch({ st: stSel.value || null }));
  on(provSel, 'change', () => router.patch({ prov: provSel.value || null }));
  on(ownerSel, 'change', () => router.patch({ owner: ownerSel.value || null }));

  function render() {
    const s = store.get();
    const params = (s.ui.route && s.ui.route.params) || {};
    const now = Date.now() / 1000;
    const snap = buildFleet({
      agents: s.agents, workspaces: s.workspaces, boards: s.boards,
      escalations: s.escalations, params, now, openYards, openDead,
    });
    hooks.selected = snap.selected;
    hooks.compact = s.ui.densityApplied === 'compact';
    const wsCur = params.ws || 'all';
    list(tabs, snap.tabs, (t) => t.id, (t) => {
      const b = on(h('button', { type: 'button', class: 'tab', role: 'tab', 'aria-selected': t.id === wsCur },
        t.name, h('span', { class: 'n' }, String(t.count))), 'click',
        () => router.patch({ ws: t.id === 'all' ? null : t.id }));
      b._t = { n: b.lastChild };
      return b;
    }, (b, t) => {
      b.setAttribute('aria-selected', t.id === wsCur ? 'true' : 'false');
      text(b._t.n, t.count);
    });
    const doc = root.ownerDocument;
    if (doc.activeElement !== search && search.value !== (params.q || '')) search.value = params.q || '';
    syncOptions(stSel, ST_OPTS, params.st || '');
    const provs = [...new Set([...s.agents.map((a) => a.provider).filter(Boolean),
      ...s.providers.map((p) => p.name).filter(Boolean)])].sort();
    syncOptions(provSel, [['', 'All providers'], ...provs.map((p) => [p, p])], params.prov || '');
    const owners = [...new Set(s.agents.map((a) => a.owner).filter(Boolean))].sort();
    syncOptions(ownerSel, [['', 'All owners'], ...owners.map((o) => [o, o])], params.owner || '');
    list(yardsEl, snap.yards, (y) => y.id,
      (y) => createYard(y, now, hooks),
      (sec, y) => updateYard(sec, y, now, hooks));
    if (!snap.yards.length) {
      emptyEl.removeAttribute('hidden');
      const filtered = !!((params.ws && params.ws !== 'all') || params.st || params.prov || params.owner || params.q);
      if (emptyEl._shown !== (filtered ? 'f' : 'n')) {
        emptyEl._shown = filtered ? 'f' : 'n';
        const clear = on(h('button', { type: 'button' }, 'Clear filters'), 'click',
          () => router.patch({ ws: null, st: null, prov: null, owner: null, q: null }));
        emptyEl.replaceChildren(filtered ? h('p', { class: 'empty' }, 'No agents match these filters. ', clear)
          : h('p', { class: 'empty' }, 'No agents yet. Start one: ', h('code', { class: 'mono' }, 'python agentctl.py spawn "<task>" --cwd <dir>')));
      }
    } else {
      emptyEl._shown = null;
      emptyEl.setAttribute('hidden', '');
      emptyEl.replaceChildren();
    }
    const conn = s.conn || {};
    text(status, conn.state === 'down' ? 'daemon unreachable, showing last data'
      : (!s.agents.length && conn.state === 'connecting' ? 'Loading\u2026' : ''));
    rov.refresh();
  }

  const pick = memo((s) => [s.agents, s.workspaces, s.boards, s.escalations, s.ui.route,
    s.ui.densityApplied, s.conn], (...a) => a);
  const off = store.subscribe(pick, render);
  const unbinds = [
    keys.bind('j', () => rov.move(1), 'fleet', 'Next agent'),
    keys.bind('k', () => rov.move(-1), 'fleet', 'Previous agent'),
  ];
  const timer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return; // badge refresh waits for a visible tab
    render();
  }, 30000);
  render();

  const cleanup = () => {
    off();
    unbinds.forEach((u) => u());
    clearInterval(timer);
    if (debounce) clearTimeout(debounce);
    rov.destroy();
    if (current && current.cleanup === cleanup) current = null;
  };
  current = { cleanup };
  return cleanup;
}

export function unmount() {
  if (current) current.cleanup();
}
