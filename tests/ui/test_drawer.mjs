// B3 drawer tests: pure stream merge, caps-gated controls, stop form flow, hostile text as text,
// exact write bodies (/api/send /api/interrupt /api/stop), status-fetch discipline. Run: node --test tests/ui/
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { install, serialize, Event } from './shim.mjs';

import * as drawer from '../../orch/ui/js/views/drawer.js';
import * as logic from '../../orch/ui/js/components/drawer-logic.js';
const { mergeStream, stopReasonError, canSteer, canInterrupt } = logic;
import { createStore } from '../../orch/ui/js/core/store.js';

const tick = () => new Promise((r) => setTimeout(r, 5));

const agent = (over = {}) => ({
  id: 'a1', status: 'busy', provider: 'claude', model: 'claude-haiku-4-5',
  goal: 'port scripts', paths: ['docs/x.md'], turns: 2, queued: 0,
  caps: { steer: true, interrupt: true, usage: true, native_queue: false },
  effort: 'high', effort_applied: 'high', effort_warning: null,
  last_text: 'step 8 done', pending_escalation: null, ...over,
});

const stateFor = (agents, agentId) => ({
  agents,
  ui: { route: { view: 'fleet', params: agentId ? { agent: agentId } : {} } },
});

function fakeApi(hooks = {}) {
  const calls = { get: [], post: [] };
  return {
    calls,
    get: async (path) => { calls.get.push(path); return hooks.get ? hooks.get(path) : []; },
    post: async (path, body) => { calls.post.push([path, body]); return hooks.post ? hooks.post(path, body) : { result: 'ok' }; },
  };
}

const ev = (seq, type = 'text', extra = {}) => ({ seq, ts: 1789997000 + seq, type, text: 'line ' + seq, ...extra });

let cleanups = [];
beforeEach(() => { install(); cleanups = []; });
afterEach(() => { for (const fn of cleanups) fn(); cleanups = []; });

function mounted(agents, agentId, api) {
  const store = createStore(stateFor(agents, agentId));
  const el = document.createElement('aside');
  document.body.appendChild(el);
  cleanups.push(drawer.mount(el, store, api));
  return { store, el, api };
}

// ------------------------------------------------------------------ pure logic
describe('mergeStream', () => {
  test('incremental merge by seq: dedupe, sort, append', () => {
    const out = mergeStream([ev(1), ev(2)], [ev(2), ev(4), ev(3)]);
    assert.deepEqual(out.map((e) => e.seq), [1, 2, 3, 4]);
  });
  test('window cap keeps the newest 500', () => {
    const page = (from, n) => Array.from({ length: n }, (_, i) => ev(from + i));
    const out = mergeStream(page(1, 400), page(301, 300));
    assert.equal(out.length, 500);
    assert.equal(out[0].seq, 101);
    assert.equal(out[499].seq, 600);
  });
  test('ignores entries without a seq', () => {
    assert.deepEqual(mergeStream([{ no: 1 }], [ev(1)]).map((e) => e.seq), [1]);
  });
  test('stopReasonError requires a non-blank reason', () => {
    assert.equal(stopReasonError(''), 'A reason is required to stop an agent.');
    assert.equal(stopReasonError('   '), 'A reason is required to stop an agent.');
    assert.equal(stopReasonError(null), 'A reason is required to stop an agent.');
    assert.equal(stopReasonError('done here'), null);
  });
  test('caps gates', () => {
    assert.equal(canSteer(agent()), true);
    assert.equal(canSteer(agent({ caps: { steer: false, interrupt: true } })), false);
    assert.equal(canInterrupt(agent({ caps: { steer: false, interrupt: 'restart' } })), true);
    assert.equal(canInterrupt(agent({ caps: { steer: false, interrupt: false } })), false);
    assert.equal(canSteer(agent({ caps: undefined })), false);
  });
});

// ------------------------------------------------------------------ mounting
describe('drawer view', () => {
  test('header shows id, status word+glyph, provider, model, effort; hostile goal/paths/last_text stay text', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [] } : []) });
    const { el } = mounted([agent({ goal: evil, paths: [evil], last_text: evil })], 'a1', api);
    await tick();
    assert.match(el.querySelector('.aid').textContent, /a1/);
    assert.match(el.querySelector('.st').textContent, /busy/);
    assert.equal(el.querySelector('.st').querySelector('use').getAttribute('href'), '#s-busy');
    assert.match(el.textContent, /claude/);
    const goal = el.querySelector('.dw-goal');
    assert.equal(goal.childNodes.length, 1);
    assert.equal(goal.firstChild.nodeType, 3);
    assert.equal(goal.querySelector ? goal.querySelector('img') : null, null);
    assert.match(serialize(goal), /&lt;img/);
    for (const node of [el.querySelector('.dw-last'), ...el.querySelector('.dw-paths').querySelectorAll('.pt')]) {
      assert.equal(node.children.length, 0, 'no element children in agent text');
      assert.match(serialize(node), /&lt;img/);
    }
  });

  test('event stream merges incrementally by seq and caps the DOM at 500 lines', async () => {
    let live = Array.from({ length: 10 }, (_, i) => ev(i + 1));
    const api = fakeApi({
      get: (p) => {
        if (p.startsWith('/api/status')) return { turns_full: [] };
        const since = Number(/since=(\d+)/.exec(p)[1]);
        return live.filter((e) => e.seq > since);
      },
    });
    const { el } = mounted([agent()], 'a1', api);
    await tick();
    assert.equal(el.querySelectorAll('.dw-line').length, 10);
    const seen = api.calls.get.filter((p) => p.startsWith('/api/events')).length;
    assert.ok(seen >= 1);
    live = live.concat(Array.from({ length: 600 }, (_, i) => ev(11 + i)));
    await new Promise((r) => setTimeout(r, 1200));
    const lines = el.querySelectorAll('.dw-line');
    assert.ok(lines.length <= 500, `capped, got ${lines.length}`);
    assert.equal(lines.length, 500);
    assert.equal(lines[0].querySelectorAll('span')[0].textContent.split(' ')[0], '#111');
    const hostile = ev(9999, 'text', { text: '<script>alert(1)</script>' });
    live.push(hostile);
    await new Promise((r) => setTimeout(r, 1200));
    const last = el.querySelectorAll('.dw-line');
    assert.equal(last[last.length - 1].querySelector('.dw-text').children.length, 0);
    assert.match(serialize(last[last.length - 1]), /&lt;script&gt;/);
  });

  test('caps-gated controls: steer/interrupt disabled without caps', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [] } : []) });
    const { el } = mounted([agent({ caps: { steer: false, interrupt: false } })], 'a1', api);
    await tick();
    const radios = el.querySelectorAll('input[type="radio"]');
    const steer = radios.find((r) => r.value === 'steer');
    const intr = radios.find((r) => r.value === 'interrupt');
    assert.equal(steer.disabled, true);
    assert.equal(intr.disabled, true);
    assert.equal(el.querySelectorAll('button').find((b) => b.textContent === 'Interrupt turn').disabled, true);
    assert.match(el.querySelector('.dw-meta').textContent, /no caps/);
  });

  test('interrupt posts the exact body', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [] } : []) });
    const { el } = mounted([agent()], 'a1', api);
    await tick();
    el.querySelectorAll('button').find((b) => b.textContent === 'Interrupt turn').dispatchEvent(new Event('click'));
    await tick();
    assert.deepEqual(api.calls.post, [['/api/interrupt', { id: 'a1' }]]);
  });

  test('send posts queue/steer/interrupt modes with the exact body', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [] } : []) });
    const { el } = mounted([agent()], 'a1', api);
    await tick();
    const box = el.querySelector('#dw-msg');
    const radios = el.querySelectorAll('input[type="radio"]');
    const form = el.querySelector('.dw-send');
    box.value = 'keep going';
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(api.calls.post[0], ['/api/send', { id: 'a1', msg: 'keep going', mode: 'queue' }]);
    assert.equal(box.value, '', 'cleared after send');
    box.value = 'nudge';
    radios.find((r) => r.value === 'steer').checked = true;
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(api.calls.post[1], ['/api/send', { id: 'a1', msg: 'nudge', mode: 'steer' }]);
    box.value = 'cut in';
    radios.find((r) => r.value === 'steer').checked = false;
    radios.find((r) => r.value === 'interrupt').checked = true;
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(api.calls.post[2], ['/api/send', { id: 'a1', msg: 'cut in', mode: 'interrupt' }]);
    box.value = '   ';
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(api.calls.post.length, 3, 'blank message is not sent');
  });

  test('stop form flow: inline form, reason required, no prompt(), exact body', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [] } : []) });
    const { el } = mounted([agent()], 'a1', api);
    await tick();
    const stopForm = el.querySelector('.dw-stop');
    assert.equal(stopForm.hidden, true);
    el.querySelectorAll('button').find((b) => b.textContent === 'Stop').dispatchEvent(new Event('click'));
    assert.equal(stopForm.hidden, false);
    stopForm.dispatchEvent(new Event('submit'));
    await tick();
    assert.match(el.querySelector('.dw-err').textContent, /reason is required/);
    assert.equal(api.calls.post.length, 0);
    el.querySelector('#dw-reason').value = 'done here';
    stopForm.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(api.calls.post, [['/api/stop', { id: 'a1', reason: 'done here' }]]);
    assert.equal(stopForm.hidden, true);
  });

  test('turns_full fetched on open and on turn change only', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [{ t0: 1, response: 'r', ok: true }] } : []) });
    const { store, el } = mounted([agent({ turns: 2 })], 'a1', api);
    await tick();
    assert.equal(api.calls.get.filter((p) => p.startsWith('/api/status')).length, 1);
    assert.match(el.querySelector('.dw-turns').textContent, /turn 1: done/);
    store.set(stateFor([agent({ turns: 2, last_text: 'new line' })], 'a1'));
    store.flush();
    await tick();
    assert.equal(api.calls.get.filter((p) => p.startsWith('/api/status')).length, 1, 'same turn count: no refetch');
    assert.match(el.querySelector('.dw-last').textContent, /new line/);
    store.set(stateFor([agent({ turns: 3, last_text: 'new line' })], 'a1'));
    store.flush();
    await tick();
    assert.equal(api.calls.get.filter((p) => p.startsWith('/api/status')).length, 2, 'turn change refetches');
  });

  test('a turn finishing (same turn count, busy -> idle) refetches the turns', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [{ t0: 1, response: 'r', ok: true }] } : []) });
    const { store } = mounted([agent({ turns: 2, status: 'busy' })], 'a1', api);
    await tick();
    const n = () => api.calls.get.filter((p) => p.startsWith('/api/status')).length;
    assert.equal(n(), 1);
    store.set(stateFor([agent({ turns: 2, status: 'idle' })], 'a1'));
    store.flush();
    await tick();
    assert.equal(n(), 2, 'status change at the same turn count refetches');
  });

  test('status polls: one in flight per agent, and a late reply for a previous agent is dropped', async () => {
    const held = [];
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status')
      ? new Promise((res) => held.push([p, res])) : []) });
    const { store, el } = mounted([agent({ turns: 2 }), agent({ id: 'a2', turns: 2 })], 'a1', api);
    await tick();
    const n = () => api.calls.get.filter((p) => p.startsWith('/api/status')).length;
    assert.equal(n(), 1);
    store.set(stateFor([agent({ turns: 3 }), agent({ id: 'a2', turns: 2 })], 'a1'));   // turn change while the first poll is out
    store.flush();
    await tick();
    assert.equal(n(), 1, 'no overlapping poll for the same agent');
    held[0][1]({ turns_full: [{ t0: 1, response: 'one', ok: true }] });
    await tick();
    assert.equal(n(), 2, 'the skipped poll re-runs once the first lands');
    store.set(stateFor([agent(), agent({ id: 'a2', turns: 2 })], 'a2'));               // switch away with a poll still out
    store.flush();
    await tick();
    assert.equal(n(), 3);
    held[2][1]({ turns_full: [{ t0: 9, response: 'from a2', ok: true }] });
    await tick();
    held[1][1]({ turns_full: [{ t0: 1, response: 'STALE a1', ok: true }, { t0: 2, response: 'STALE', ok: true }] });
    await tick();
    const txt = el.querySelector('.dw-turns').textContent;
    assert.match(txt, /turn 1/);
    assert.doesNotMatch(txt, /turn 2/, 'older a1 reply must not overwrite a2');
  });

  test('switching agents resets the stream and refetches with since=0', async () => {
    const api = fakeApi({ get: (p) => (p.startsWith('/api/status') ? { turns_full: [] } : [ev(1), ev(2)]) });
    const { store, el } = mounted([agent(), agent({ id: 'a2', goal: 'other' })], 'a1', api);
    await tick();
    assert.equal(el.querySelectorAll('.dw-line').length, 2);
    store.set(stateFor([agent(), agent({ id: 'a2', goal: 'other' })], 'a2'));
    store.flush();
    await tick();
    const evCalls = api.calls.get.filter((p) => p.startsWith('/api/events'));
    assert.ok(evCalls.some((p) => p.includes('id=a2') && p.includes('since=0')), 'refetch from zero, got ' + evCalls.join(' | '));
    assert.match(el.querySelector('.dw-goal').textContent, /other/);
  });

  test('close button patches the route (no prompt, no navigation hack)', async () => {
    globalThis.location = { hash: '#/fleet?agent=a1' };
    const api = fakeApi({ get: () => [] });
    const { el } = mounted([agent()], 'a1', api);
    await tick();
    el.querySelector('.drawer-close').dispatchEvent(new Event('click'));
    assert.match(globalThis.location.hash, /#\/fleet/);
    assert.doesNotMatch(globalThis.location.hash, /agent/);
    delete globalThis.location;
  });
});
