// Boots the dashboard: network client, store, router, keys, header chrome, view registry, drawer, connect screen.
//
// Store shape (views read it; nothing but app.js and core/sync.js writes the shared parts):
//   agents       /api/list?all=1&tree=1 rows, in tree order (each has depth, parent, children)
//   workspaces   /api/workspaces rows {id, name, root, agents, busy, live, sessions}
//   boards       { [wsId]: /api/board reply {posts, last, open_questions} } (latest 100 posts)
//   providers    /api/providers?models=0 rows {name, enabled, state, caps, ...}
//   escalations  /api/escalations rows (pending only) {id, agent, why, tool, input, ts, state, workspace, deadline}
//   audit        /api/audit?n=200 rows, only kept fresh while the audit view is open
//   conn         { state: 'connecting'|'ok'|'down'|'auth'|'none', error: string, at: ms of the last good reply }
//   ui           { route: {view, params}, density: 'auto'|'comfortable'|'compact', densityApplied: 'comfortable'|'compact',
//                  theme: 'system'|'light'|'dark', selection: agent id or null (fleet cursor) }
//
// Views live in js/views/<name>.js and export mount(el, store, api) -> optional cleanup function, and/or unmount().
// views/escalations.js may also export mountBar(el, store, api) for the pinned bar in #esc-bar.
// Each mounted view gets its own keys scope named after the view; the drawer gets the scope "drawer".

import { h, on, text, list } from './core/h.js';
import { createStore, memo } from './core/store.js';
import * as router from './core/router.js';
import * as keys from './core/keys.js';
import { trapFocus } from './core/a11y.js';
import { totals } from './core/derive.js';
import { tokens as fmtTokens, safeId } from './core/fmt.js';
import { createSync } from './core/sync.js';

const AUTO_COMPACT_ABOVE = 24;
const PREF_DENSITY = 'switchyard.density';
const PREF_THEME = 'switchyard.theme';
const DENSITIES = ['auto', 'comfortable', 'compact'];
const THEMES = ['system', 'light', 'dark'];
const PROVIDER_GLYPH = { agy: 'g-agy', claude: 'g-claude', opencode: 'g-opencode', codex: 'g-codex' };
const VIEW_LABEL = { fleet: 'Fleet', timeline: 'Timeline', board: 'Board', models: 'Models', escalations: 'Escalations', audit: 'Audit', drawer: 'Agent drawer' };

const $ = (id) => document.getElementById(id);

function pref(key, allowed, fallback) {
  try {
    const v = localStorage.getItem(key);
    return allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}
function savePref(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage blocked: the choice lasts for this page only */ }
}

// ---------------------------------------------------------------- network (E2's modules, with a stand-in)

const TOKEN_KEY = 'switchyard.token';

// Minimal same-origin client used only when js/net/ cannot be loaded, so the shell still runs on its own.
function stubNet() {
  let tok = null;
  try { tok = sessionStorage.getItem(TOKEN_KEY); } catch { /* ignore */ }
  const authCbs = [];
  async function request(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (tok) headers.Authorization = 'Bearer ' + tok;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
      const data = await res.json().catch(() => null);
      if (res.status === 401) authCbs.forEach((cb) => cb());
      if (!res.ok || (data && data.error)) return { error: (data && data.error) || 'HTTP ' + res.status, status: res.status };
      return data;
    } catch (e) {
      return { error: String(e.message || e), status: 0 };
    }
  }
  const api = {
    get: (path) => request('GET', path),
    post: (path, body) => request('POST', path, { by: 'dashboard', ...(body || {}) }),
    token: () => tok,
    setToken: (t) => { tok = t || null; try { sessionStorage.setItem(TOKEN_KEY, tok || ''); } catch { /* ignore */ } },
    onAuthError: (cb) => { authCbs.push(cb); return () => authCbs.splice(authCbs.indexOf(cb), 1); },
  };
  let timer = null;
  const changes = {
    start(onDirty) { this.stop(); onDirty({ reset: true }); timer = setInterval(() => onDirty({ reset: true }), 3000); },
    stop() { if (timer) clearInterval(timer); timer = null; },
  };
  return { api, changes, stub: true };
}

async function loadNet() {
  try {
    const [api, changes] = await Promise.all([import('./net/api.js'), import('./net/changes.js')]);
    return { api, changes, stub: false };
  } catch (e) {
    console.warn('switchyard: js/net not available, using the built-in stand-in client', e);
    return stubNet();
  }
}

// ---------------------------------------------------------------- views

async function loadModule(name) {
  try {
    return await import(`./views/${name}.js`);
  } catch (e) {
    return { loadError: e };
  }
}

function placeholder(name, err) {
  const label = VIEW_LABEL[name] || name;
  return h('section', { class: 'placeholder', 'aria-label': label },
    h('h2', null, label + ' view not built yet'),
    h('p', { class: 'muted' }, `js/views/${name}.js is missing or failed to load. The rest of the dashboard still works.`),
    err ? h('pre', { class: 'mono' }, String((err && err.message) || err)) : null);
}

function mountInto(el, mod, name, store, api, fn = 'mount') {
  if (mod.loadError || typeof mod[fn] !== 'function') {
    if (fn === 'mount') el.replaceChildren(placeholder(name, mod.loadError));
    return null;
  }
  try {
    const r = mod[fn](el, store, api);
    if (typeof r === 'function') return r;
    const un = fn === 'mount' ? mod.unmount : mod.unmountBar;
    return typeof un === 'function' ? () => un.call(mod) : null;
  } catch (e) {
    console.error(`switchyard: ${name} failed to mount`, e);
    el.replaceChildren(placeholder(name, e));
    return null;
  }
}

function safeCall(fn) {
  if (!fn) return;
  try { fn(); } catch (e) { console.error('switchyard: cleanup failed', e); }
}

// ---------------------------------------------------------------- boot

async function boot() {
  const net = await loadNet();
  const api = net.api;
  api.token(); // resolves (and strips) a #t=<token> fragment before the router reads the hash

  const density = pref(PREF_DENSITY, DENSITIES, 'auto');
  const store = createStore({
    agents: [],
    workspaces: [],
    boards: {},
    providers: [],
    escalations: [],
    audit: [],
    conn: { state: 'connecting', error: '', at: 0 },
    ui: {
      route: router.parse(),
      density,
      densityApplied: density === 'compact' ? 'compact' : 'comfortable',
      theme: pref(PREF_THEME, THEMES, 'system'),
      selection: null,
    },
  });
  const setUi = (patch) => store.set((s) => ({ ui: { ...s.ui, ...patch } }));
  const sync = createSync(store, api);
  const main = $('main');

  // ---- view in <main>
  let current = null; // { name, cleanup, popScope }
  let mountSeq = 0;
  let pendingName = null; // view whose module is still loading
  async function showView(name) {
    if ((current && current.name === name) || pendingName === name) return;
    const seq = ++mountSeq;
    pendingName = name;
    if (current) { safeCall(current.cleanup); current.popScope(); current = null; }
    const el = h('div', { class: 'view', 'data-view': name });
    main.replaceChildren(el);
    const mod = await loadModule(name);
    if (seq !== mountSeq) return; // the user navigated again while this loaded
    pendingName = null;
    current = { name, popScope: keys.pushScope(name), cleanup: mountInto(el, mod, name, store, api) };
  }

  // ---- drawer (aside#drawer, owned by views/drawer.js)
  const drawer = $('drawer');
  let drawerState = null; // { cleanup, popScope, release }
  let drawerSeq = 0;
  async function openDrawer() {
    if (drawerState) return; // already open: the drawer view follows ui.route.params.agent itself
    const seq = ++drawerSeq;
    drawerState = { cleanup: null, popScope: keys.pushScope('drawer'), release: null };
    drawer.hidden = false;
    document.body.classList.add('drawer-open');
    const mod = await loadModule('drawer');
    if (seq !== drawerSeq || !drawerState) return;
    if (mod.loadError || typeof mod.mount !== 'function') {
      const close = on(h('button', { type: 'button', class: 'drawer-close' }, 'Close'), 'click', () => router.patch({ agent: null }));
      drawer.replaceChildren(h('div', { class: 'drawer-bar' }, close), placeholder('drawer', mod.loadError));
    } else {
      drawerState.cleanup = mountInto(drawer, mod, 'drawer', store, api);
    }
    drawerState.release = trapFocus(drawer);
  }
  function closeDrawer() {
    if (!drawerState) return;
    drawerSeq++;
    const st = drawerState;
    drawerState = null;
    safeCall(st.cleanup);
    st.popScope();
    drawer.replaceChildren();
    drawer.hidden = true;
    document.body.classList.remove('drawer-open');
    if (st.release) st.release(); // returns focus to the row that opened it
  }

  // ---- routing
  function applyRoute(route) {
    if (document.body.classList.contains('connecting')) return; // no views until there is a token
    setUi({ route });
    document.body.dataset.view = route.view;   // css hides the pinned escalation bar on its own view
    showView(route.view);
    if (route.params.agent) openDrawer(); else closeDrawer();
    if (route.view === 'audit') sync.refresh('audit');
  }
  router.onChange(applyRoute);

  // ---- escalation bar (views/escalations.js mountBar)
  loadModule('escalations').then((mod) => { mountInto($('esc-bar'), mod, 'escalations', store, api, 'mountBar'); });

  // ---- header: counts and connection
  const meta = $('meta');
  const fleetTotals = memo((s) => [s.agents], totals);
  // subscribe() compares by identity, so the selector is memoised: it returns the same array until an input changes
  const headerInputs = memo((s) => [fleetTotals(s), s.escalations.length, s.workspaces.length, s.conn], (...a) => a);
  store.subscribe(headerInputs, ([t, waiting, nws, conn]) => {
    document.body.dataset.net = conn.state;
    if (conn.state === 'auth') { text(meta, 'token rejected'); return; }
    if (conn.state === 'none') { text(meta, 'not connected'); return; }
    if (conn.state === 'connecting') { text(meta, 'connecting…'); return; }
    const parts = conn.state === 'down' ? ['daemon unreachable, showing last data'] : [];
    parts.push(`${t.agents} agents`, `${t.busy} busy`, `${waiting} waiting on you`, `${nws} workspaces`, `${fmtTokens(t.tokens)} tokens`);
    text(meta, parts.join(' · '));
  });

  // ---- header: provider chips (toggle = the old dashboard's provider on/off)
  const provs = $('provs');
  function provChip(p) {
    const tog = h('button', { type: 'button', class: 'tog', role: 'switch' });
    on(tog, 'click', async () => {
      const cur = store.get().providers.find((x) => x.name === p.name);
      tog.disabled = true;
      await api.post('/api/provider', { name: p.name, enabled: !(cur && cur.enabled) });
      tog.disabled = false;
      sync.refresh('providers');
    });
    const el = h('div', { class: 'prov', 'data-name': p.name },
      h('svg', { class: 'i p-' + p.name, 'aria-hidden': 'true', focusable: 'false' }, h('use', { href: '#' + (PROVIDER_GLYPH[p.name] || 'g-other') })),
      h('span', { class: 'nm' }, p.name),
      h('span', { class: 'faint st-word' }),
      tog);
    updateProvChip(el, p);
    return el;
  }
  function updateProvChip(el, p) {
    el.classList.toggle('off', !p.enabled);
    const word = !p.enabled ? 'off' : p.state && p.state !== 'ready' ? String(p.state).replace(/_/g, ' ') : '';
    text(el.querySelector('.st-word'), word);
    const tog = el.querySelector('.tog');
    tog.setAttribute('aria-checked', p.enabled ? 'true' : 'false');
    tog.setAttribute('aria-label', `${p.name} ${p.enabled ? 'enabled' : 'disabled'}`);
  }
  store.subscribe((s) => s.providers, (rows) => {
    list(provs, rows.filter((p) => safeId(p.name)), (p) => p.name, provChip, updateProvChip);
  });

  // ---- nav: current view, board target, escalation count
  const navLinks = [...$('views').querySelectorAll('a[data-view]')];
  const boardWs = (s) => {
    const p = s.ui.route.params;
    if (p.ws && p.ws !== 'all') return p.ws;
    return s.workspaces.length ? s.workspaces[0].id : null;
  };
  store.subscribe((s) => s.ui.route, (route) => {
    for (const a of navLinks) {
      if (a.dataset.view === route.view) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
  });
  store.subscribe(boardWs, (ws) => {
    const a = navLinks.find((x) => x.dataset.view === 'board');
    a.setAttribute('href', ws ? router.format('board', { ws }) : router.format('fleet'));
  });
  store.subscribe((s) => s.escalations.length, (n) => text($('esc-count'), n ? String(n) : ''));

  // ---- density and theme
  const densityBtn = $('density-btn');
  store.subscribe((s) => {
    const live = fleetTotals(s).live;
    const applied = s.ui.density === 'auto' ? (live > AUTO_COMPACT_ABOVE ? 'compact' : 'comfortable') : s.ui.density;
    return s.ui.density + ':' + applied;
  }, (key) => {
    const [chosen, applied] = key.split(':');
    document.documentElement.dataset.density = applied;
    if (store.get().ui.densityApplied !== applied) setUi({ densityApplied: applied });
    text(densityBtn, chosen === 'auto' ? `Density: auto (${applied})` : `Density: ${chosen}`);
  });
  function cycleDensity() {
    const next = DENSITIES[(DENSITIES.indexOf(store.get().ui.density) + 1) % DENSITIES.length];
    savePref(PREF_DENSITY, next);
    setUi({ density: next });
  }
  on(densityBtn, 'click', cycleDensity);

  const themeBtn = $('theme-btn');
  store.subscribe((s) => s.ui.theme, (theme) => {
    if (theme === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    text(themeBtn, 'Theme: ' + theme);
  });
  on(themeBtn, 'click', () => {
    const next = THEMES[(THEMES.indexOf(store.get().ui.theme) + 1) % THEMES.length];
    savePref(PREF_THEME, next);
    setUi({ theme: next });
  });

  // ---- help dialog
  const help = $('help');
  let helpState = null;
  function openHelp() {
    if (helpState) return;
    const close = on(h('button', { type: 'button' }, 'Close'), 'click', closeHelp);
    help.replaceChildren(
      h('h2', { id: 'help-title' }, 'Keyboard shortcuts'),
      h('dl', { class: 'keys' }, keys.help().map((k) => [h('dt', null, h('kbd', null, k.seq)), h('dd', null, k.label)])),
      h('p', { class: 'faint' }, 'Allow, deny and stop have no single-key shortcut.'),
      close);
    help.hidden = false;
    helpState = { popScope: keys.pushScope('help'), release: trapFocus(help) };
  }
  function closeHelp() {
    if (!helpState) return;
    const st = helpState;
    helpState = null;
    help.hidden = true;
    help.replaceChildren();
    st.popScope();
    st.release();
  }
  on($('help-btn'), 'click', openHelp);
  on($('skip'), 'click', () => main.focus());

  // ---- keys
  const goBoard = () => {
    const ws = boardWs(store.get());
    router.go(ws ? 'board' : 'fleet', ws ? { ws } : {});
  };
  keys.bind('g f', () => router.go('fleet'), 'global', 'Go to fleet');
  keys.bind('g t', () => router.go('timeline'), 'global', 'Go to timeline');
  keys.bind('g b', goBoard, 'global', 'Go to board');
  keys.bind('g m', () => router.go('models'), 'global', 'Go to models');
  keys.bind('g e', () => router.go('escalations'), 'global', 'Go to escalations');
  keys.bind('g a', () => router.go('audit'), 'global', 'Go to audit');
  keys.bind('/', () => {
    const search = main.querySelector('[data-search]');
    if (search) search.focus(); else router.go('fleet');
  }, 'global', 'Search');
  keys.bind('e', () => {
    if (store.get().ui.route.view === 'escalations') { const b = main.querySelector('.view button'); if (b) b.focus(); return; }
    const btn = $('esc-bar').querySelector('button');
    if (btn) btn.focus(); else router.go('escalations');
  }, 'global', 'First escalation');
  keys.bind('.', cycleDensity, 'global', 'Density');
  keys.bind('?', openHelp, 'global', 'This help');
  keys.bind('Escape', () => router.patch({ agent: null }), 'drawer', 'Close the drawer');
  keys.bind('Escape', closeHelp, 'help', 'Close help');
  keys.start(document);

  // ---- connect screen and data
  let running = false;
  function startData() {
    if (running) return;
    running = true;
    store.set({ conn: { state: 'connecting', error: '', at: 0 } });
    // the feed's first reply is always a reset (it has no revision yet); we just loaded everything, so skip that one
    let first = true;
    sync.load().then(() => net.changes.start((d) => {
      const skip = first && d && d.reset;
      first = false;
      if (!skip) sync.onDirty(d);
    }, (up, r) => {
      const c = store.get().conn;
      if (!up) store.set({ conn: { state: 'down', error: (r && r.error) || 'no response', at: c.at } });
      else first = false;    // the reset this reply carries reloads everything; markConn then flips conn to 'ok'
    }));
  }
  function stopData() {
    running = false;
    net.changes.stop();
  }
  function showConnect(reason) {
    stopData();
    closeDrawer();
    if (current) { safeCall(current.cleanup); current.popScope(); current = null; }
    mountSeq++;
    pendingName = null;
    store.set({ conn: { state: reason ? 'auth' : 'none', error: reason || '', at: 0 } });
    document.body.classList.add('connecting');

    const input = h('input', { type: 'password', id: 'token-input', name: 'token', autocomplete: 'off', spellcheck: 'false', required: true });
    const msg = h('p', { class: 'err', role: 'alert' }, reason || '');
    const form = h('form', { class: 'connect-form' },
      h('label', { for: 'token-input' }, 'Admin token'),
      h('div', { class: 'row' }, input, h('button', { type: 'submit' }, 'Connect')),
      msg);
    on(form, 'submit', async (ev) => {
      ev.preventDefault();
      const t = input.value.trim();
      if (!t) return;
      api.setToken(t);
      const r = await api.get('/api/workspaces');
      if (r && r.error) {
        text(msg, r.status === 401 ? 'That token was rejected.' : 'Could not reach the daemon: ' + r.error);
        return;
      }
      leaveConnect();
    });
    // "agentctl.py dashboard" opened in an already-open tab only changes the fragment: reload so api.js picks it up
    window.addEventListener('hashchange', reloadOnToken);
    main.replaceChildren(h('section', { class: 'connect', 'aria-labelledby': 'connect-title' },
      h('h2', { id: 'connect-title' }, 'Connect to Switchyard'),
      h('p', { class: 'muted' }, 'Open the dashboard with "agentctl.py dashboard", or paste the admin token from orch/token.txt.'),
      h('p', { class: 'faint' }, 'The token stays in this tab (session storage) and is only sent to this daemon.'),
      form));
    input.focus();
  }
  function reloadOnToken() {
    if (/^#(?:.*&)?t=/.test(location.hash)) location.reload();
  }
  function leaveConnect() {
    window.removeEventListener('hashchange', reloadOnToken);
    document.body.classList.remove('connecting');
    main.replaceChildren();
    const route = router.parse();
    applyRoute(route);
    startData();
  }

  api.onAuthError(() => { if (running) showConnect('The daemon rejected the token. Paste a current one.'); });

  if (!api.token()) showConnect('');
  else leaveConnect();
}

boot();
