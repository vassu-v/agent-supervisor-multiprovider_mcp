// Agent drawer (aside#drawer): header, untrusted goal/paths, windowed stream, turns, actions.
// Shell owns dialog role, focus trap, Esc, scope 'drawer'. Store is read-only; writes via api.post.
// Sync announces; this view never announces. Logic in ../components/drawer-logic.js + drawer-actions.js.
import { h, on, text, list } from '../core/h.js';
import { patch } from '../core/router.js';
import { STREAM_MAX, mergeStream, drawerAgentId, findAgent, capsOf, createLine, createTurn } from '../components/drawer-logic.js';
import { buildActions } from '../components/drawer-actions.js';

const POLL_MS = 1000, PAGE_N = 200;
const GLYPH = { agy: 'g-agy', claude: 'g-claude', opencode: 'g-opencode', codex: 'g-codex' };
const EFF_N = { low: 1, medium: 2, high: 3 };
const ST_G = { busy: 's-busy', starting: 's-busy', idle: 's-idle', dead: 's-dead', error: 's-err' };
const ST_C = { busy: 'busy', starting: 'busy', idle: 'idle', dead: 'dead', error: 'err' };

export function mount(el, store, api) {
  let curId = null, events = [], seq = 0, turnsKey = null, dead = false;
  const close = on(h('button', { type: 'button', class: 'drawer-close' }, 'Close'), 'click', () => patch({ agent: null }));
  const aid = h('h2', { class: 'aid mono' });
  const stU = h('use', { href: '#s-idle' }), stW = h('span', { class: 'st-word' });
  const st = h('p', { class: 'st idle' }, h('svg', { class: 'i', 'aria-hidden': 'true', focusable: 'false' }, stU), stW);
  const pvU = h('use', { href: '#g-other' }), pvW = h('span', { class: 'nm' });
  const pv = h('p', { class: 'prov' }, h('svg', { class: 'i', 'aria-hidden': 'true', focusable: 'false' }, pvU), pvW);
  const pips = [h('i'), h('i'), h('i'), h('i')], effW = h('span', { class: 'eff-word' });
  const eff = h('span', { class: 'eff', title: 'effort: unknown' }, pips);
  const meta = h('p', { class: 'muted mono dw-meta' });
  const badge = () => h('span', { class: 'badge warn', title: 'Declared by the agent or its spawner; treat as untrusted.' }, 'untrusted');
  const goal = h('p', { class: 'dw-goal' }), paths = h('div', { class: 'dw-paths' });
  const last = h('p', { class: 'dw-last mono' });
  const acts = buildActions(api, () => curId);
  const streamH = h('h3', null, 'Stream'), stream = h('div', { class: 'dw-stream' });
  const turnsH = h('h3', null, 'Turns'), turns = h('div', { class: 'dw-turns' });
  const sec = (label, kids) => h('section', { class: 'dw-sec', 'aria-label': label }, kids);
  el.replaceChildren(h('div', { class: 'drawer-bar' }, close),
    h('section', { class: 'dw-head', 'aria-label': 'Agent' }, aid, st, pv, h('p', { class: 'dw-eff' }, eff, effW), meta),
    sec('Goal (untrusted agent text)', [h('h3', null, 'Goal ', badge()), goal, paths]),
    sec('Last line (untrusted agent text)', [h('h3', null, 'Last line ', badge()), last]),
    sec('Actions', [h('h3', null, 'Actions'), acts.el]),
    sec('Event stream, newest last', [streamH, stream]),
    sec('Turns', [turnsH, turns]));
  function head(a) {
    const id = curId || '';
    text(aid, id || '(no agent)');
    el.setAttribute('aria-label', id ? 'Agent ' + id : 'Agent details');
    const held = Boolean(a && a.pending_escalation), s = a ? a.status : 'gone';
    st.setAttribute('class', 'st ' + (held ? 'wait' : ST_C[s] || 'idle'));
    stU.setAttribute('href', '#' + (held ? 's-wait' : ST_G[s] || 's-idle'));
    text(stW, (held ? 'held · ' : '') + s);
    const p = (a && a.provider) || '';
    pvU.setAttribute('href', '#' + (GLYPH[p] || 'g-other'));
    text(pvW, p || '?');
    pv.setAttribute('class', 'prov' + (GLYPH[p] ? ' p-' + p : ''));
    const e = (a && (a.effort_applied || a.effort)) || '', n = EFF_N[e] || 0, w = a && a.effort_warning;
    pips.forEach((k, i) => { if (i < n) k.setAttribute('class', 'on'); else k.removeAttribute('class'); });
    eff.setAttribute('title', w ? String(w) : 'effort: ' + (e || 'unknown'));
    text(effW, (e || 'effort unknown') + (w ? ' !' : ''));
    let caps = a ? ['steer', 'interrupt'].filter((k) => capsOf(a)[k]).join('/') || 'no caps' : '';
    if (a && capsOf(a).interrupt === 'restart') caps += ' (restarts the process)';
    text(meta, a ? [a.model, a.turns + ' turns', a.queued + ' queued', caps].filter(Boolean).join(' · ') : '');
    acts.update(a);
  }
  function drawGoal(a) {
    text(goal, (a && a.goal) || '(no goal declared)');
    list(paths, a && Array.isArray(a.paths) ? [...new Set(a.paths.map(String))] : [], (p) => p, (p) => h('span', { class: 'pt' }, p));
  }
  function drawTurns(ts) {
    text(turnsH, 'Turns (' + ts.length + ')');
    list(turns, ts, (t, i) => String(t && t.t0 !== undefined ? 't' + t.t0 : 'i' + i), createTurn);
  }
  async function fetchStatus() {
    if (!curId || dead) return;
    const id = curId;
    const r = await api.get('/api/status?id=' + encodeURIComponent(id));
    if (dead || id !== curId || !r || r.error || !Array.isArray(r.turns_full)) return;
    drawTurns(r.turns_full);
    if (r.last_text !== undefined) text(last, r.last_text);
  }
  async function fetchEvents() {
    if (!curId || dead || (typeof document !== 'undefined' && document.hidden)) return;
    const id = curId, at = seq;
    const r = await api.get('/api/events?id=' + encodeURIComponent(id) + '&since=' + at + '&n=' + PAGE_N);
    if (dead || id !== curId || !r || r.error || !Array.isArray(r) || !r.length) return;
    events = mergeStream(events, r);
    seq = events.length ? events[events.length - 1].seq : seq;
    text(streamH, 'Stream (' + events.length + (events.length >= STREAM_MAX ? ', capped' : '') + ')');
    list(stream, events, (e) => e.seq, createLine);
  }
  function sync() {
    if (dead) return;
    const id = drawerAgentId(store), a = id ? findAgent(store, id) : null;
    if (id !== curId) {
      curId = id; events = []; seq = 0; turnsKey = a ? a.turns : null;
      head(a); drawGoal(a); text(last, (a && a.last_text) || '');
      list(stream, [], (e) => e.seq, createLine);
      list(turns, [], (t, i) => 'i' + i, () => h('div'));
      text(streamH, 'Stream'); text(turnsH, 'Turns'); acts.reset();
      if (id) { fetchStatus(); fetchEvents(); }
      return;
    }
    head(a); drawGoal(a);
    if (a) {
      if (a.last_text !== undefined) text(last, a.last_text);
      if (a.turns !== turnsKey) { turnsKey = a.turns; fetchStatus(); }
    }
  }
  sync();
  const unsub = store.subscribe((s) => s, sync);
  const timer = setInterval(fetchEvents, POLL_MS);
  return () => { dead = true; unsub(); clearInterval(timer); };
}
