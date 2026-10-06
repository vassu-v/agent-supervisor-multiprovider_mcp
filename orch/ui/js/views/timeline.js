import { h, on, text, list } from '../core/h.js';
import { bind } from '../core/keys.js';
import { patch, SPANS } from '../core/router.js';
import { frac, lanesFor, MAX_MARKS, pc } from '../components/timeline-layout.js';
import { table, grid } from '../components/timeline-table.js';
import { normSpan, windowFor, timelineUrl, DEFAULT_SPAN } from '../components/timeline-span.js';
export { normSpan, spanSecs, windowFor, timelineUrl, DEFAULT_SPAN } from '../components/timeline-span.js';
export { frac, layoutLane, layoutPosts, bucket, lanesFor, MAX_MARKS } from '../components/timeline-layout.js';
function geo(node, m) {
  const s = node.style;
  s.setProperty('position', 'absolute');
  s.setProperty('top', m.kind === 'turn' ? '6px' : '4px');
  s.setProperty('left', pc(m.x0));
  if (m.x1 > m.x0 + 1e-9) { s.setProperty('width', pc(m.x1 - m.x0)); s.setProperty('height', '6px'); }
  else { s.removeProperty('width'); s.removeProperty('height'); }
}
const fix = (node, m) => {
  if (node.getAttribute('class') !== m.cls) node.setAttribute('class', m.cls);
  if (node.getAttribute('title') !== m.label) node.setAttribute('title', m.label);
  if (node.getAttribute('aria-label') !== m.label) node.setAttribute('aria-label', m.label);
  geo(node, m);
};
const dot = (m) => {
  const node = h('span', { class: m.cls, tabindex: '0', role: 'img', title: m.label, 'aria-label': m.label });
  geo(node, m);
  return node;
};
const edge = (cls, top) => {
  const r = h('i', { class: cls, 'aria-hidden': 'true' });
  const s = r.style;
  s.setProperty('position', 'absolute'); s.setProperty('left', '0'); s.setProperty('top', top);
  if (cls === 'tl-rail') { s.setProperty('width', '100%'); s.setProperty('height', '2px'); }
  else { s.setProperty('bottom', '0'); s.setProperty('width', '1px'); }
  return r;
};
export function mount(el, store, api) {
  const S = { data: null, win: null, error: '', loading: true, table: false, req: 0, dead: false };
  const params = () => ((store.get().ui && store.get().ui.route && store.get().ui.route.params) || {});
  const span = () => normSpan(params().span);
  const ws = () => (typeof params().ws === 'string' ? params().ws : '');
  const spans = h('div', { class: 'tl-spans', role: 'group', 'aria-label': 'Time span' });
  const btns = {};
  for (const s of SPANS) {
    const b = h('button', { type: 'button', 'data-span': s, 'aria-pressed': s === DEFAULT_SPAN ? 'true' : 'false' }, s);
    on(b, 'click', () => patch({ span: s }));
    btns[s] = b;
    spans.appendChild(b);
  }
  const tbtn = h('button', { type: 'button', 'aria-pressed': 'false' }, 'table');
  const msg = h('p', { class: 'muted', role: 'status' });
  const box = h('div', { role: 'list', 'aria-label': 'Agent swimlanes' });
  const t = table();
  el.appendChild(h('section', { class: 'tl-wrap', 'aria-label': 'Timeline' },
    h('h2', null, 'Timeline'), spans, tbtn, msg, box, t.wrap));
  const flip = () => {
    S.table = !S.table;
    tbtn.setAttribute('aria-pressed', S.table ? 'true' : 'false');
    if (t.wrap.hasAttribute('hidden') === S.table) t.wrap.toggleAttribute('hidden');
    if (S.table && S.win) grid(t.body, S.data, S.win.from, S.win.to);
  };
  on(tbtn, 'click', flip);
  const row = (l) => {
    const r = h('div', { class: 'tl-lane', role: 'listitem' },
      h('span', { class: 'tl-id' }, l.title),
      h('div', { class: 'tl-track' }, edge('tl-rail', '8px'),
        h('div', { class: 'tl-marks', role: 'list', 'aria-label': l.title }), edge('mk-now', '0')));
    sync(r, l);
    return r;
  };
  function sync(r, l) {
    if (S.win) {
      const nl = r.querySelector('.mk-now');
      if (nl) nl.style.setProperty('left', pc(frac(Date.now() / 1000, S.win.from, S.win.to)));
    }
    const m = r.querySelector('.tl-marks');
    if (m) list(m, l.marks, (x) => x.key, dot, fix);
    const lab = r.querySelector('.tl-id');
    if (lab) text(lab, l.title);
  }
  function render() {
    const d = S.data;
    S.win = d && d.from && d.to ? { from: d.from, to: d.to } : windowFor(span(), Date.now() / 1000);
    const lanes = d ? lanesFor(d, S.win.from, S.win.to) : [];
    let n = 0;
    let cut = Boolean(d && d.truncated);
    for (const l of lanes) { n += l.marks.length; if (l.truncated) cut = true; }
    if (S.error) text(msg, 'timeline failed: ' + S.error);
    else if (S.loading) text(msg, 'loading…');
    else if (!lanes.length) text(msg, 'no agents');
    else text(msg, lanes.length + ' lanes · ' + n + ' marks · ' + span() + (cut ? ' · bucketed' : ''));
    list(box, lanes, (l) => l.key, row, sync);
    if (S.table && S.win) grid(t.body, S.data, S.win.from, S.win.to);
  }
  async function load() {
    if (S.dead) return;
    const my = ++S.req;
    S.loading = true;
    render();
    const r = await api.get(timelineUrl(ws(), span(), Date.now() / 1000));
    if (S.dead || my !== S.req) return;
    S.loading = false;
    S.error = r && r.error ? String(r.error) : '';
    S.data = r && r.error ? null : r;
    render();
  }
  let seen = '';
  const unsub = store.subscribe((s) => (s.ui && s.ui.route) || {}, (route) => {
    const p = (route && route.params) || {};
    const sp = normSpan(p.span);
    for (const s of SPANS) btns[s].setAttribute('aria-pressed', s === sp ? 'true' : 'false');
    const k = (typeof p.ws === 'string' ? p.ws : '') + '|' + sp;
    if (k !== seen) { seen = k; load(); }
  });
  const unkey = bind('t', flip, 'timeline', 'Text table');
  return () => {
    S.dead = true;
    S.req++;
    unsub();
    unkey();
    el.replaceChildren();
  };
}
