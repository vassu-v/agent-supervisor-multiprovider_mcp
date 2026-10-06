// Models view (#/models): rollup maths, refresh throttle, toggle bodies.
// Run: node --test tests/ui/test_models.mjs
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { install, Event } from './shim.mjs';

import {
  mount, unmount,
} from '../../orch/ui/js/views/models.js';
import {
  REFRESH_MS, MODELS_URL,
  agentTokens, modelRollup, capsTable, shouldRefresh, toggleBody,
} from '../../orch/ui/js/components/models-rollup.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/10-agents.json', import.meta.url), 'utf8'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let doc;
beforeEach(() => { doc = install(); });

function fakeStore(state) {
  const subs = [];
  return {
    get: () => state,
    subscribe(sel, cb) {
      const s = { sel, cb };
      subs.push(s);
      cb(sel(state), state);
      return () => { subs.splice(subs.indexOf(s), 1); };
    },
    flush() {},
  };
}

function fakeApi(models, postImpl) {
  const calls = [];
  return {
    calls,
    get: async (path) => { calls.push(['GET', path]); return JSON.parse(JSON.stringify(models)); },
    post: async (path, body) => {
      calls.push(['POST', path, body]);
      if (postImpl) return postImpl(path, body);
      return { name: body.name, enabled: body.enabled };
    },
  };
}

function state() {
  return {
    providers: JSON.parse(JSON.stringify(fixture.providers)),
    agents: JSON.parse(JSON.stringify(fixture.list)),
  };
}

// ------------------------------------------------------------------ pure

describe('agentTokens', () => {
  test('input + output only; cache reads are not spend', () => {
    assert.equal(agentTokens({ input: 10, output: 5, cache_read: 99 }), 15);
    assert.equal(agentTokens(null), 0);
    assert.equal(agentTokens({}), 0);
  });
});

describe('modelRollup', () => {
  test('groups by provider+model with agent and token counts', () => {
    const rows = [
      { provider: 'claude', model: 'm1', usage: { input: 100, output: 50, cache_read: 9 } },
      { provider: 'claude', model: 'm1', usage: { input: 10, output: 0 } },
      { provider: 'agy', model: 'm1', usage: { input: 7, output: 1 } },
      { provider: 'claude', model: null, usage: null },
    ];
    const r = modelRollup(rows);
    assert.equal(r.length, 3);
    assert.deepEqual([r[0].provider, r[0].model, r[0].agents, r[0].tokens], ['claude', 'm1', 2, 160]);
    assert.deepEqual([r[1].provider, r[1].model], ['agy', 'm1']);
    assert.deepEqual([r[2].provider, r[2].model, r[2].agents, r[2].tokens], ['claude', '', 1, 0]);
  });
  test('sorts by agents, then tokens, then names', () => {
    const rows = [
      { provider: 'b', model: 'x', usage: { input: 1, output: 0 } },
      { provider: 'a', model: 'y', usage: { input: 500, output: 0 } },
      { provider: 'a', model: 'y', usage: { input: 1, output: 0 } },
      { provider: 'a', model: 'z', usage: { input: 900, output: 0 } },
    ];
    assert.deepEqual(modelRollup(rows).map((r) => r.provider + ':' + r.model), ['a:y', 'a:z', 'b:x']);
  });
  test('empty input', () => {
    assert.deepEqual(modelRollup([]), []);
    assert.deepEqual(modelRollup(null), []);
  });
  test('matches the fixture totals', () => {
    const r = modelRollup(fixture.list);
    assert.equal(r.reduce((s, x) => s + x.agents, 0), fixture.list.length);
    const one = r.find((x) => x.model === 'claude-opus-4-1');
    const want = fixture.list
      .filter((a) => a.provider === one.provider && a.model === one.model)
      .reduce((s, a) => s + (a.usage.input + a.usage.output), 0);
    assert.equal(one.tokens, want);
  });
});

describe('capsTable', () => {
  test('union of caps, yes/no/raw words, dash for missing', () => {
    const t = capsTable([
      { name: 'a', caps: { steer: true, interrupt: true } },
      { name: 'b', caps: { steer: false, interrupt: 'restart', extra: 1 } },
      { name: 'c', caps: {} },
    ]);
    assert.deepEqual(t.cols, ['a', 'b', 'c']);
    assert.deepEqual(t.rows.map((r) => r.cap), ['steer', 'interrupt', 'extra']);
    const byCap = new Map(t.rows.map((r) => [r.cap, r.cells.map((c) => c.display)]));
    assert.deepEqual(byCap.get('steer'), ['yes', 'no', '—']);
    assert.deepEqual(byCap.get('interrupt'), ['yes', 'restart', '—']);
  });
  test('fixture providers keep their real caps', () => {
    const t = capsTable(fixture.providers);
    const steer = t.rows.find((r) => r.cap === 'steer');
    assert.ok(steer);
    assert.equal(steer.cells.find((c) => c.name === 'claude').display, 'yes');
    assert.equal(steer.cells.find((c) => c.name === 'agy').display, 'no');
  });
});

describe('shouldRefresh + toggleBody', () => {
  test('first load always; then at most once per 30 s', () => {
    assert.equal(REFRESH_MS, 30000);
    assert.equal(shouldRefresh(0, 1000), true);
    assert.equal(shouldRefresh(undefined, 1000), true);
    assert.equal(shouldRefresh(1000, 1000 + 29999), false);
    assert.equal(shouldRefresh(1000, 1000 + 30000), true);
  });
  test('toggle body flips enabled', () => {
    assert.deepEqual(toggleBody('claude', true), { name: 'claude', enabled: false });
    assert.deepEqual(toggleBody('agy', false), { name: 'agy', enabled: true });
  });
});

// ------------------------------------------------------------------ view

describe('mount', () => {
  test('fetches /api/models?limit=500 once on open and renders rows', async () => {
    const st = state();
    const api = fakeApi(fixture.models);
    const store = fakeStore(st);
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    const cleanup = mount(root, store, api);
    assert.deepEqual(api.calls.filter((c) => c[0] === 'GET').map((c) => c[1]), [MODELS_URL]);
    await sleep(5);
    const status = root.querySelector('.m-status').textContent;
    assert.match(status, /models/);
    const ids = root.querySelectorAll('.m-id').map((n) => n.textContent);
    assert.ok(ids.includes('claude-sonnet-4-5'), 'discovered model id shown, got: ' + ids.join(','));
    const eff = root.querySelectorAll('.m-effw').map((n) => n.textContent);
    assert.ok(eff.some((w) => w.includes('low')), 'supported efforts shown as words');
    const uses = root.querySelectorAll('.m-use').map((n) => n.textContent);
    assert.ok(uses.some((u) => /agent/.test(u)), 'per-model agent counts shown');
    cleanup();
  });

  test('provider chips are switches; off means struck name', async () => {
    const st = state();
    st.providers.push({ name: 'opencode', enabled: false, state: 'disabled', caps: { steer: false } });
    const api = fakeApi(fixture.models);
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    const cleanup = mount(root, fakeStore(st), api);
    await sleep(5);
    const chips = root.querySelectorAll('.prov');
    assert.equal(chips.length, st.providers.length);
    const off = root.querySelector('[data-name="opencode"]');
    assert.equal(off.classList.contains('off'), true, 'off provider dimmed (62% + strike live in styles.css)');
    const tog = off.querySelector('.tog');
    assert.equal(tog.tagName, 'BUTTON', 'keyboard-operable by construction');
    assert.equal(tog.getAttribute('role'), 'switch');
    assert.equal(tog.getAttribute('aria-checked'), 'false');
    assert.equal(tog.getAttribute('aria-label'), 'opencode disabled');
    const onTog = root.querySelector('[data-name="claude"]').querySelector('.tog');
    assert.equal(onTog.getAttribute('aria-checked'), 'true');
    cleanup();
  });

  test('toggle posts the flipped body to /api/provider', async () => {
    const st = state();
    const api = fakeApi(fixture.models);
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    const cleanup = mount(root, fakeStore(st), api);
    await sleep(5);
    const tog = root.querySelector('[data-name="claude"]').querySelector('.tog');
    tog.dispatchEvent(new Event('click', { bubbles: true }));
    await sleep(5);
    const posts = api.calls.filter((c) => c[0] === 'POST');
    assert.equal(posts.length, 1);
    assert.equal(posts[0][1], '/api/provider');
    assert.deepEqual(posts[0][2], { name: 'claude', enabled: false });
    cleanup();
  });

  test('refresh is gated to once per 30 s', async () => {
    const st = state();
    const api = fakeApi(fixture.models);
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    const cleanup = mount(root, fakeStore(st), api);
    await sleep(5);
    const btn = root.querySelector('.m-refresh');
    assert.equal(btn.disabled, true, 'button disabled right after the opening fetch');
    btn.dispatchEvent(new Event('click', { bubbles: true }));
    await sleep(5);
    assert.equal(api.calls.filter((c) => c[0] === 'GET').length, 1, 'second fetch inside 30 s is ignored');
    const realNow = Date.now;
    try {
      Date.now = () => realNow() + 31000;
      btn.dispatchEvent(new Event('click', { bubbles: true }));
      await sleep(5);
    } finally {
      Date.now = realNow;
    }
    assert.equal(api.calls.filter((c) => c[0] === 'GET').length, 2, 'fetch allowed after 30 s');
    cleanup();
  });

  test('capability matrix has one row per cap and one column per provider', async () => {
    const st = state();
    const api = fakeApi(fixture.models);
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    const cleanup = mount(root, fakeStore(st), api);
    await sleep(5);
    const table = root.querySelector('.m-caps-t');
    assert.ok(table, 'caps table rendered');
    const head = table.querySelectorAll('th').map((n) => n.textContent);
    assert.ok(head.includes('Capability'));
    for (const p of st.providers) assert.ok(head.includes(p.name), 'column for ' + p.name);
    const rows = table.querySelector('tbody').querySelectorAll('tr');
    assert.ok(rows.length >= 4, 'steer/interrupt/usage/native_queue rows, got ' + rows.length);
    cleanup();
  });

  test('unmount removes the view', async () => {
    const st = state();
    const api = fakeApi(fixture.models);
    const root = doc.createElement('div');
    doc.body.appendChild(root);
    const cleanup = mount(root, fakeStore(st), api);
    await sleep(5);
    assert.ok(root.childNodes.length > 0);
    cleanup();
    unmount();
    assert.equal(root.childNodes.length, 0);
  });
});

// ------------------------------------------------------------------ guards

describe('source guards', () => {
  const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  test('no banned strings in the new UI files', () => {
    const bans = ['inner' + 'HTML', 'outer' + 'HTML', 'insertAdjacent' + 'HTML',
      'document.write', 'new Function', 'java' + 'script:', 'style=',
      'http://', 'https://'];
    for (const f of ['../../orch/ui/js/views/models.js', '../../orch/ui/js/components/models-rollup.js', '../../orch/ui/css/models.css']) {
      const src = read(f);
      for (const b of bans) assert.ok(!src.includes(b), `${f} contains banned ${b}`);
      const onAttr = src.match(/[^a-zA-Z]on[a-z]+=("|')/g) || [];
      assert.deepEqual(onAttr, [], `${f} has handler attributes: ${onAttr}`);
      assert.ok(!src.includes('eval('), `${f} contains eval(`);
    }
  });
  test('budget: view JS at most 8 KB', () => {
    const n = statSync(new URL('../../orch/ui/js/views/models.js', import.meta.url)).size;
    assert.ok(n <= 8 * 1024, `models.js is ${n} bytes, budget is 8192`);
  });
});
