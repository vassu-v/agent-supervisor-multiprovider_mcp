// #/models: provider switches, discovered models + efforts, per-model
// rollups, caps matrix. Store is read-only; fetched while open.

import { h, on, text, list } from '../core/h.js';
import { bind } from '../core/keys.js';
import { tokens as fmtTokens, safeId } from '../core/fmt.js';
import { MODELS_URL, modelRollup, capsTable, shouldRefresh, toggleBody } from '../components/models-rollup.js';

const GLYPH = {agy:'g-agy',claude:'g-claude',opencode:'g-opencode',codex:'g-codex'};

export function mount(el, store, api) {
  const S = { provs: [], agents: [], models: null, err: '', last: 0, busy: new Set(), req: 0, dead: false };

  const statusEl = h('p', { class: 'muted m-status', role: 'status' });
  const refreshBtn = h('button', { type: 'button', class: 'm-refresh', title: 'At most once per 30 s' }, 'Refresh models');
  const provEl = h('div', { class: 'provs m-provs', role: 'group', 'aria-label': 'Providers' });
  const secsEl = h('div', { class: 'm-secs' });
  const capsEl = h('div', { class: 'm-caps' });
  const root = h('section', { class: 'view-m-models' },
    h('h2', null, 'Models and providers'),
    h('div', { class: 'm-top' }, refreshBtn, statusEl),
    h('h3', null, 'Providers'), provEl,
    h('h3', null, 'Models'), secsEl,
    h('h3', null, 'Capabilities'), capsEl);
  el.appendChild(root);

  // Chips follow the header pattern in app.js: role=switch + struck name.
  function paintChip(node, p) {
    node.classList.toggle('off', !p.enabled);
    text(node.querySelector('.st-word'),
      !p.enabled ? 'off' : (p.state && p.state !== 'ready' ? String(p.state).replace(/_/g, ' ') : ''));
    const tog = node.querySelector('.tog');
    tog.setAttribute('aria-checked', p.enabled ? 'true' : 'false');
    tog.setAttribute('aria-label', p.name + ' ' + (p.enabled ? 'enabled' : 'disabled'));
    tog.disabled = S.busy.has(p.name);
  }
  function createChip(p) {
    const tog = h('button', { type: 'button', class: 'tog', role: 'switch' });
    on(tog, 'click', () => toggle(p.name));
    const node = h('div', { class: 'prov', 'data-name': p.name },
      h('svg', { class: 'i p-' + p.name, 'aria-hidden': 'true', focusable: 'false' },
        h('use', { href: '#' + (GLYPH[p.name] || 'g-other') })),
      h('span', { class: 'nm' }, p.name), h('span', { class: 'faint st-word' }), tog);
    paintChip(node, p);
    return node;
  }
  const paintProvs = (rows) =>
    list(provEl, rows.filter((p) => safeId(p.name)), (p) => p.name, createChip, paintChip);

  async function toggle(name) {
    if (S.busy.has(name) || S.dead) return;
    const cur = S.provs.find((x) => x.name === name);
    S.busy.add(name);
    paintProvs(S.provs);
    let r;
    try {
      r = await api.post('/api/provider', toggleBody(name, !!(cur && cur.enabled)));
    } catch (e) {
      r = { error: String((e && e.message) || e) };
    }
    S.busy.delete(name);
    if (S.dead) return;
    if (r && r.error) {
      S.err = 'toggle failed: ' + r.error;
      paintStatus();
    }
    paintProvs(S.provs);
  }

  function createRow(it) {
    const row = h('li', { class: 'm-model', 'data-id': it.id },
      h('span', { class: 'mono m-id' }, it.id),
      h('span', { class: 'm-eff' },
        h('span', { class: 'eff', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i')),
        h('span', { class: 'm-effw' })),
      h('span', { class: 'm-use' }));
    paintRow(row, it);
    return row;
  }
  function paintRow(row, it) {
    text(row.querySelector('.m-id'), it.id);
    const w = it.efforts.length ? it.efforts.join(' · ') : 'no effort levels';
    text(row.querySelector('.m-effw'), w);
    const eff = row.querySelector('.m-eff');
    eff.setAttribute('title', w);
    eff.querySelectorAll('i').forEach((pip, i) => pip.classList.toggle('on', i < Math.min(4, it.efforts.length)));
    text(row.querySelector('.m-use'),
      it.use.agents + (it.use.agents === 1 ? ' agent · ' : ' agents · ') + fmtTokens(it.use.tokens));
  }
  function createSection(p) {
    const sec = h('section', { class: 'm-prov' },
      h('h3', { class: 'm-pname' }), h('p', { class: 'faint m-note' }), h('ul', { class: 'm-list' }));
    paintSection(sec, p);
    return sec;
  }
  function paintSection(sec, p) {
    const rec = S.models && S.models[p.name];
    const models = (rec && rec.models) || [];
    text(sec.querySelector('.m-pname'), p.name + ' · ' + models.length + (models.length === 1 ? ' model' : ' models'));
    const roll = modelRollup(S.agents.filter((a) => a && a.provider === p.name));
    const byId = new Map(roll.map((r) => [r.model, r]));
    const items = models.map((m) => {
      const id = String(m.id);
      return {
        id, efforts: Array.isArray(m.efforts) ? m.efforts.map(String) : [],
        use: byId.get(id) || { agents: 0, tokens: 0 },
      };
    });
    const placed = roll.reduce((s, r) => s + r.agents, 0);
    text(sec.querySelector('.m-note'), !S.models ? 'loading…'
      : (rec && rec.error ? 'failed: ' + rec.error : placed + (placed === 1 ? ' agent' : ' agents') + ' on this provider'));
    sec.classList.toggle('is-off', !p.enabled);
    const ul = sec.querySelector('ul');
    list(ul, items, (it) => it.id, createRow, paintRow);
  }
  const paintSections = () =>
    list(secsEl, S.provs.filter((p) => safeId(p.name)), (p) => p.name, createSection, paintSection);

  let capsKey = null;
  let capsBody = null;
  function paintCaps(rows) {
    const t = capsTable(rows.filter((p) => safeId(p.name)));
    const key = t.cols.join(' ');
    if (!capsBody || key !== capsKey) {
      capsKey = key;
      const table = h('table', { class: 'm-caps-t' },
        h('thead', null, h('tr', null,
          [h('th', { scope: 'col' }, 'Capability')].concat(t.cols.map((c) => h('th', { scope: 'col' }, c))))),
        h('tbody'));
      capsBody = table.querySelector('tbody');
      capsEl.replaceChildren(table);
    }
    list(capsBody, t.rows, (r) => r.cap,
      (r) => h('tr', null,
        [h('th', { scope: 'row' }, r.cap)].concat(r.cells.map((c) => h('td', null, c.display)))),
      (node, r) => {
        text(node.querySelector('th'), r.cap);
        const tds = node.querySelectorAll('td');
        r.cells.forEach((c, i) => text(tds[i], c.display));
      });
  }

  function paintStatus() {
    if (S.err) text(statusEl, S.err);
    else if (!S.models) text(statusEl, 'loading…');
    else {
      let n = 0;
      for (const k of Object.keys(S.models)) {
        const m = S.models[k];
        if (m && Array.isArray(m.models)) n += m.models.length;
      }
      const placed = modelRollup(S.agents).reduce((s, r) => s + r.agents, 0);
      text(statusEl, n + (n === 1 ? ' model · ' : ' models · ') + placed + (placed === 1 ? ' agent' : ' agents') + ' placed');
    }
  }
  function paintRefresh() {
    refreshBtn.disabled = !shouldRefresh(S.last, Date.now());
  }

  async function load() {
    if (S.dead || !shouldRefresh(S.last, Date.now())) return false;
    const my = ++S.req;
    S.last = Date.now();
    paintRefresh();
    let r;
    try {
      r = await api.get(MODELS_URL);
    } catch (e) {
      r = { error: String((e && e.message) || e) };
    }
    if (S.dead || my !== S.req) return false;
    if (r && !r.error) {
      S.models = r;
      S.err = '';
    } else {
      S.err = 'no models: ' + ((r && r.error) || 'request failed');
    }
    paintSections();
    paintStatus();
    paintRefresh();
    return true;
  }

  on(refreshBtn, 'click', load);

  S.provs = store.get().providers || [];
  S.agents = store.get().agents || [];
  paintProvs(S.provs);
  paintSections();
  paintCaps(S.provs);
  paintStatus();
  paintRefresh();

  const offP = store.subscribe((s) => s.providers, (rows) => {
    S.provs = rows || [];
    paintProvs(S.provs);
    paintCaps(S.provs);
    paintSections();
  });
  const offA = store.subscribe((s) => s.agents, (rows) => {
    S.agents = rows || [];
    paintSections();
    paintStatus();
  });
  const unbindKey = bind('r', () => { load(); }, 'models', 'Refresh models');
  load();

  function unmount() {
    S.dead = true;
    S.req += 1;
    offP();
    offA();
    unbindKey();
    root.remove();
  }
  return unmount;
}

export function unmount() {}
