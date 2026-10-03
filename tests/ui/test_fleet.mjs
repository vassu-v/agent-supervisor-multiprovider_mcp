// Fleet view: pure model tests + DOM render budgets using tests/ui/shim.mjs.
// Run: node --test "tests/ui/*.mjs" (the quoted glob; a bare directory fails on Node 24).
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { install, serialize, counts, resetCounts, Event } from './shim.mjs';

import { createStore } from '../../orch/ui/js/core/store.js';
import * as keys from '../../orch/ui/js/core/keys.js';
import * as model from '../../orch/ui/js/components/fleet-model.js';
import { railShapes, railWidth, xFor } from '../../orch/ui/js/components/fleet-rail.js';
import { mount, unmount } from '../../orch/ui/js/views/fleet.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');
const fixture = (n) => JSON.parse(readFileSync(join(HERE, 'fixtures', `${n}-agents.json`), 'utf8'));

function boardsOf(fx) {
  const out = {};
  for (const [ws, b] of Object.entries(fx.boards || {})) out[ws] = b;
  return out;
}

function fakeStore(fx, params = {}, over = {}, densityApplied = 'comfortable') {
  return createStore({
    agents: fx.list,
    workspaces: fx.workspaces,
    boards: boardsOf(fx),
    providers: fx.providers || [],
    escalations: (fx.escalations || []).filter((e) => e.state === 'pending'),
    audit: [],
    conn: { state: 'ok', error: '', at: Date.now() },
    ui: {
      route: { view: 'fleet', params },
      density: 'auto', densityApplied, theme: 'system', selection: null,
    },
    ...over,
  });
}

const api = { get: async () => ({}), post: async () => ({}) };

function elements(el) {
  let n = 0;
  const walk = (x) => {
    if (x.nodeType === 1) n++;
    for (const c of x.childNodes) walk(c);
  };
  walk(el);
  return n;
}

function rowsOf(host) {
  return host.querySelectorAll('.row');
}

function rowIds(host) {
  return rowsOf(host).map((r) => r.getAttribute('data-id')).join(',');
}

let doc;
let host;
beforeEach(() => {
  doc = install();
  globalThis.location = { hash: '#/fleet' };
  keys.reset();
  host = doc.createElement('div');
  doc.body.appendChild(host);
});
afterEach(() => { unmount(); });

function show(fx, params = {}, over = {}, densityApplied = 'comfortable') {
  host.replaceChildren(); // app.js swaps the view root on navigation; mirror that
  const store = fakeStore(fx, params, over, densityApplied);
  mount(host, store, api);
  store.flush();
  return store;
}

// ---------------------------------------------------------------- budgets
describe('budgets and source hygiene', () => {
  const ui = join(ROOT, 'orch', 'ui');
  const walk = (dir, ext) => {
    const out = [];
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name);
      if (e.isDirectory()) out.push(...walk(p, ext));
      else if (e.name.endsWith(ext)) out.push(p);
    }
    return out;
  };
  test('view files stay small; fleet JS fits its share of the budgets', () => {
    const files = ['js/views/fleet.js', 'js/components/fleet-model.js', 'js/components/fleet-rail.js',
      'js/components/fleet-row.js', 'js/components/fleet-yard.js'];
    let sum = 0;
    for (const f of files) {
      const size = statSync(join(ui, f)).size;
      assert.ok(size <= 8192, `${f} is ${size}B (exceeds 8KB)`);
      sum += size;
    }
    assert.ok(sum <= 32 * 1024, `fleet JS is ${sum}B (exceeds 32KB)`);
    const css = statSync(join(ui, 'css', 'fleet.css')).size;
    assert.ok(css <= 4096, `fleet.css is ${css}B`);
  });
  test('fleet sources contain none of the banned strings', () => {
    const files = [...walk(join(ui, 'js', 'views'), '.js'), ...walk(join(ui, 'js', 'components'), '.js'),
      ...walk(join(ui, 'css'), '.css')].filter((f) => /fleet/.test(f));
    assert.ok(files.length >= 5, 'fleet files exist');
    const banned = ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'new Function',
      'javascript:', 'http://', 'https://', 'style=', 'eval('];
    for (const f of files) {
      const src = readFileSync(f, 'utf8');
      for (const b of banned) assert.ok(!src.includes(b), `${f} contains ${b}`);
      assert.ok(!/['"]on[a-z]+['"]\s*:/.test(src), `${f} has an on* handler key`);
    }
  });
});

// ---------------------------------------------------------------- model
describe('fleet-model', () => {
  test('effort pips and words never read model ids', () => {
    assert.deepEqual([model.effortPips('low'), model.effortPips('medium'), model.effortPips('high'),
      model.effortPips(null), model.effortPips('turbo')], [1, 2, 3, 0, 0]);
    assert.deepEqual([model.effortWord('low'), model.effortWord('medium'),
      model.effortWord('high'), model.effortWord(null)], ['low', 'med', 'high', 'free']);
  });
  test('sparkValues prefers turn_usage, else turns_full, else null', () => {
    assert.deepEqual(model.sparkValues({ turn_usage: [1, 2, 3] }), [1, 2, 3]);
    assert.deepEqual(model.sparkValues({ turn_usage: Array.from({ length: 20 }, (_, i) => i) }).length, 12);
    const a = { turns_full: [{ usage: { input: 3, output: 1 } }, { usage: { input: 1, output: 1 } }, {}] };
    assert.deepEqual(model.sparkValues(a), [4, 2]);
    assert.equal(model.sparkValues({ usage: { input: 9, output: 9 } }), null);
  });
  test('badges: quiet at 120s, stuck? at 600s, restarted N, needs review', () => {
    const now = 10000;
    const busy = (ts) => ({ status: 'busy', restarts: 0, last_event: { ts } });
    assert.deepEqual(model.badgesFor(busy(now - 10), now), []);
    assert.deepEqual(model.badgesFor(busy(now - 120), now).map((b) => b.label), ['quiet']);
    assert.deepEqual(model.badgesFor(busy(now - 900), now).map((b) => b.label), ['stuck?']);
    assert.deepEqual(model.badgesFor({ status: 'busy', restarts: 2, last_event: { ts: now - 1 } }, now).map((b) => b.label), ['restarted 2']);
    assert.deepEqual(model.badgesFor({ status: 'idle', needs_review: true }, now).map((b) => b.label), ['needs review']);
    assert.equal(model.badgesFor({ status: 'idle' }, now).length, 0);
  });
  test('statusFor: dead, held, busy, starting, idle', () => {
    const now = 5000;
    assert.deepEqual(model.statusFor({ status: 'dead' }, false, null, now).word, 'done');
    assert.equal(model.statusFor({ status: 'dead', stopped_by: 'x' }, false, null, now).word, 'stopped');
    const held = model.statusFor({ status: 'busy' }, true, 'Bash', now);
    assert.equal(held.platform, 'wait');
    assert.ok(held.word.includes('Bash'));
    const busy = model.statusFor({ status: 'busy', turn_t0: now - 240 }, false, null, now);
    assert.equal(busy.platform, 'busy');
    assert.ok(busy.word.includes('4m'), busy.word);
    assert.equal(model.statusFor({ status: 'starting' }, false, null, now).word, 'starting');
    assert.equal(model.statusFor({ status: 'idle' }, false, null, now).platform, 'idle');
  });
  test('buildFleet on the 10-agent fixture: yards, tabs, attention first', () => {
    const fx = fixture(10);
    const snap = model.buildFleet({
      agents: fx.list, workspaces: fx.workspaces, boards: boardsOf(fx),
      escalations: fx.escalations, params: {}, now: 1790000000, openYards: new Set(), openDead: new Set(),
    });
    assert.equal(snap.yards.length, 2);
    assert.deepEqual(snap.tabs.map((t) => t.id), ['all', 'w-api', 'w-web']);
    const apiYard = snap.yards.find((y) => y.id === 'w-api');
    assert.equal(apiYard.rows[0].agent.id, 'a5', 'pending escalation sorts first');
    assert.ok(apiYard.rows.every((r) => r.agent.status !== 'dead') || apiYard.deadCount === 1);
    assert.equal(apiYard.folded, false, '5-agent yard does not fold dead');
    assert.ok(apiYard.board.length <= 5);
  });
  test('filters narrow rows; unknown workspace empties the fleet', () => {
    const fx = fixture(10);
    const base = {
      agents: fx.list, workspaces: fx.workspaces, boards: boardsOf(fx),
      escalations: fx.escalations, now: 1790000000, openYards: new Set(), openDead: new Set(),
    };
    const q = model.buildFleet({ ...base, params: { q: 'flaky CI' } });
    assert.ok(q.yards.length > 0 && q.yards.every((y) => y.rows.length > 0));
    assert.ok(q.yards.flatMap((y) => y.rows).length < fx.list.length);
    const prov = model.buildFleet({ ...base, params: { prov: 'agy' } });
    assert.ok(prov.yards.flatMap((y) => y.rows).every((r) => r.agent.provider === 'agy'));
    const st = model.buildFleet({ ...base, params: { st: 'dead' } });
    assert.ok(st.yards.flatMap((y) => y.rows).every((r) => r.agent.status === 'dead'));
    const ws = model.buildFleet({ ...base, params: { ws: 'w-web' } });
    assert.deepEqual(ws.yards.map((y) => y.id), ['w-web']);
    assert.deepEqual(model.buildFleet({ ...base, params: { ws: 'nope' } }).yards, []);
  });
  test('60-agent yards fold dead; openDead re-opens them', () => {
    const fx = fixture(60);
    const base = {
      agents: fx.list, workspaces: fx.workspaces, boards: boardsOf(fx),
      escalations: fx.escalations, params: {}, now: 1790000000, openYards: new Set(), openDead: new Set(),
    };
    const folded = model.buildFleet(base);
    assert.ok(folded.yards.length >= 2);
    for (const y of folded.yards) {
      if (y.deadCount > 0) {
        assert.equal(y.folded, true);
        assert.ok(y.rows.every((r) => r.agent.status !== 'dead'));
      }
    }
    const open = model.buildFleet({ ...base, openDead: new Set(folded.yards.map((y) => y.id)) });
    assert.ok(open.yards.flatMap((y) => y.rows).some((r) => r.agent.status === 'dead'));
  });
  test('quiet yards collapse only at scale', () => {
    const mk = (id, ws, status) => ({
      id, status, workspace: ws, workspace_name: ws, provider: 'agy', usage: {},
      last_event: { ts: 1790000000 - 5 },
    });
    const idle30 = Array.from({ length: 30 }, (_, i) => mk('i' + i, 'w1', 'idle'));
    const ws = [{ id: 'w1', name: 'one' }, { id: 'w2', name: 'two' }];
    const boards = {};
    const small = model.buildFleet({
      agents: idle30.slice(0, 10), workspaces: ws, boards, escalations: [],
      params: {}, now: 1790000000, openYards: new Set(), openDead: new Set(),
    });
    assert.ok(small.yards.every((y) => !y.collapsed), 'below scale nothing collapses');
    const big = model.buildFleet({
      agents: idle30, workspaces: ws, boards, escalations: [],
      params: {}, now: 1790000000, openYards: new Set(), openDead: new Set(),
    });
    assert.ok(big.yards.length > 0 && big.yards.every((y) => y.collapsed), 'all-idle yards collapse at scale');
    const mixed = model.buildFleet({
      agents: [...idle30.slice(0, 29), mk('b0', 'w1', 'busy')], workspaces: ws, boards, escalations: [],
      params: {}, now: 1790000000, openYards: new Set(), openDead: new Set(),
    });
    assert.equal(mixed.yards.find((y) => y.id === 'w1').collapsed, false, 'busy yard stays open');
    const forced = model.buildFleet({
      agents: idle30, workspaces: ws, boards, escalations: [],
      params: {}, now: 1790000000, openYards: new Set(['w1']), openDead: new Set(),
    });
    assert.equal(forced.yards.find((y) => y.id === 'w1').collapsed, false, 'user can re-open');
  });
});

// ---------------------------------------------------------------- rail
describe('fleet-rail', () => {
  test('width caps at depth 3 (102px gutter)', () => {
    assert.equal(railWidth(0), 48);
    assert.equal(railWidth(3), 102);
    assert.equal(railWidth(9), 102);
    assert.equal(xFor(1) - xFor(0), 18);
  });
  test('shapes: turnout for children, platform per status, question diamond', () => {
    const root = railShapes({ depth: 0, last: true, hasChildren: true, cont: [], platform: 'busy', question: false }, 48);
    assert.ok(root.some(([t, a]) => t === 'circle' && a.class === 'r-node-busy'));
    assert.ok(root.some(([t, a]) => t === 'path' && /V48/.test(a.d)), 'kid lane runs down');
    const kid = railShapes({ depth: 1, last: true, hasChildren: false, cont: [], platform: 'idle', question: true }, 66);
    assert.ok(kid.some(([t, a]) => t === 'path' && a.d.includes('Q')), 'turnout curve');
    assert.ok(kid.some(([t, a]) => t === 'circle' && a.class === 'r-node-idle'));
    assert.ok(kid.some(([t, a]) => t === 'path' && a.class === 'r-q'));
    const wait = railShapes({ depth: 1, last: false, hasChildren: false, cont: [0], platform: 'wait', question: false }, 66);
    assert.ok(wait.some(([t, a]) => t === 'circle' && a.class === 'r-sig'), 'red signal head');
    assert.ok(wait.some(([t, a]) => t === 'path' && a.d.startsWith('M18 ')), 'continuing lane');
    const dead = railShapes({ depth: 0, last: true, hasChildren: false, cont: [], platform: 'dead', question: false }, 48);
    assert.ok(dead.some(([t, a]) => t === 'path' && a.class === 'r-hollow-o'), 'hollow siding');
    assert.ok(dead.some(([t, a]) => t === 'rect' && a.class === 'r-stop'), 'buffer stop');
  });
});

// ---------------------------------------------------------------- render
describe('fleet render', () => {
  test('1/10/60 fixtures render within the node budgets', () => {
    show(fixture(1));
    const n1 = elements(host);
    show(fixture(10));
    const n10 = elements(host);
    // 60 live agents switch the app to compact density (auto above 24 live);
    // compact rows omit goal and last line, like the compact stylesheet.
    show(fixture(60), {}, {}, 'compact');
    const n60 = elements(host);
    assert.ok(n1 < n10 && n10 < n60, `scales with agents: ${n1}/${n10}/${n60}`);
    assert.ok(n60 <= 1500, `60 agents use ${n60} element nodes (budget 1500)`);
    assert.ok(n60 / 60 < 25, `per-row average ${(n60 / 60).toFixed(1)} under 25`);
  });
  test('one-row change touches only that row', () => {
    const fx = fixture(10);
    const store = show(fx);
    const before = new Map(rowsOf(host).map((r) => [r.getAttribute('data-id'), r]));
    const perWs = new Map();
    for (const a of fx.list) perWs.set(a.workspace, (perWs.get(a.workspace) || 0) + 1);
    const foldedDead = fx.list.filter((a) => a.status === 'dead' && perWs.get(a.workspace) > 8).length;
    assert.equal(before.size, fx.list.length - foldedDead, 'all visible agents have rows');
    resetCounts();
    const target = fx.list.find((a) => a.status !== 'dead');
    store.set({ agents: fx.list.map((a) => (a.id === target.id ? { ...a, last_text: 'brand new line' } : a)) });
    store.flush();
    assert.equal(counts.creates, 0, 'no nodes created');
    assert.ok(counts.inserts <= 4 && counts.removes <= 4, `tiny patch (inserts=${counts.inserts} removes=${counts.removes})`);
    for (const r of rowsOf(host)) {
      assert.equal(r, before.get(r.getAttribute('data-id')), 'row identity kept');
    }
    assert.ok(host.textContent.includes('brand new line'));
  });
  test('one-row change in compact touches only that row', () => {
    const fx = fixture(60);
    const store = show(fx, {}, {}, 'compact');
    const before = new Map(rowsOf(host).map((r) => [r.getAttribute('data-id'), r]));
    resetCounts();
    const target = fx.list.find((a) => a.status !== 'dead');
    store.set({ agents: fx.list.map((a) => (a.id === target.id ? { ...a, model: 'changed-model' } : a)) });
    store.flush();
    assert.equal(counts.creates, 0, 'no nodes created');
    assert.ok(counts.inserts <= 4 && counts.removes <= 4, `tiny patch (inserts=${counts.inserts} removes=${counts.removes})`);
    for (const r of rowsOf(host)) {
      assert.equal(r, before.get(r.getAttribute('data-id')), 'row identity kept');
    }
    assert.ok(host.textContent.includes('changed-model'));
  });
  test('route filters drive the rows', () => {
    const fx = fixture(10);
    const store = show(fx);
    const all = rowIds(host);
    store.set({ ui: { ...store.get().ui, route: { view: 'fleet', params: { q: 'flaky CI' } } } });
    store.flush();
    assert.ok(rowsOf(host).length < all.split(',').length);
    store.set({ ui: { ...store.get().ui, route: { view: 'fleet', params: { ws: 'w-web' } } } });
    store.flush();
    assert.equal(host.querySelectorAll('.yard').length, 1);
    assert.ok(rowIds(host).length > 0);
  });
  test('collapse renders a summary bar with a working expander', () => {
    const mk = (id) => ({
      id, status: 'idle', workspace: 'w9', workspace_name: 'nine', provider: 'agy', model: 'm',
      usage: {}, turns: 1, paths: [], goal: 'g', created: 1, last_event: { ts: 1790000000 - 5 },
    });
    const fx = fixture(1);
    const agents = [...Array.from({ length: 30 }, (_, i) => mk('z' + i))];
    const store = show(fx, {}, {
      agents, workspaces: [{ id: 'w9', name: 'nine', root: 'D:/w9', sessions: [] }], boards: {},
    });
    assert.equal(host.querySelectorAll('.row').length, 0, 'collapsed yard shows no rows');
    assert.ok(host.textContent.includes('Show 30 agents'));
    host.querySelector('.yard-sum').querySelector('button').dispatchEvent(new Event('click'));
    store.flush();
    assert.ok(rowsOf(host).length > 0, 'expander opens the yard');
  });
  test('dead fold into "N finished" and re-open', () => {
    const fx = fixture(60);
    const store = show(fx);
    assert.ok(!rowIds(host).split(',').some((id) => fx.list.find((a) => a.id === id && a.status === 'dead')),
      'dead rows are folded');
    const folds = host.querySelectorAll('.fold');
    assert.ok(folds.length > 0 && folds[0].textContent.includes('finished'));
    const deadTotal = fx.list.filter((a) => a.status === 'dead').length;
    assert.ok(host.textContent.includes(`${deadTotal} finished`) || folds.length > 1);
    folds[0].dispatchEvent(new Event('click'));
    store.flush();
    assert.ok(rowIds(host).split(',').some((id) => fx.list.find((a) => a.id === id && a.status === 'dead')),
      'dead rows re-appear after unfold');
    void store;
  });
  test('badges, pips, provider chips and tool lines render', () => {
    const now = Date.now() / 1000;
    const mk = (id, extra) => ({
      id, status: 'busy', workspace: 'w1', workspace_name: 'one', provider: 'agy', model: 'gemini-3-flash-low',
      usage: { input: 10, output: 5 }, turns: 2, paths: [], goal: 'g', created: now - 10,
      last_event: { ts: now - 3 }, turn_t0: now - 3, effort_applied: 'high', ...extra,
    });
    const fx = fixture(1);
    show(fx, {}, {
      agents: [
        mk('q1', { last_event: { ts: now - 130 } }),
        mk('s1', { last_event: { ts: now - 700 } }),
        mk('r1', { restarts: 2 }),
        mk('v1', { status: 'idle', needs_review: true, last_event: null, turn_t0: null }),
        mk('f1', { effort_applied: null, effort_warning: null, current_tool: 'Write', last_text: 'did a thing' }),
        mk('w1x', { effort_applied: 'low', effort_warning: 'fell back to low' }),
      ],
      workspaces: [{ id: 'w1', name: 'one', root: 'D:/one', sessions: [] }],
      boards: {},
    });
    const txt = host.textContent;
    for (const want of ['quiet', 'stuck?', 'restarted 2', 'needs review']) assert.ok(txt.includes(want), want);
    const warns = host.querySelectorAll('.eff-warn');
    assert.ok(warns.length > 0, 'warning marker');
    assert.ok(warns.some((w) => (w.getAttribute('title') || '').includes('fell back')), 'warning text in title');
    assert.ok(txt.includes('free'), 'model without effort shows free');
    assert.ok(host.querySelector('.p-agy'), 'provider shape+name chip');
    assert.ok(txt.includes('Write') && txt.includes('did a thing'), 'tool + last line');
  });
  test('agent-authored markup stays text', () => {
    const evil = '<img src=x onerror=alert(1)>';
    const fx = fixture(1);
    show(fx, {}, {
      agents: [{ ...fx.list[0], goal: evil, last_text: evil, paths: [evil] }],
    });
    assert.equal(host.querySelector('img'), null);
    assert.ok(host.textContent.includes(evil));
    assert.ok(serialize(host).includes('&lt;img'));
  });
  test('click opens the drawer route; j/k move the cursor', () => {
    const fx = fixture(10);
    show(fx);
    const first = rowsOf(host)[0];
    first.dispatchEvent(new Event('click'));
    assert.ok(globalThis.location.hash.includes('agent='), globalThis.location.hash);
    const pop = keys.pushScope('fleet');
    const second = rowsOf(host)[1];
    first.focus();
    assert.equal(doc.activeElement, first);
    keys.handle({ key: 'j', target: doc.body, preventDefault() {} });
    assert.equal(doc.activeElement, second);
    keys.handle({ key: 'k', target: doc.body, preventDefault() {} });
    assert.equal(doc.activeElement, first);
    pop();
  });
  test('60-agent patch averages under 8ms', () => {
    const fx = fixture(60);
    const store = show(fx, {}, {}, 'compact');
    const t0 = performance.now();
    const N = 50;
    for (let k = 0; k < N; k++) {
      const target = fx.list[k % fx.list.length];
      store.set({ agents: store.get().agents.map((a) => (a.id === target.id ? { ...a, turns: a.turns + 1 } : a)) });
      store.flush();
    }
    const per = (performance.now() - t0) / N;
    assert.ok(per < 8, `${per.toFixed(2)}ms per patch`);
  });
});
