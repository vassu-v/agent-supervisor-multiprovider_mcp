// Audit view: pure window/filter helpers plus the mounted table (text-only cells,
// sticky header CSS, filters, keyboard). Run: node --test tests/ui/test_audit.mjs
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { install, serialize, counts, resetCounts, Event } from './shim.mjs';

import { createStore } from '../../orch/ui/js/core/store.js';
import * as audit from '../../orch/ui/js/views/audit.js';

const EVIL = '<img src=x onerror=alert(1)>';
const rows = [
  { ts: 100, kind: 'spawn', by: 'dashboard', agent: 'a1', provider: 'claude', model: 'opus' },
  { ts: 200, kind: 'send', by: 'agent:a1', agent: 'a2', mode: 'queue', text: 'hello' },
  { ts: 150, kind: 'stop', by: 'dashboard', agent: 'a1', reason: 'done here' },
  { ts: 300, kind: 'guard', by: 'agent:a2', agent: 'a2', tool: 'Bash', error: EVIL },
];

let doc;
beforeEach(() => { doc = install(); });

// ------------------------------------------------------------- pure helpers
describe('audit pure helpers', () => {
  test('formatTs labels UTC, blanks missing stamps', () => {
    assert.equal(audit.formatTs(0), '1970-01-01 00:00:00');
    assert.match(audit.formatTs(1700000000), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    assert.equal(audit.formatTs(null), '');
    assert.equal(audit.formatTs('nope'), '');
    assert.equal(audit.formatIso(null), '');
  });
  test('actor prefers by, target picks the acted-on id', () => {
    assert.equal(audit.auditActor(rows[1]), 'agent:a1');
    assert.equal(audit.auditActor({ agent: 'a9' }), 'a9');
    assert.equal(audit.auditActor({}), '');
    assert.equal(audit.auditTarget(rows[0]), 'a1');
    assert.equal(audit.auditTarget({ kind: 'resolve', escalation: 'e1', decision: 'allow' }), 'e1');
    assert.equal(audit.auditTarget({ kind: 'provider_toggle', provider: 'agy' }), 'agy');
    assert.equal(audit.auditTarget({}), '');
  });
  test('detail is a stable sorted line without the column fields', () => {
    const d = audit.auditDetail({ kind: 'send', by: 'x', agent: 'a2', mode: 'queue', text: 'hi' });
    assert.equal(d, 'mode: queue · text: hi');
    const long = audit.auditDetail({ kind: 'k', by: 'b', big: 'x'.repeat(500), z: 1, a: 2 });
    assert.ok(long.length <= 401 + 1, long.length);
    assert.ok(long.includes('a: 2'));
  });
  test('hostile text passes through the helper unchanged (the DOM keeps it inert)', () => {
    assert.ok(audit.auditDetail(rows[3]).includes(EVIL));
  });
  test('sortNewest puts newest first, missing stamps last, ties stable', () => {
    const got = audit.sortNewest(rows);
    assert.deepEqual(got.map((r) => r.ts), [300, 200, 150, 100]);
    const tied = audit.sortNewest([{ ts: 5, kind: 'a' }, { ts: 5, kind: 'b' }, {}]);
    assert.deepEqual(tied.map((r) => r.kind), ['a', 'b', undefined]);
  });
  test('filterAudit matches text everywhere and actor exactly', () => {
    assert.equal(audit.filterAudit(rows, '', '').length, 4);
    assert.deepEqual(audit.filterAudit(rows, ' opus ', '').map((r) => r.kind), ['spawn']);
    assert.deepEqual(audit.filterAudit(rows, 'AGENT:A1', '').map((r) => r.kind), ['send']);
    assert.deepEqual(audit.filterAudit(rows, '', 'dashboard').map((r) => r.kind), ['spawn', 'stop']);
    assert.deepEqual(audit.filterAudit(rows, 'stop dashboard', '').map((r) => r.kind), ['stop']);
    assert.deepEqual(audit.filterAudit(rows, 'stop opus', ''), []);
    assert.deepEqual(audit.filterAudit(rows, EVIL, '').map((r) => r.kind), ['guard']);
  });
  test('prepareAudit windows the newest rows and reports counts', () => {
    const many = Array.from({ length: 250 }, (_, i) => ({ ts: i + 1, kind: 'k' + i, by: 'b' }));
    const p = audit.prepareAudit(many, { limit: 100 });
    assert.equal(p.rows.length, 100);
    assert.equal(p.rows[0].ts, 250);
    assert.deepEqual([p.shown, p.total], [100, 250]);
    const all = audit.prepareAudit(many, { limit: 500 });
    assert.equal(all.shown, 250);
  });
  test('auditKeys are unique and stable when new rows arrive first', () => {
    const a = [{ ts: 1, kind: 'k', by: 'b' }, { ts: 1, kind: 'k', by: 'b' }];
    const k1 = audit.auditKeys(a);
    assert.equal(new Set(k1).size, 2);
    const k2 = audit.auditKeys([{ ts: 2, kind: 'n', by: 'b' }, ...a]);
    assert.deepEqual(k2.slice(1), k1);
  });
  test('view + css stay inside the 8KB budget', () => {
    const js = statSync(new URL('../../orch/ui/js/views/audit.js', import.meta.url)).size;
    const css = statSync(new URL('../../orch/ui/css/audit.css', import.meta.url)).size;
    assert.ok(js + css <= 8192, `audit view is ${js + css} bytes`);
  });
});

// ------------------------------------------------------------------- mounted
function mounted(initial) {
  const store = createStore({ audit: [], ui: { route: { view: 'audit', params: {} } } });
  const el = doc.createElement('div');
  doc.body.appendChild(el);
  const cleanup = audit.mount(el, store, {});
  if (initial) {
    store.set({ audit: initial });
    store.flush();
  }
  store.flush();
  return { store, el, cleanup };
}

describe('audit view', () => {
  test('exports the view contract and renders landmarks with the five columns', () => {
    const { cleanup } = mounted(rows);
    assert.equal(typeof audit.mount, 'function');
    assert.equal(typeof audit.unmount, 'function');
    const table = doc.body.querySelector('table');
    assert.ok(table);
    assert.equal(table.getAttribute('aria-label'), 'Audit log, newest first');
    assert.deepEqual(
      table.querySelectorAll('th').map((th) => th.textContent),
      ['Time', 'Actor', 'Action', 'Target', 'Detail']);
    assert.ok(table.querySelectorAll('th').every((th) => th.getAttribute('scope') === 'col'));
    assert.ok(doc.body.querySelector('section'));
    assert.ok(doc.body.querySelector('input'));
    assert.ok(doc.body.querySelector('select'));
    cleanup();
  });
  test('rows are newest first and a one-row change touches one row', () => {
    const { store, cleanup } = mounted(rows);
    const first = () => doc.body.querySelectorAll('tbody')[0].children.map((tr) => tr.children[2].textContent);
    assert.deepEqual(first(), ['guard', 'send', 'stop', 'spawn']);
    const trs = doc.body.querySelectorAll('tbody')[0].children.slice();
    resetCounts();
    store.set({ audit: [...rows, { ts: 400, kind: 'resolve', by: 'dashboard', escalation: 'e7', decision: 'deny' }] });
    store.flush();
    assert.deepEqual(first(), ['resolve', 'guard', 'send', 'stop', 'spawn']);
    assert.equal(counts.creates, 7, 'one tr plus its six descendants'); // tr + 5 td + time
    assert.equal(doc.body.querySelectorAll('tbody')[0].children[1], trs[0], 'old rows keep identity');
    cleanup();
  });
  test('hostile text renders as text, never as elements', () => {
    const evil = { ts: 1, kind: 'guard<script>', by: EVIL, agent: EVIL, tool: EVIL, error: EVIL };
    const { cleanup } = mounted([evil]);
    const tr = doc.body.querySelectorAll('tbody')[0].children[0];
    assert.equal(tr.querySelector('img'), null);
    assert.equal(tr.querySelector('script'), null);
    assert.ok(!/onerror=alert/.test(serialize(tr.children[0])), 'time cell holds no evil text');
    const html = serialize(tr);
    assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'), 'evil detail stays escaped');
    assert.ok(html.includes('guard&lt;script&gt;'), 'evil action stays escaped');
    for (const td of tr.children.slice(1)) assert.equal(td.children.length, 0, 'data cells hold only text');
    assert.ok(serialize(tr).includes('&lt;img src=x onerror=alert(1)&gt;'));
    cleanup();
  });
  test('text filter narrows rows and the actor select lists actors', () => {
    const { cleanup } = mounted(rows);
    const input = doc.body.querySelector('input');
    const sel = doc.body.querySelector('select');
    assert.deepEqual(sel.children.map((o) => o.textContent), ['All actors', 'agent:a1', 'agent:a2', 'dashboard']);
    input.value = 'stop';
    input.dispatchEvent(new Event('input'));
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 1);
    assert.ok(doc.body.querySelector('p').textContent.includes('1 of 1'));
    input.value = '';
    input.dispatchEvent(new Event('input'));
    sel.value = 'dashboard';
    sel.dispatchEvent(new Event('change'));
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 2);
    sel.value = 'nope';
    sel.dispatchEvent(new Event('change'));
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 4, 'unknown actor falls back to all');
    assert.equal(doc.body.querySelector('.empty').hidden, true);
    input.value = 'zzz-no-match';
    input.dispatchEvent(new Event('input'));
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 0);
    assert.equal(doc.body.querySelector('.empty').hidden, false);
    cleanup();
  });
  test('keyboard: arrows walk rows with one tab stop, j/k move too', () => {
    const { cleanup } = mounted(rows);
    const trs = doc.body.querySelectorAll('tbody')[0].children;
    assert.deepEqual(trs.map((r) => r.getAttribute('tabindex')), ['0', '-1', '-1', '-1']);
    trs[0].focus();
    trs[0].dispatchEvent(new Event('keydown', { key: 'ArrowDown' }));
    assert.equal(doc.activeElement, trs[1]);
    trs[1].dispatchEvent(new Event('keydown', { key: 'End' }));
    assert.equal(doc.activeElement, trs[3]);
    trs[3].dispatchEvent(new Event('keydown', { key: 'ArrowUp' }));
    assert.equal(doc.activeElement, trs[2]);
    trs[2].dispatchEvent(new Event('keydown', { key: 'Home' }));
    assert.equal(doc.activeElement, trs[0]);
    cleanup();
  });
  test('windowing: only the window renders, Show more grows it', () => {
    const many = Array.from({ length: 250 }, (_, i) => ({ ts: i + 1, kind: 'k' + i, by: 'b' }));
    const { cleanup } = mounted(many);
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 100);
    const btn = doc.body.querySelector('button.audit-more') || doc.body.querySelectorAll('button')[0];
    assert.equal(btn.hidden, false);
    assert.match(btn.textContent, /150 remaining/);
    btn.dispatchEvent(new Event('click'));
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 200);
    btn.dispatchEvent(new Event('click'));
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 250);
    assert.equal(btn.hidden, true);
    cleanup();
  });
  test('records without actor or stamp do not break the actor list or keys', () => {
    const { cleanup } = mounted([{ ts: null, kind: 'x' }, { kind: 'y' }, ...rows]);
    assert.deepEqual(
      doc.body.querySelector('select').children.map((o) => o.textContent),
      ['All actors', 'agent:a1', 'agent:a2', 'dashboard']);
    assert.equal(doc.body.querySelectorAll('tbody')[0].children.length, 6);
    cleanup();
  });
  test('unmount cleans up without throwing and empty store shows the empty note', () => {
    const { store, cleanup } = mounted();
    assert.equal(doc.body.querySelector('.empty').hidden, false);
    store.set({ audit: rows });
    store.flush();
    assert.equal(doc.body.querySelector('.empty').hidden, true);
    cleanup();
  });
});
