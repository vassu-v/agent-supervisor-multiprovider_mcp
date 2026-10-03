// B2 escalations: countdown math, stacking, resolve bodies, text-node
// safety and the pending-plus-history view. DOM via the shim.
// Run: node --test tests/ui/
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { install, serialize, Event } from './shim.mjs';
import { createStore } from '../../orch/ui/js/core/store.js';
import {
  countdownLabel, fuseFrac, fuseVar, heldLabel, heldS,
  remainingS, sortPending, totalLabel, totalS,
} from '../../orch/ui/js/components/esc-time.js';
import { createSignal, tickSignal, updateSignal } from '../../orch/ui/js/components/esc-card.js';
import { createHistRow } from '../../orch/ui/js/components/esc-row.js';
import { mount, mountBar, unmount, unmountBar } from '../../orch/ui/js/views/escalations.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let doc;
beforeEach(() => {
  unmountBar();
  unmount();
  doc = install();
});

const E = (over = {}) => ({
  id: 'e1', agent: 'a5', why: "risky action: 'git push origin main'",
  tool: 'Bash', input: 'git push origin main',
  ts: 1000, state: 'pending', workspace: 'w-api', deadline: 1600, ...over,
});

function fakeApi(posts = [], gets = {}) {
  return {
    posts,
    get: async (path) => (path in gets ? gets[path] : []),
    post: async (path, body) => { posts.push([path, body]); return { ok: true }; },
  };
}

// ------------------------------------------------------------------ pure math
describe('countdown math', () => {
  test('remaining clamps at zero and needs a deadline', () => {
    assert.equal(remainingS(E(), 1060), 540);
    assert.equal(remainingS(E(), 2000), 0);
    assert.equal(remainingS(E({ deadline: null }), 1000), null);
    assert.equal(remainingS({}, 1000), null);
  });
  test('held clamps future stamps at zero', () => {
    assert.equal(heldS(E(), 1072), 72);
    assert.equal(heldS(E(), 500), 0);
    assert.equal(heldS(E({ ts: null }), 1000), null);
  });
  test('total needs deadline after raised', () => {
    assert.equal(totalS(E()), 600);
    assert.equal(totalS(E({ deadline: 1000 })), null);
    assert.equal(totalS(E({ deadline: null })), null);
  });
  test('fuse fraction drains then empties', () => {
    assert.equal(fuseFrac(E(), 1000), 1);
    assert.equal(fuseFrac(E(), 1300), 0.5);
    assert.equal(fuseFrac(E(), 2000), 0);
    assert.equal(fuseFrac(E({ deadline: null, ts: 1000 }), 1200), 1);
    assert.equal(fuseFrac(E({ deadline: null, ts: null }), 1200), 1);
  });
  test('labels match the mockup wording', () => {
    assert.equal(countdownLabel(E({ deadline: 1528 }), 1000), 'auto-stop in 8:48');
    assert.equal(countdownLabel(E({ deadline: null }), 1000), 'no deadline');
    assert.equal(heldLabel(E(), 1072), 'held 1m12s');
    assert.equal(totalLabel(E()), 'of 10:00');
    assert.equal(totalLabel(E({ deadline: null })), '');
    assert.equal(fuseVar(E(), 1300), '50.0%');
  });
  test('stacking: pending only, earliest deadline first', () => {
    const rows = [
      E({ id: 'late', deadline: 1900 }),
      E({ id: 'done', state: 'deny', deadline: 1100 }),
      E({ id: 'early', deadline: 1200 }),
      E({ id: 'nodead', deadline: null, ts: 900 }),
    ];
    assert.deepEqual(sortPending(rows).map((e) => e.id), ['early', 'late', 'nodead']);
    assert.notEqual(sortPending(rows), rows);
  });
});

// ------------------------------------------------------------------ bar stacking
describe('mountBar', () => {
  test('stacks pending cards earliest-deadline-first with keyed identity', async () => {
    const store = createStore({ escalations: [E({ id: 'late', deadline: 1900 }), E({ id: 'early', deadline: 1200 })] });
    const bar = doc.body.appendChild(doc.createElement('section'));
    const api = fakeApi();
    mountBar(bar, store, api);
    store.flush();
    assert.equal(bar.children.length, 2);
    assert.equal(bar.children[0].getAttribute('data-id'), 'early');
    const first = bar.children[0];
    const second = bar.children[1];
    // one row changes: only that row is patched, nodes are kept
    store.set({ escalations: [E({ id: 'late', deadline: 1900, why: 'edited' }), E({ id: 'early', deadline: 1200 })] });
    store.flush();
    assert.equal(bar.children[1], second);
    assert.equal(bar.children[0], first);
    assert.match(bar.children[1].textContent, /edited/);
    // resolving removes the card
    store.set({ escalations: [E({ id: 'early', deadline: 1200 })] });
    store.flush();
    assert.equal(bar.children.length, 1);
    assert.equal(bar.children[0].getAttribute('data-id'), 'early');
    unmountBar();
  });

  test('first button is focusable so global e lands on it', () => {
    const store = createStore({ escalations: [E()] });
    const bar = doc.body.appendChild(doc.createElement('section'));
    mountBar(bar, store, fakeApi());
    store.flush();
    const btn = bar.querySelector('button');
    assert.ok(btn);
    btn.focus();
    assert.equal(doc.activeElement, btn);
    unmountBar();
  });

  test('countdown ticks without rebuilding the card', () => {
    const api = fakeApi();
    const n = createSignal(E({ deadline: 1528, ts: 1000 }), api);
    doc.body.appendChild(n);
    tickSignal(n, E({ deadline: 1528, ts: 1000 }), 1000);
    const cd = n._esc.cd;
    assert.equal(cd.textContent, 'auto-stop in 8:48');
    assert.equal(n._esc.held.textContent, 'held 0s');
    tickSignal(n, E({ deadline: 1528, ts: 1000 }), 1060);
    assert.equal(cd.textContent, 'auto-stop in 7:48');
    assert.equal(cd.parentNode && n.contains(cd), true);
  });
});

// ------------------------------------------------------------------ resolve
describe('resolve bodies', () => {
  test('Deny posts the exact server body and locks the buttons', async () => {
    const posts = [];
    const store = createStore({ escalations: [E({ id: 'e9' })] });
    const bar = doc.body.appendChild(doc.createElement('section'));
    mountBar(bar, store, fakeApi(posts));
    store.flush();
    const card = bar.children[0];
    const [deny, allow] = [card._esc.deny, card._esc.allow];
    deny.dispatchEvent(new Event('click'));
    assert.equal(deny.disabled, true);
    assert.equal(allow.disabled, true);
    await sleep(10);
    assert.deepEqual(posts, [['/api/resolve', { escalation: 'e9', decision: 'deny', note: '' }]]);
    unmountBar();
  });
  test('Allow once posts decision allow', async () => {
    const posts = [];
    const store = createStore({ escalations: [E({ id: 'e7' })] });
    const bar = doc.body.appendChild(doc.createElement('section'));
    mountBar(bar, store, fakeApi(posts));
    store.flush();
    bar.children[0]._esc.allow.dispatchEvent(new Event('click'));
    await sleep(10);
    assert.deepEqual(posts, [['/api/resolve', { escalation: 'e7', decision: 'allow', note: '' }]]);
    unmountBar();
  });
  test('a failed resolve unlocks and reports the error as text', async () => {
    const store = createStore({ escalations: [E({ id: 'e7' })] });
    const bar = doc.body.appendChild(doc.createElement('section'));
    const api = { get: async () => [], post: async () => ({ error: 'no such pending escalation', status: 400 }) };
    mountBar(bar, store, api);
    store.flush();
    const card = bar.children[0];
    card._esc.deny.dispatchEvent(new Event('click'));
    await sleep(10);
    assert.equal(card._esc.deny.disabled, false);
    assert.equal(card._esc.err.textContent, 'no such pending escalation');
    assert.equal(card._esc.err.getAttribute('role'), 'alert');
    unmountBar();
  });
});

// ------------------------------------------------------------------ text safety
describe('agent text stays text', () => {
  const evil = '<img src=x onerror=alert(1)>';
  test('command markup never becomes an element', () => {
    const n = createSignal(E({ input: evil, why: evil, tool: evil }), fakeApi());
    assert.equal(n.querySelector('img'), null);
    assert.equal(n._esc.cmd.textContent, evil);
    assert.equal(n._esc.cmd.childNodes.length, 1);
    assert.equal(n._esc.cmd.firstChild.nodeType, 3);
    assert.match(serialize(n._esc.cmd), /&lt;img/);
    assert.ok(!serialize(n).includes('<img'));
  });
  test('history rows escape too', () => {
    const n = createHistRow(E({ state: 'deny', input: evil, note: evil }));
    assert.equal(n.querySelector('img'), null);
    assert.match(serialize(n), /&lt;img/);
  });
  test('update path is safe as well', () => {
    const n = createSignal(E(), fakeApi());
    updateSignal(n, E({ input: evil }));
    assert.equal(n.querySelector('img'), null);
    assert.equal(n._esc.cmd.textContent, evil);
  });
});

// ------------------------------------------------------------------ full view
describe('#/escalations view', () => {
  const all = [
    E({ id: 'p1', deadline: 1500 }),
    E({ id: 'p2', deadline: 1400 }),
    E({ id: 'h1', state: 'deny', note: 'nope', resolved_by: 'dashboard', resolved: 1200 }),
    E({ id: 'h2', state: 'allow', note: '', resolved_by: 'dashboard', resolved: 1300 }),
  ];
  test('pending plus resolved history, history fetched by the view', async () => {
    const store = createStore({ escalations: all.filter((e) => e.state === 'pending') });
    let got = null;
    const api = {
      get: async (path) => { got = path; return all; },
      post: fakeApi().post,
    };
    const root = doc.body.appendChild(doc.createElement('div'));
    mount(root, store, api);
    await sleep(10);
    assert.equal(got, '/api/escalations?all=1');
    const pend = root.querySelector('.esc-pending');
    assert.equal(pend.children.length, 2);
    assert.equal(pend.children[0].getAttribute('data-id'), 'p2');
    const hist = root.querySelector('.esc-history');
    assert.equal(hist.children.length, 2);
    assert.match(hist.textContent, /denied/);
    assert.match(hist.textContent, /allowed/);
    assert.match(hist.textContent, /nope/);
    const refresh = root.querySelector('button');
    assert.ok(refresh);
    refresh.focus();
    assert.equal(doc.activeElement, refresh);
    unmount();
  });
  test('empty states read cleanly', async () => {
    const store = createStore({ escalations: [] });
    const api = fakeApi([], { '/api/escalations?all=1': [] });
    const root = doc.body.appendChild(doc.createElement('div'));
    mount(root, store, api);
    await sleep(10);
    assert.match(root.textContent, /No pending escalations/);
    assert.match(root.textContent, /nothing decided yet/);
    unmount();
  });
  test('store pending changes repaint the pending lane', async () => {
    const store = createStore({ escalations: [] });
    const api = fakeApi([], { '/api/escalations?all=1': [] });
    const root = doc.body.appendChild(doc.createElement('div'));
    mount(root, store, api);
    await sleep(10);
    store.set({ escalations: [E({ id: 'n1' })] });
    store.flush();
    await sleep(10);
    assert.equal(root.querySelector('.esc-pending').children.length, 1);
    unmount();
  });
});

// ------------------------------------------------------------------ budgets
describe('budgets and bans', () => {
  test('view source stays small and clean', () => {
    const base = new URL('../../orch/ui/', import.meta.url);
    const files = [
      'js/views/escalations.js',
      'js/components/esc-time.js',
      'js/components/esc-card.js',
      'js/components/esc-row.js',
      'css/esc.css',
    ];
    assert.ok(statSync(new URL('js/views/escalations.js', base)).size <= 8192, 'view <= 8KB');
    const banned = ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'javascript:', 'style=', 'http://', 'https://'];
    for (const f of files) {
      const src = readFileSync(new URL(f, base), 'utf8');
      for (const b of banned) assert.ok(!src.includes(b), `${f} contains ${b}`);
    }
    const view = readFileSync(new URL('js/views/escalations.js', base), 'utf8');
    assert.ok(!view.includes('keys.bind'), 'no single-key allow/deny shortcuts');
    assert.ok(!view.includes('announce('), 'sync.js owns announcements');
  });
});
