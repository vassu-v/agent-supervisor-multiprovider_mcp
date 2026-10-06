// Regression tests for the PR #2 review findings. Run: node --test "tests/ui/*.mjs"
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { install } from './shim.mjs';

import { createStore } from '../../orch/ui/js/core/store.js';
import { createSync } from '../../orch/ui/js/core/sync.js';
import * as model from '../../orch/ui/js/components/fleet-model.js';
import { createYard, updateYard } from '../../orch/ui/js/components/fleet-yard.js';
import { postNode, updatePost } from '../../orch/ui/js/components/board-post.js';
import { modelRollup, REFRESH_MS } from '../../orch/ui/js/components/models-rollup.js';
import { table, grid } from '../../orch/ui/js/components/timeline-table.js';
import { mount as mountModels } from '../../orch/ui/js/views/models.js';
import { mount as mountTimeline } from '../../orch/ui/js/views/timeline.js';

let doc;
beforeEach(() => { doc = install(); });

const NOW = 1790000000;
const mk = (id, ws, status, parent = null) => ({
  id, status, workspace: ws, workspace_name: ws, provider: 'agy', usage: {}, parent,
  last_event: { ts: NOW - 5 },
});
const build = (agents, params = {}, over = {}) => model.buildFleet({
  agents, workspaces: [{ id: 'w1', name: 'one' }], boards: {}, escalations: [],
  params, now: NOW, openYards: new Set(), openDead: new Set(), ...over,
});

describe('fleet model', () => {
  test('a dead status filter never folds the dead agents it selects', () => {
    const dead = Array.from({ length: 10 }, (_, i) => mk('d' + i, 'w1', 'dead'));
    const y = build(dead, { st: 'dead' }).yards[0];
    assert.equal(y.folded, false);
    assert.equal(y.canFold, false);
    assert.equal(y.rows.length, 10);
    assert.equal(build(dead, {}).yards[0].folded, true, 'unfiltered still folds');
  });
  test('folding dead parents promotes live children to roots', () => {
    const agents = [mk('p', 'w1', 'dead'), mk('c', 'w1', 'idle', 'p')];
    for (let i = 0; i < 8; i++) agents.push(mk('x' + i, 'w1', 'idle'));
    const y = build(agents).yards[0];
    assert.equal(y.folded, true);
    const c = y.rows.find((r) => r.agent.id === 'c');
    assert.equal(c.depth, 0);
    assert.deepEqual(c.cont, []);
    assert.equal(y.maxDepth, 0);
  });
  test('small yards cannot fold', () => {
    const y = build([mk('a', 'w1', 'idle'), mk('b', 'w1', 'dead')]).yards[0];
    assert.equal(y.canFold, false);
  });
});

describe('fleet yard', () => {
  const hooks = { toggleYard() {}, toggleDead() {}, hl() {}, open() {} };
  test('collapsed summary follows live data', () => {
    const idle = (n) => Array.from({ length: n }, (_, i) => mk('i' + i, 'w1', 'idle'));
    const y1 = build(idle(30)).yards[0];
    assert.equal(y1.collapsed, true);
    const sec = createYard(y1, NOW, hooks);
    document.body.appendChild(sec);
    const y2 = build(idle(31)).yards[0];
    updateYard(sec, y2, NOW, hooks);
    assert.match(sec.querySelector('.yard-sum').textContent, /31 live/);
    assert.match(sec.querySelector('.yard-sum').textContent, /Show 31 agents/);
  });
  test('no Hide finished button on a yard that cannot fold', () => {
    const small = build([mk('a', 'w1', 'idle'), mk('b', 'w1', 'dead')]).yards[0];
    const sec = createYard(small, NOW, hooks);
    assert.equal(sec.querySelector('button.fold'), null);
    const big = [mk('d', 'w1', 'dead')];
    for (let i = 0; i < 9; i++) big.push(mk('x' + i, 'w1', 'idle'));
    const sec2 = createYard(build(big, {}, { openDead: new Set(['w1']) }).yards[0], NOW, hooks);
    assert.match(sec2.querySelector('button.fold').textContent, /Hide finished/);
  });
});

describe('board post', () => {
  test('a reused node gets an answer form when it becomes an open question', () => {
    const ctx = { byId: new Map(), agents: [], api: { post: async () => ({}) }, ws: 'w1' };
    const p = { id: 7, ts: 1, ws: 'w1', sender: 'agent:a1', sender_kind: 'agent', kind: 'question', status: 'answered', text: 'q' };
    const node = postNode(p, ctx);
    assert.equal(node.querySelector('form.ans'), null);
    updatePost(node, { ...p, status: 'open' }, ctx);
    assert.ok(node.querySelector('form.ans'));
    assert.equal(node.querySelector('form.ans').hidden, false);
  });
});

describe('models', () => {
  test('rollup keys do not collide across provider/model splits', () => {
    const r = modelRollup([{ provider: 'ab', model: 'c' }, { provider: 'a', model: 'bc' }]);
    assert.equal(r.length, 2);
    assert.notEqual(r[0].key, r[1].key);
  });
  test('Refresh re-enables itself when the cooldown expires, and unmount clears the timer', async () => {
    const realNow = Date.now;
    const realST = globalThis.setTimeout;
    const realCT = globalThis.clearTimeout;
    const timers = [];
    const cleared = [];
    const store = { get: () => ({ providers: [], agents: [] }), subscribe: () => () => {} };
    const api = { get: async () => ({}), post: async () => ({}) };
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    let un;
    try {
      globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
      globalThis.clearTimeout = (id) => { cleared.push(id); };
      un = mountModels(root, store, api);
      for (let i = 0; i < 5; i++) await Promise.resolve();
      const btn = root.querySelector('.m-refresh');
      assert.equal(btn.disabled, true);
      const t = timers.filter((x) => x.ms > 1000 && x.ms <= REFRESH_MS + 100).pop();
      assert.ok(t, 'a recheck timer is scheduled at the cooldown deadline');
      Date.now = () => realNow() + REFRESH_MS + 50;
      t.fn();
      assert.equal(btn.disabled, false);
      cleared.length = 0;
      un();
      un = null;
      assert.ok(cleared.length > 0, 'unmount clears the timer');
    } finally {
      Date.now = realNow;
      globalThis.setTimeout = realST;
      globalThis.clearTimeout = realCT;
      if (un) un();
    }
  });
});

describe('timeline', () => {
  test('span group carries the tl-spans class the css targets', () => {
    const state = { ui: { route: { view: 'timeline', params: {} } } };
    const store = { get: () => state, set() {}, subscribe: () => () => {} };
    const el = doc.createElement('div');
    doc.body.appendChild(el);
    const un = mountTimeline(el, store, { get: async () => ({ agents: [], items: [] }), post: async () => ({}) });
    assert.ok(el.querySelector('.tl-spans'));
    un();
  });
  test('table counts come from raw marks and rows key by lane', () => {
    const from = 1000;
    const to = 1900;
    const items = [];
    for (let i = 0; i < 2500; i++) items.push({ a: 'a1', k: 'turn', t0: from + (i % 800), t1: from + (i % 800) + 1, ok: true, int: false });
    items.push({ a: 'a1', k: 'guard', t: from + 5, decision: 'block' });
    items.push({ a: 'a1', k: 'esc', t: from + 6, id: 'e', state: 'pending' });
    const agents = [{ id: 'a1' }, { id: 'a11' }];
    const t = table();
    doc.body.appendChild(t.wrap);
    grid(t.body, { agents, items }, from, to);
    const rows = t.body.querySelectorAll('tr');
    assert.equal(rows.length, 2);
    const cells = [...rows[0].querySelectorAll('td')].map((c) => c.textContent);
    assert.deepEqual(cells, ['2500', '0', '1', '1']);
    grid(t.body, { agents, items: items.slice(-2) }, from, to); // updates in place
    assert.deepEqual([...t.body.querySelectorAll('tr')[0].querySelectorAll('td')].map((c) => c.textContent), ['0', '0', '1', '1']);
  });
});

describe('sync board', () => {
  test('a stale board reply never overwrites a newer one', async () => {
    const store = createStore({ boards: {}, conn: { state: 'ok', error: '', at: 0 }, ui: { route: { view: 'board', params: {} } } });
    const waits = [];
    const api = { get: () => new Promise((res) => waits.push(res)) };
    const sync = createSync(store, api);
    const a = sync.refresh('board', 'w1');
    const b = sync.refresh('board', 'w1');
    waits[1]({ posts: [{ id: 2, kind: 'info' }], last: 2, open_questions: 0 });
    await b;
    waits[0]({ posts: [{ id: 1, kind: 'info' }], last: 1, open_questions: 0 });
    await a;
    store.flush();
    assert.equal(store.get().boards.w1.last, 2);
  });
});

describe('app view mount', () => {
  // app.js boots on import (needs a real page), so this guards the source of the fix.
  test('showView ignores a repeat request for the view that is still loading', () => {
    const src = readFileSync(new URL('../../orch/ui/js/app.js', import.meta.url), 'utf8');
    assert.match(src, /pendingName === name\) return;/);
    assert.match(src, /pendingName = null;\s*store\.set\(\{ conn/);
  });
});
