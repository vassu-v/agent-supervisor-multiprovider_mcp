// Core UI modules: h() security, keyed list(), store batching, router, keys, a11y, fmt, derive.
// Run: node --test tests/ui/
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { install, serialize, counts, resetCounts, Event } from './shim.mjs';

import { h, list, text, on } from '../../orch/ui/js/core/h.js';
import { createStore, memo } from '../../orch/ui/js/core/store.js';
import { parse, format } from '../../orch/ui/js/core/router.js';
import * as keys from '../../orch/ui/js/core/keys.js';
import { announce, trapFocus, roving, focusables } from '../../orch/ui/js/core/a11y.js';
import { ago, duration, clock, tokens, bytes, safeId } from '../../orch/ui/js/core/fmt.js';
import * as derive from '../../orch/ui/js/core/derive.js';

const fixture = (n) => JSON.parse(readFileSync(new URL(`./fixtures/${n}-agents.json`, import.meta.url), 'utf8'));
const tick = () => new Promise((r) => setTimeout(r, 5));

let doc;
beforeEach(() => { doc = install(); });

// ------------------------------------------------------------------ h()
describe('h() security', () => {
  for (const name of ['onclick', 'onerror', 'onClick', 'ONLOAD', 'style', 'srcdoc', 'src', 'action', 'formaction', 'xlink:href']) {
    test(`rejects attribute ${name}`, () => {
      assert.throws(() => h('div', { [name]: 'x' }));
    });
  }
  test('rejects the raw-markup property names', () => {
    const inner = 'inner' + 'HTML';
    const outer = 'outer' + 'HTML';
    assert.throws(() => h('div', { [inner]: '<b>x</b>' }));
    assert.throws(() => h('div', { [outer]: '<b>x</b>' }));
  });
  test('href must be an in-page link', () => {
    const js = 'java' + 'script:alert(1)';
    assert.throws(() => h('a', { href: js }));
    assert.throws(() => h('a', { href: ' #x' }));
    assert.throws(() => h('a', { href: '/api/list' }));
    assert.throws(() => h('a', { href: '//evil.example' }));
    assert.throws(() => h('a', { href: 42 }));
    assert.equal(h('a', { href: '#/fleet' }).getAttribute('href'), '#/fleet');
  });
  test('markup in text stays text', () => {
    const evil = '<img src=x onerror=alert(1)>';
    const el = h('p', null, evil);
    assert.equal(el.childNodes.length, 1);
    assert.equal(el.firstChild.nodeType, 3);
    assert.equal(el.textContent, evil);
    assert.equal(el.querySelector('img'), null);
    assert.equal(serialize(el), '<p>&lt;img src=x onerror=alert(1)&gt;</p>');
    // and through text()
    text(el, evil + '!');
    assert.equal(el.children.length, 0);
    assert.equal(el.textContent, evil + '!');
  });
  test('attribute values are never parsed', () => {
    const el = h('div', { title: '"><script>x</script>', 'data-x': '<b>' });
    assert.equal(el.children.length, 0);
    assert.equal(el.getAttribute('title'), '"><script>x</script>');
  });
  test('rejects non-node objects as children', () => {
    assert.throws(() => h('div', null, { nodeName: 'fake' }));
  });
});

describe('h() construction', () => {
  test('allowed attributes, booleans and numbers', () => {
    const el = h('button', { class: 'b', id: 'x', type: 'button', disabled: true, tabindex: -1, 'aria-pressed': false, 'data-id': 'a1', title: null }, 'Go', 3);
    assert.equal(el.tagName, 'BUTTON');
    assert.equal(el.getAttribute('class'), 'b');
    assert.equal(el.getAttribute('disabled'), '');
    assert.equal(el.getAttribute('tabindex'), '-1');
    assert.equal(el.getAttribute('aria-pressed'), 'false');
    assert.equal(el.hasAttribute('title'), false);
    assert.equal(el.textContent, 'Go3');
    assert.equal(el.childNodes.length, 2);
  });
  test('value and checked are properties too', () => {
    const i = h('input', { type: 'checkbox', checked: true, value: 'v' });
    assert.equal(i.checked, true);
    assert.equal(i.value, 'v');
    const off = h('input', { type: 'checkbox', checked: false });
    assert.equal(off.hasAttribute('checked'), false);
  });
  test('arrays flatten, null/false/true are skipped', () => {
    const el = h('ul', null, [h('li', null, 'a'), [h('li', null, 'b'), null]], false, undefined, true, 'c');
    assert.equal(el.children.length, 2);
    assert.equal(el.textContent, 'abc');
  });
  test('svg tags use the svg namespace and allow geometry attributes', () => {
    const s = h('svg', { viewBox: '0 0 12 12', class: 'i' }, h('use', { href: '#g-agy' }), h('rect', { x: 1, y: 2, width: 3, height: 4 }));
    assert.match(s.namespaceURI, /svg$/);
    assert.equal(s.firstChild.getAttribute('href'), '#g-agy');
    assert.throws(() => h('div', { viewBox: '0 0 1 1' }), 'svg-only attributes are rejected on html');
  });
  test('on() attaches listeners and returns the element', () => {
    let n = 0;
    const b = on(h('button', null, 'x'), 'click', () => n++);
    b.dispatchEvent(new Event('click'));
    assert.equal(n, 1);
  });
  test('text() only writes when changed', () => {
    const el = h('span', null, 'a');
    const node = el.firstChild;
    text(el, 'a');
    assert.equal(el.firstChild, node);
    text(el, null);
    assert.equal(el.textContent, '');
  });
});

// ------------------------------------------------------------------ list()
describe('list() keyed reconciler', () => {
  const create = (it) => h('li', { 'data-k': it.id }, it.label);
  const update = (node, it) => text(node, it.label);
  const ids = (ul) => ul.children.map((c) => c.getAttribute('data-k')).join(',');

  test('creates, keeps identity, updates in place', () => {
    const ul = h('ul');
    const first = list(ul, [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], (x) => x.id, create, update);
    assert.equal(ids(ul), 'a,b');
    const again = list(ul, [{ id: 'a', label: 'A2' }, { id: 'b', label: 'B' }], (x) => x.id, create, update);
    assert.equal(again[0], first[0]);
    assert.equal(again[1], first[1]);
    assert.equal(ul.children[0].textContent, 'A2');
  });

  test('a one-row change touches only that row', () => {
    const ul = h('ul');
    const items = Array.from({ length: 60 }, (_, i) => ({ id: 'a' + i, label: 'x' }));
    list(ul, items, (x) => x.id, create, update);
    resetCounts();
    let updates = 0;
    const changed = items.map((it, i) => (i === 30 ? { ...it, label: 'y' } : it));
    list(ul, changed, (x) => x.id, create, (n, it) => { updates++; update(n, it); });
    assert.equal(counts.inserts, 1 + 0, 'only the text node of the changed row is inserted'); // text() replaces one text node
    assert.equal(counts.creates, 0);
    assert.equal(updates, 60);
  });

  test('moves the minimum number of nodes', () => {
    const ul = h('ul');
    const mk = (s) => s.split('').map((id) => ({ id, label: id }));
    const nodes = list(ul, mk('abcde'), (x) => x.id, create, update);
    resetCounts();
    list(ul, mk('bcdea'), (x) => x.id, create, update);
    assert.equal(ids(ul), 'b,c,d,e,a');
    assert.equal(counts.inserts, 1, 'only "a" moves');
    assert.equal(ul.children[4], nodes[0]);
    resetCounts();
    list(ul, mk('edcba'), (x) => x.id, create, update);
    assert.equal(ids(ul), 'e,d,c,b,a');
    assert.equal(counts.inserts, 3, 'b and a stay, e d c move');
  });

  test('removes stale nodes and adds new ones', () => {
    const ul = h('ul');
    const mk = (s) => s.split('').map((id) => ({ id, label: id }));
    const before = list(ul, mk('abc'), (x) => x.id, create, update);
    const after = list(ul, mk('xcay'), (x) => x.id, create, update);
    assert.equal(ids(ul), 'x,c,a,y');
    assert.equal(after[1], before[2]);
    assert.equal(after[2], before[0]);
    assert.equal(before[1].parentNode, null);
    list(ul, [], (x) => x.id, create, update);
    assert.equal(ul.childNodes.length, 0);
  });

  test('numeric keys and duplicate keys', () => {
    const ul = h('ul');
    list(ul, [{ id: 1, label: 'a' }, { id: 2, label: 'b' }], (x) => x.id, create, update);
    assert.equal(ul.children.length, 2);
    assert.throws(() => list(ul, [{ id: 1 }, { id: 1 }], (x) => x.id, create, update), /duplicate/);
  });

  test('untouched focused node keeps focus; moved focused input gets focus and caret back', () => {
    doc.body.appendChild(h('div'));
    const ul = h('ul');
    doc.body.appendChild(ul);
    const mkInput = (it) => h('li', { 'data-k': it.id }, h('input', { type: 'text', value: it.id }));
    const nodes = list(ul, ['a', 'b', 'c'].map((id) => ({ id })), (x) => x.id, mkInput);
    const inputB = nodes[1].firstChild;
    inputB.focus();
    inputB.setSelectionRange(1, 1);
    list(ul, ['a', 'b', 'c', 'd'].map((id) => ({ id })), (x) => x.id, mkInput);
    assert.equal(doc.activeElement, inputB, 'append does not disturb focus');
    const inputA = nodes[0].firstChild;
    inputA.focus();
    inputA.setSelectionRange(0, 1);
    list(ul, ['b', 'c', 'd', 'a'].map((id) => ({ id })), (x) => x.id, mkInput); // "a" is the one that moves
    assert.equal(doc.activeElement, inputA);
    assert.deepEqual([inputA.selectionStart, inputA.selectionEnd], [0, 1]);
  });
});

// ------------------------------------------------------------------ store
describe('store', () => {
  test('batches set() calls into one callback run', async () => {
    const s = createStore({ a: 1, b: 1 });
    const seen = [];
    s.subscribe((st) => st.a, (v) => seen.push(v));
    s.set({ a: 2 });
    s.set({ a: 3 });
    s.set((st) => ({ a: st.a + 1 }));
    assert.deepEqual(seen, [], 'nothing runs synchronously');
    await tick();
    assert.deepEqual(seen, [4], 'initial run sees the latest value once');
    s.set({ b: 2 });
    await tick();
    assert.deepEqual(seen, [4], 'unrelated change does not call');
    s.set({ a: 5 });
    s.set({ a: 6 });
    await tick();
    assert.deepEqual(seen, [4, 6]);
  });
  test('unsubscribe and flush', () => {
    const s = createStore({ a: 1 });
    let n = 0;
    const off = s.subscribe((st) => st.a, () => n++);
    s.flush();
    assert.equal(n, 1);
    off();
    s.set({ a: 2 });
    s.flush();
    assert.equal(n, 1);
  });
  test('memo keeps identity until inputs change', () => {
    let runs = 0;
    const sel = memo((st) => [st.list], (l) => { runs++; return l.filter((x) => x > 1); });
    const l = [1, 2, 3];
    const a = sel({ list: l, other: 1 });
    const b = sel({ list: l, other: 2 });
    assert.equal(a, b);
    assert.equal(runs, 1);
    sel({ list: [5] });
    assert.equal(runs, 2);
  });
  test('set() keeps state immutable at the top level', () => {
    const s = createStore({ a: { x: 1 }, b: 1 });
    const before = s.get();
    s.set({ b: 2 });
    assert.notEqual(s.get(), before);
    assert.equal(s.get().a, before.a);
  });
});

// ------------------------------------------------------------------ router
describe('router', () => {
  test('defaults to the fleet', () => {
    assert.deepEqual(parse(''), { view: 'fleet', params: {} });
    assert.deepEqual(parse('#t=secret'), { view: 'fleet', params: {} });
    assert.deepEqual(parse('#/nope'), { view: 'fleet', params: {} });
  });
  test('parses views and params', () => {
    assert.deepEqual(parse('#/fleet?ws=all&st=busy,idle&prov=claude&owner=a1&q=fix%20ci&agent=a5'),
      { view: 'fleet', params: { ws: 'all', st: 'busy,idle', prov: 'claude', owner: 'a1', q: 'fix ci', agent: 'a5' } });
    assert.deepEqual(parse('#/timeline?ws=w1&span=1h'), { view: 'timeline', params: { ws: 'w1', span: '1h' } });
    assert.deepEqual(parse('#/board/w-api?agent=a1'), { view: 'board', params: { ws: 'w-api', agent: 'a1' } });
    assert.deepEqual(parse('#/models'), { view: 'models', params: {} });
    assert.deepEqual(parse('#/escalations'), { view: 'escalations', params: {} });
    assert.deepEqual(parse('#/audit'), { view: 'audit', params: {} });
  });
  test('drops invalid ids and values', () => {
    assert.deepEqual(parse('#/fleet?agent=../etc&ws=%3Cimg%3E&span=2d&owner=' + 'a'.repeat(65)), { view: 'fleet', params: {} });
    assert.deepEqual(parse('#/board/bad%20id'), { view: 'fleet', params: {} });
    assert.deepEqual(parse('#/fleet?evil=1&agent=ok_1'), { view: 'fleet', params: { agent: 'ok_1' } });
    assert.deepEqual(parse('#/fleet?q=%E0%A4%A'), { view: 'fleet', params: {} }, 'bad percent-encoding is dropped');
    assert.deepEqual(parse('#/fleet?q=' + 'x'.repeat(201)), { view: 'fleet', params: {} });
  });
  test('format round-trips', () => {
    for (const h0 of ['#/fleet', '#/fleet?ws=w1&q=a%20b&agent=a1', '#/board/w1?agent=a2', '#/timeline?ws=all&span=6h', '#/audit']) {
      const r = parse(h0);
      assert.equal(format(r.view, r.params), h0);
    }
    assert.equal(format('board', {}), '#/fleet', 'a board without a workspace falls back');
    assert.equal(format('fleet', { agent: 'bad id', q: '<x>' }), '#/fleet?q=%3Cx%3E');
  });
});

// ------------------------------------------------------------------ keys
describe('keys', () => {
  beforeEach(() => keys.reset());
  const key = (k, extra = {}) => keys.handle({ key: k, target: doc.body, preventDefault() {}, ...extra });

  test('single keys and g-sequences', () => {
    const hits = [];
    keys.bind('j', () => hits.push('j'));
    keys.bind('g f', () => hits.push('gf'));
    assert.equal(key('j'), true);
    assert.equal(key('g'), false);
    assert.equal(key('f'), true);
    assert.equal(key('f'), false, 'f alone is not bound');
    assert.deepEqual(hits, ['j', 'gf']);
  });
  test('ignores typing and modifiers, but Escape still works', () => {
    const hits = [];
    keys.bind('j', () => hits.push('j'));
    keys.bind('Escape', () => hits.push('esc'));
    const input = h('input', { type: 'text' });
    assert.equal(key('j', { target: input }), false);
    assert.equal(key('j', { ctrlKey: true }), false);
    assert.equal(key('Escape', { target: input }), true);
    assert.equal(key('j', { target: h('input', { type: 'checkbox' }) }), true);
    assert.deepEqual(hits, ['esc', 'j']);
  });
  test('scopes: inactive scopes are ignored, the latest scope wins', () => {
    const hits = [];
    keys.bind('Escape', () => hits.push('global'));
    keys.bind('Escape', () => hits.push('drawer'), 'drawer');
    key('Escape');
    const pop = keys.pushScope('drawer');
    key('Escape');
    pop();
    key('Escape');
    assert.deepEqual(hits, ['global', 'drawer', 'global']);
  });
  test('help lists labelled bindings of active scopes', () => {
    keys.bind('?', () => {}, 'global', 'Help');
    keys.bind('j', () => {}, 'fleet', 'Next');
    keys.bind('x', () => {});
    assert.deepEqual(keys.help().map((b) => b.seq), ['?']);
    keys.pushScope('fleet');
    assert.deepEqual(keys.help().map((b) => b.seq), ['j', '?']);
  });
});

// ------------------------------------------------------------------ a11y
describe('a11y', () => {
  test('announce once per id into the right region', async () => {
    const a = h('div', { id: 'live-assertive' });
    const p = h('div', { id: 'live-polite' });
    doc.body.append(a, p);
    assert.equal(announce('Escalation: a1', 'assertive', 'e1'), true);
    assert.equal(announce('Escalation: a1', 'assertive', 'e1'), false);
    announce('New question', 'polite');
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(a.textContent, 'Escalation: a1');
    assert.equal(p.textContent, 'New question');
  });
  test('trapFocus focuses inside, wraps Tab, and restores focus on release', () => {
    const opener = h('button', null, 'open');
    const first = h('button', null, '1');
    const hiddenBtn = h('div', { hidden: true }, h('button', null, 'h'));
    const last = h('input', { type: 'text' });
    const dlg = h('div', { role: 'dialog', tabindex: -1 }, first, hiddenBtn, last);
    doc.body.append(opener, dlg);
    opener.focus();
    assert.deepEqual(focusables(dlg), [first, last]);
    const release = trapFocus(dlg);
    assert.equal(doc.activeElement, first);
    last.focus();
    const ev = new Event('keydown', { key: 'Tab' });
    last.dispatchEvent(ev);
    assert.equal(ev.defaultPrevented, true);
    assert.equal(doc.activeElement, first);
    const back = new Event('keydown', { key: 'Tab', shiftKey: true });
    first.dispatchEvent(back);
    assert.equal(doc.activeElement, last);
    release();
    assert.equal(doc.activeElement, opener);
  });
  test('roving keeps one tab stop and follows arrows and tree levels', () => {
    const rows = ['a', 'b', 'c'].map((id, i) => h('div', { role: 'row', 'aria-level': i === 0 ? 1 : 2, 'data-id': id }, id));
    const grid = h('div', { role: 'treegrid' }, rows);
    doc.body.appendChild(grid);
    const toggled = [];
    const r = roving(grid, { onToggle: (row, open) => toggled.push([row.getAttribute('data-id'), open]) });
    assert.deepEqual(rows.map((x) => x.getAttribute('tabindex')), ['0', '-1', '-1']);
    const press = (k) => { const ev = new Event('keydown', { key: k }); doc.activeElement.dispatchEvent(ev); return ev; };
    rows[0].focus();
    press('ArrowDown');
    assert.equal(doc.activeElement, rows[1]);
    assert.deepEqual(rows.map((x) => x.getAttribute('tabindex')), ['-1', '0', '-1']);
    press('End');
    assert.equal(doc.activeElement, rows[2]);
    press('ArrowLeft'); // leaf: go to parent
    assert.equal(doc.activeElement, rows[0]);
    rows[0].setAttribute('aria-expanded', 'true');
    press('ArrowLeft');
    assert.deepEqual(toggled, [['a', false]]);
    rows[1].remove();
    r.refresh();
    assert.equal(grid.querySelectorAll('[tabindex="0"]').length, 1);
    r.destroy();
  });
});

// ------------------------------------------------------------------ fmt
describe('fmt', () => {
  test('ago', () => {
    assert.equal(ago(100, 102), 'now');
    assert.equal(ago(100, 145), '45s');
    assert.equal(ago(0, 3 * 60 + 5), '3m');
    assert.equal(ago(0, 7200), '2h');
    assert.equal(ago(0, 86400 * 5), '5d');
    assert.equal(ago(null, 5), '');
    assert.equal(ago(100_000, 160_000 * 1000 / 1000), '16h');
    assert.equal(ago(1_700_000_000_000, 1_700_000_060), '1m', 'ms timestamps are accepted');
  });
  test('duration and clock', () => {
    assert.equal(duration(12), '12s');
    assert.equal(duration(72), '1m12s');
    assert.equal(duration(240), '4m');
    assert.equal(duration(45 * 60), '45m');
    assert.equal(duration(2 * 3600 + 5 * 60), '2h05m');
    assert.equal(duration(3 * 86400 + 4 * 3600), '3d4h');
    assert.equal(duration(-1), '');
    assert.equal(clock(528), '8:48');
    assert.equal(clock(-5), '0:00');
  });
  test('tokens and bytes', () => {
    assert.equal(tokens(950), '950');
    assert.equal(tokens(1234), '1.2k');
    assert.equal(tokens(118_000), '118k');
    assert.equal(tokens(3_400_000), '3.4M');
    assert.equal(tokens(undefined), '0');
    assert.equal(bytes(512), '512 B');
    assert.equal(bytes(1229), '1.2 KB');
    assert.equal(bytes(34 * 1024 * 1024), '34 MB');
  });
  test('safeId', () => {
    assert.equal(safeId('a-1_B'), 'a-1_B');
    assert.equal(safeId('a b'), null);
    assert.equal(safeId('<x>'), null);
    assert.equal(safeId(5), null);
    assert.equal(safeId('x'.repeat(65)), null);
  });
});

// ------------------------------------------------------------------ derive
describe('derive', () => {
  const now = 10_000;
  const A = (id, status, extra = {}) => ({ id, status, provider: 'claude', usage: { input: 10, output: 5, cache_read: 99 }, workspace: 'w1', ...extra });

  test('stuck thresholds', () => {
    assert.equal(derive.stuck(A('a', 'busy', { last_event: { ts: now - 60 } }), now), null);
    assert.equal(derive.stuck(A('a', 'busy', { last_event: { ts: now - 120 } }), now), 'quiet');
    assert.equal(derive.stuck(A('a', 'busy', { last_event: { ts: now - 600 } }), now), 'stuck?');
    assert.equal(derive.stuck(A('a', 'busy', { turn_t0: now - 700 }), now), 'stuck?', 'falls back to turn_t0');
    assert.equal(derive.stuck(A('a', 'idle', { last_event: { ts: 0 } }), now), null);
    assert.equal(derive.stuck(A('a', 'busy'), now), null, 'no timestamps: no verdict');
  });

  test('attention order: escalation > stuck > blocked > busy > idle > dead', () => {
    const rows = [
      A('dead', 'dead'),
      A('idle', 'idle'),
      A('busy', 'busy', { last_event: { ts: now - 10 } }),
      A('blocked', 'idle'),
      A('stuck', 'busy', { last_event: { ts: now - 900 } }),
      A('esc', 'busy', { pending_escalation: 'e1' }),
      A('esc2', 'idle'),
    ];
    const ctx = { now, blocked: new Set(['blocked']), escalated: derive.escalatedAgents([{ agent: 'esc2', state: 'pending' }, { agent: 'idle', state: 'allowed' }]) };
    assert.deepEqual(derive.attention(rows, ctx).map((a) => a.id), ['esc', 'esc2', 'stuck', 'blocked', 'busy', 'idle', 'dead']);
    assert.notEqual(derive.attention(rows, ctx), rows, 'returns a copy');
  });

  test('blockedAgents from board posts', () => {
    const posts = [
      { id: 1, sender: 'agent:a1', kind: 'blocked', text: '' },
      { id: 2, sender: 'agent:a2', kind: 'blocked', text: '' },
      { id: 3, sender: 'agent:a2', kind: 'done', text: '' },
      { id: 4, sender: 'a3', sender_kind: 'agent', kind: 'question', status: 'open' },
      { id: 5, sender: 'dashboard', sender_kind: 'human', kind: 'question', status: 'open' },
      { id: 6, sender: 'agent:a4', kind: 'question', status: 'answered' },
    ];
    assert.deepEqual([...derive.blockedAgents(posts)].sort(), ['a1', 'a3']);
  });

  test('rollup and totals', () => {
    const rows = [A('a', 'busy'), A('b', 'idle'), A('c', 'dead'), A('d', 'starting', { workspace: 'w2' })];
    const r = derive.rollup(rows, { w1: 2, w3: 1 });
    assert.deepEqual(r.w1, { agents: 3, live: 2, busy: 1, idle: 1, dead: 1, tokens: 45, questions: 2 });
    assert.deepEqual(r.w2, { agents: 1, live: 1, busy: 1, idle: 0, dead: 0, tokens: 15, questions: 0 });
    assert.equal(r.w3.questions, 1);
    assert.deepEqual(derive.totals(rows), { agents: 4, live: 3, busy: 2, idle: 1, dead: 1, tokens: 60 });
  });

  test('filter', () => {
    const rows = [A('a1', 'busy', { goal: 'Fix CI', owner: 'dashboard' }), A('a2', 'idle', { provider: 'agy', paths: ['src/x.py'] }), A('a3', 'dead')];
    assert.equal(derive.filter(rows, {}), rows);
    assert.deepEqual(derive.filter(rows, { st: 'busy,idle' }).map((a) => a.id), ['a1', 'a2']);
    assert.deepEqual(derive.filter(rows, { st: 'live' }).map((a) => a.id), ['a1', 'a2']);
    assert.deepEqual(derive.filter(rows, { prov: 'agy' }).map((a) => a.id), ['a2']);
    assert.deepEqual(derive.filter(rows, { owner: 'dashboard' }).map((a) => a.id), ['a1']);
    assert.deepEqual(derive.filter(rows, { q: 'fix ci' }).map((a) => a.id), ['a1']);
    assert.deepEqual(derive.filter(rows, { q: 'X.PY' }).map((a) => a.id), ['a2']);
  });

  test('byWorkspace keeps order and includes empty listed workspaces', () => {
    const rows = [A('a', 'busy', { workspace: 'w2', workspace_name: 'two' }), A('b', 'idle'), A('c', 'idle', { workspace: 'w2' }), A('d', 'idle', { workspace: null })];
    const g = derive.byWorkspace(rows, [{ id: 'w1', name: 'one' }, { id: 'w9', name: 'nine' }]);
    assert.deepEqual(g.map((x) => [x.id, x.name, x.agents.map((a) => a.id).join('')]), [['w1', 'one', 'b'], ['w9', 'nine', ''], ['w2', 'two', 'ac'], ['', 'no workspace', 'd']]);
  });

  test('tree: depth, last child, orphans become roots, cycles do not loop', () => {
    const rows = [
      A('lead', 'busy'), A('db', 'dead', { parent: 'lead' }), A('api', 'busy', { parent: 'lead' }),
      A('tests', 'idle', { parent: 'api' }), A('orphan', 'idle', { parent: 'gone' }),
      A('x', 'idle', { parent: 'y' }), A('y', 'idle', { parent: 'x' }),
    ];
    const t = derive.tree(rows);
    assert.deepEqual(t.map((n) => `${n.agent.id}:${n.depth}:${n.last ? 'L' : '-'}`), ['lead:0:-', 'db:1:-', 'api:1:L', 'tests:2:L', 'orphan:0:L']);
    assert.deepEqual(t[3].ancestorsLast, [false, true]);
    assert.equal(t[0].hasChildren, true);
  });

  test('works on the fixtures', () => {
    for (const n of [1, 10, 60]) {
      let f;
      try { f = fixture(n); } catch { continue; } // fixtures belong to E2; tolerate their absence
      const rows = f.list;
      const t = derive.tree(rows);
      assert.equal(t.length, rows.length, `${n}: every agent appears once in the tree`);
      for (const node of t) if (node.agent.depth !== undefined) assert.equal(node.depth, node.agent.depth, `${n}: depth matches the server`);
      const ctx = { now: Math.max(...rows.map((a) => a.created)) + 60, escalated: derive.escalatedAgents(f.escalations) };
      const ordered = derive.attention(rows, ctx);
      const ranks = ordered.map((a) => derive.attentionRank(a, ctx));
      assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b), `${n}: ranks ascend`);
      const r = derive.rollup(rows);
      assert.equal(Object.values(r).reduce((s, x) => s + x.agents, 0), rows.length);
    }
  });
});

// ------------------------------------------------------------------ performance smoke
test('a 60-row list patch with one change is fast', () => {
  const ul = h('div');
  const items = Array.from({ length: 60 }, (_, i) => ({ id: 'a' + i, v: 0 }));
  const create = (it) => h('div', { class: 'row' }, h('span', { class: 'aid' }, it.id), h('span', { class: 'v' }, String(it.v)));
  const update = (n, it) => text(n.lastChild, it.v);
  list(ul, items, (x) => x.id, create, update);
  const t0 = performance.now();
  for (let k = 0; k < 50; k++) list(ul, items.map((it, i) => (i === k % 60 ? { ...it, v: k } : it)), (x) => x.id, create, update);
  const per = (performance.now() - t0) / 50;
  assert.ok(per < 8, `patch took ${per.toFixed(2)} ms`);
});
