// Timeline view: span maths, layout, bucketing, and mount/render behaviour.
// Run: node --test tests/ui/test_timeline.mjs
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { statSync } from 'node:fs';
import { install, Event } from './shim.mjs';
import * as keys from '../../orch/ui/js/core/keys.js';
import {
  normSpan, spanSecs, windowFor, frac, timelineUrl,
  layoutLane, layoutPosts, bucket, lanesFor, mount, MAX_MARKS,
} from '../../orch/ui/js/views/timeline.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let cleanups = [];

beforeEach(() => {
  install();
  keys.reset();
  globalThis.location = { hash: '#/timeline' };
});
afterEach(() => {
  for (const fn of cleanups.splice(0)) {
    try { fn(); } catch { /* ignore */ }
  }
  delete globalThis.location;
});

function fakeStore(params = {}) {
  const subs = [];
  let state = { ui: { route: { view: 'timeline', params } } };
  return {
    get: () => state,
    set: (patch) => {
      state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
      for (const s of subs) s.cb(s.sel(state), state);
    },
    subscribe: (sel, cb) => {
      const s = { sel, cb };
      subs.push(s);
      cb(sel(state), state);
      return () => { const i = subs.indexOf(s); if (i >= 0) subs.splice(i, 1); };
    },
  };
}

function fakeApi(data) {
  const urls = [];
  return {
    urls,
    get: async (u) => { urls.push(u); return data; },
    post: async () => ({}),
  };
}

function sample() {
  const now = Date.now() / 1000;
  const from = now - 900;
  return {
    from, to: now, truncated: false,
    agents: [
      { id: 'a1', provider: 'claude', model: 'm', created: from - 10, ended: null },
      { id: 'a2', provider: 'agy', model: 'm', created: from - 5, ended: null },
    ],
    items: [
      { a: 'a1', k: 'turn', t0: from + 10, t1: from + 100, ok: true, int: false },
      { a: 'a1', k: 'turn', t0: from + 200, t1: null, ok: null, int: false },
      { a: 'a1', k: 'tool', t0: from + 20, t1: from + 30, tool: 'Bash', ok: true },
      { a: 'a1', k: 'tool', t0: from + 40, t1: from + 41, tool: 'Read', ok: false },
      { a: 'a1', k: 'guard', t: from + 50, decision: 'block' },
      { a: 'a1', k: 'esc', t: from + 60, id: 'e1', state: 'pending' },
      { a: 'a1', k: 'esc', t: from + 70, id: 'e2', state: 'allowed' },
      { a: 'a2', k: 'turn', t0: from + 5, t1: from + 15, ok: false, int: true },
      { a: null, k: 'post', t: from + 80, id: 42, kind: 'question', ws: 'w1' },
    ],
  };
}

async function mounted(params, data) {
  const store = fakeStore(params);
  const api = fakeApi(data === undefined ? sample() : data);
  const el = document.createElement('div');
  document.body.appendChild(el);
  const un = mount(el, store, api);
  cleanups.push(un);
  await sleep(10);
  return { el, store, api, un };
}

// ------------------------------------------------------------------ spans

describe('spans', () => {
  test('normSpan keeps 15m/1h/6h, defaults anything else', () => {
    assert.equal(normSpan('15m'), '15m');
    assert.equal(normSpan('1h'), '1h');
    assert.equal(normSpan('6h'), '6h');
    assert.equal(normSpan('2d'), '15m');
    assert.equal(normSpan(undefined), '15m');
  });
  test('spanSecs and windowFor widths', () => {
    assert.equal(spanSecs('15m'), 900);
    assert.equal(spanSecs('1h'), 3600);
    assert.equal(spanSecs('6h'), 21600);
    const w = windowFor('1h', 5000);
    assert.deepEqual(w, { from: 1400, to: 5000 });
  });
  test('frac clamps out-of-window times', () => {
    assert.equal(frac(0, 100, 200), 0);
    assert.equal(frac(300, 100, 200), 1);
    assert.equal(frac(150, 100, 200), 0.5);
    assert.equal(frac(150, 200, 200), 0, 'degenerate window');
  });
  test('timelineUrl: ws vs all, span change moves since', () => {
    const now = 1_700_000_000;
    const a = timelineUrl('w1', '15m', now);
    const b = timelineUrl('w1', '1h', now);
    const all = timelineUrl('', '15m', now);
    assert.match(a, /ws=w1/);
    assert.match(all, /all=1/);
    assert.match(timelineUrl('all', '15m', now), /all=1/);
    assert.doesNotMatch(timelineUrl('all', '15m', now), /ws=/);
    assert.match(a, /max=2000/);
    const since = (u) => Number(/since=(\d+)/.exec(u)[1]);
    assert.equal(since(b) - since(a), 900 - 3600, '1h looks further back');
    assert.match(a, new RegExp('until=' + now));
  });
});

// ------------------------------------------------------------------ layout

describe('layout', () => {
  test('turns become blocks, tools/guard/esc become ticks', () => {
    const d = sample();
    const marks = layoutLane('a1', d.items, d.from, d.to);
    assert.equal(marks.length, 7);
    const kinds = marks.map((m) => m.kind);
    assert.deepEqual(kinds, ['turn', 'turn', 'tool', 'tool', 'guard', 'esc', 'esc']);
    const closed = marks[0];
    assert.ok(Math.abs(closed.x0 - 10 / 900) < 1e-9);
    assert.ok(Math.abs(closed.x1 - 100 / 900) < 1e-9);
    assert.match(closed.cls, /mk-busy/);
    assert.equal(marks[1].x1, 1, 'open turn runs to the window edge');
    assert.equal(marks[2].x0, marks[2].x1, 'tool tick has no width');
    assert.match(marks[3].cls, /mk-err/, 'failed tool reads as an error');
    assert.match(marks[4].cls, /mk-guard/);
    assert.match(marks[5].cls, /mk-err/, 'pending escalation reads as an error');
    assert.ok(!/mk-err/.test(marks[6].cls), 'resolved escalation is muted');
    assert.match(marks[0].label, /turn/);
  });
  test('failed interrupted turn reads as an error', () => {
    const d = sample();
    const marks = layoutLane('a2', d.items, d.from, d.to);
    assert.equal(marks.length, 1);
    assert.match(marks[0].cls, /mk-err/);
    assert.match(marks[0].label, /failed/);
    assert.match(marks[0].label, /interrupted/);
  });
  test('out-of-window items clamp, keys stay unique', () => {
    const d = sample();
    const items = [
      ...d.items,
      { a: 'a1', k: 'turn', t0: d.from - 5000, t1: d.to + 5000, ok: true, int: false },
      { a: 'a1', k: 'tool', t0: d.from - 1, t1: null, tool: 'X', ok: true },
    ];
    const marks = layoutLane('a1', items, d.from, d.to);
    assert.equal(marks[7].x0, 0);
    assert.equal(marks[7].x1, 1);
    assert.equal(marks[8].x0, 0);
    assert.equal(new Set(marks.map((m) => m.key)).size, marks.length);
    assert.deepEqual(layoutLane('nobody', d.items, d.from, d.to), []);
  });
  test('posts land on their own lane data', () => {
    const d = sample();
    const posts = layoutPosts(d.items, d.from, d.to);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].label, 'question #42');
    assert.match(posts[0].cls, /mk-post/);
  });
});

// ------------------------------------------------------------------ bucketing

describe('bucketing', () => {
  test('under the cap nothing changes', () => {
    const marks = [{ key: 'a', x0: 0.1, x1: 0.2 }, { key: 'b', x0: 0.3, x1: 0.3 }];
    const r = bucket(marks, 2000);
    assert.equal(r.marks, marks);
    assert.equal(r.truncated, false);
  });
  test('over the cap merges into time buckets', () => {
    const marks = Array.from({ length: 2500 }, (_, i) => ({ key: 'm' + i, x0: i / 2500, x1: i / 2500 }));
    const r = bucket(marks, 2000);
    assert.ok(r.marks.length <= 2000);
    assert.equal(r.truncated, true);
    assert.equal(r.marks.reduce((s, m) => s + m.count, 0), 2500, 'counts are preserved');
    assert.ok(r.marks.every((m) => /events$/.test(m.label)));
    const xs = r.marks.map((m) => m.x0);
    assert.deepEqual(xs, [...xs].sort((a, b) => a - b), 'time order kept');
  });
  test('lanesFor stays under the cap across lanes', () => {
    const d = sample();
    for (let i = 0; i < 2500; i++) {
      d.items.push({ a: 'a1', k: 'tool', t0: d.from + (i % 899), t1: null, tool: 'Bash', ok: true });
    }
    const lanes = lanesFor(d, d.from, d.to);
    assert.equal(lanes.length, 3, 'two agents plus the board lane');
    const total = lanes.reduce((s, l) => s + l.marks.length, 0);
    assert.ok(total <= MAX_MARKS, 'total ' + total);
    assert.ok(lanes[0].truncated, 'the busy lane reports bucketing');
  });
});

// ------------------------------------------------------------------ view

describe('view', () => {
  test('one swimlane per agent plus board, marks focusable with titles', async () => {
    const { el } = await mounted({});
    const lanes = el.querySelectorAll('.tl-lane');
    assert.equal(lanes.length, 3);
    const marks = el.querySelectorAll('.tl-marks')[0].children;
    assert.equal(marks.length, 7);
    for (const m of marks) {
      assert.equal(m.getAttribute('tabindex'), '0');
      assert.ok(m.getAttribute('title'), 'mark has a title');
      assert.equal(m.getAttribute('aria-label'), m.getAttribute('title'));
    }
    assert.match(el.querySelector('[role="status"]').textContent, /3 lanes/);
  });
  test('span buttons patch the route and a span change refetches', async () => {
    const { el, store, api } = await mounted({});
    assert.equal(api.urls.length, 1);
    const btn = el.querySelector('[data-span="1h"]');
    btn.dispatchEvent(new Event('click'));
    assert.equal(globalThis.location.hash, '#/timeline?span=1h');
    store.set({ ui: { route: { view: 'timeline', params: { span: '1h' } } } });
    await sleep(10);
    assert.equal(api.urls.length, 2);
    const since = (u) => Number(/since=(\d+)/.exec(u)[1]);
    const gap = since(api.urls[0]) - since(api.urls[1]);
    assert.ok(Math.abs(gap - 2700) < 5, '1h starts 45 min earlier, got ' + gap);
    assert.equal(btn.getAttribute('aria-pressed'), 'true');
  });
  test('ws routes into the fetch url', async () => {
    const { api } = await mounted({ ws: 'w-api' });
    assert.match(api.urls[0], /ws=w-api/);
  });
  test('table toggle shows a text alternative', async () => {
    const { el } = await mounted({});
    const wrap = el.querySelector('table').parentNode;
    assert.equal(wrap.hasAttribute('hidden'), true);
    el.querySelectorAll('button').find((b) => b.textContent === 'table').dispatchEvent(new Event('click'));
    assert.equal(wrap.hasAttribute('hidden'), false);
    const rows = wrap.querySelectorAll('tbody')[0].children;
    assert.equal(rows.length, 3);
    assert.match(wrap.textContent, /a1/);
    assert.match(wrap.textContent, /board/);
  });
  test('error and empty states read as text', async () => {
    const bad = await mounted({}, { error: 'denied', status: 403 });
    assert.match(bad.el.querySelector('[role="status"]').textContent, /denied/);
    bad.un();
    const empty = await mounted({}, { from: 1, to: 2, truncated: false, agents: [], items: [] });
    assert.match(empty.el.querySelector('[role="status"]').textContent, /no agents/);
  });
  test('heavy lanes bucket in the DOM and the note says so', async () => {
    const d = sample();
    for (let i = 0; i < 2500; i++) {
      d.items.push({ a: 'a1', k: 'tool', t0: d.from + (i % 899), t1: null, tool: 'Bash', ok: true });
    }
    const { el } = await mounted({}, d);
    const first = el.querySelectorAll('.tl-marks')[0].children;
    assert.ok(first.length <= 1000, 'lane share of the cap, got ' + first.length);
    assert.match(el.querySelector('[role="status"]').textContent, /bucketed/);
  });
  test('view + css stay inside the 8KB budget', () => {
    const js = statSync(new URL('../../orch/ui/js/views/timeline.js', import.meta.url)).size;
    const css = statSync(new URL('../../orch/ui/css/timeline.css', import.meta.url)).size;
    assert.ok(js + css <= 8192, `timeline view is ${js + css} bytes`);
  });
  test('unmount removes the view, unbinds keys and stops refetch', async () => {    const { el, store, api, un } = await mounted({});
    const pop = keys.pushScope('timeline');
    assert.ok(keys.help().some((b) => b.label === 'Text table'));
    const n = api.urls.length;
    un();
    pop();
    assert.equal(el.children.length, 0);
    assert.ok(!keys.help().some((b) => b.label === 'Text table'));
    store.set({ ui: { route: { view: 'timeline', params: { span: '6h' } } } });
    await sleep(10);
    assert.equal(api.urls.length, n, 'no fetch after unmount');
  });
});
