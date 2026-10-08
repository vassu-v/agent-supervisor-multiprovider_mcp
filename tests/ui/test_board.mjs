// Board view: lanes, answered collapse, forms, hostile text, hover, keys.
// Run: node --test tests/ui/
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { install, serialize, Event } from './shim.mjs';

import { createStore } from '../../orch/ui/js/core/store.js';
import * as keys from '../../orch/ui/js/core/keys.js';
import {
  mount, unmount,
} from '../../orch/ui/js/views/board.js';
import {
  splitPosts, isOpen, isAnswered, staleLabel,
} from '../../orch/ui/js/components/board-post.js';
import {
  validateText, announceBody, askBody, answerBody, ANNOUNCE_KINDS, MAX_TEXT,
} from '../../orch/ui/js/components/board-forms.js';

const tick = () => new Promise((r) => setTimeout(r, 5));

let doc;
beforeEach(() => {
  doc = install();
  keys.reset();
  unmount();
});

const P = (over = {}) => ({
  id: 1, ts: 1700000000, ws: 'w1', sender: 'agent:a1', sender_kind: 'agent',
  kind: 'info', text: 'hello', paths: [], reply_to: null, status: 'closed',
  answered_by: null, ...over,
});

function setup(posts, agents = [{ id: 'a1', parent: 'lead' }, { id: 'lead', parent: null }]) {
  const calls = [];
  const api = { post: async (path, body) => { calls.push([path, body]); return { ok: true }; } };
  const store = createStore({
    boards: { w1: { posts, last: posts.length, open_questions: posts.filter(isOpen).length } },
    agents,
    ui: { route: { view: 'board', params: { ws: 'w1' } } },
  });
  const el = doc.createElement('div');
  doc.body.appendChild(el);
  mount(el, store, api);
  store.flush();
  return { el, store, api, calls };
}

// ------------------------------------------------------------------ pure logic
describe('splitPosts', () => {
  test('announcements, questions and activity land in separate lanes, newest first', () => {
    const posts = [
      P({ id: 1, kind: 'done' }),
      P({ id: 2, kind: 'question', status: 'open' }),
      P({ id: 3, kind: 'answer', reply_to: 2 }),
      P({ id: 4, kind: 'auto' }),
      P({ id: 5, kind: 'question', status: 'answered', answered_by: 'agent:lead' }),
      P({ id: 6, kind: 'info' }),
    ];
    const { ann, questions, activity } = splitPosts(posts);
    assert.deepEqual(ann.map((p) => p.id), [6, 1]);
    assert.deepEqual(questions.map((p) => p.id), [5, 2]);
    assert.deepEqual(activity.map((p) => p.id), [4, 3]);
  });
  test('answered means any non-open question', () => {
    assert.equal(isAnswered(P({ kind: 'question', status: 'answered' })), true);
    assert.equal(isAnswered(P({ kind: 'question', status: 'closed' })), true);
    assert.equal(isOpen(P({ kind: 'question', status: 'open' })), true);
    assert.equal(isOpen(P({ kind: 'info', status: 'open' })), false);
  });
});

describe('validation and exact server bodies', () => {
  test('validateText mirrors the server limits', () => {
    assert.equal(validateText('  '), 'text is required');
    assert.equal(validateText(''), 'text is required');
    assert.equal(validateText(null), 'text is required');
    assert.equal(validateText('x'.repeat(MAX_TEXT + 1)), 'text is too long (max 500 chars)');
    assert.equal(validateText('x'.repeat(8001)), 'text is too long');
    assert.equal(validateText(' ok '), null);
  });
  test('bodies carry exactly what the server reads', () => {
    assert.deepEqual(announceBody('w1', 'done', '  hi '), { ws: 'w1', kind: 'done', text: 'hi' });
    assert.deepEqual(announceBody('w1', 'bogus', 'hi'), { ws: 'w1', kind: 'info', text: 'hi' });
    assert.deepEqual(askBody('w1', ' q? '), { ws: 'w1', text: 'q?' });
    assert.deepEqual(answerBody('7', ' yes '), { id: 7, text: 'yes' });
    assert.deepEqual(Object.keys(answerBody(7, 'x')).sort(), ['id', 'text']);
  });
  test('stale badge names the asker parent', () => {
    const agents = [{ id: 'a1', parent: 'lead' }, { id: 'lead', parent: null }];
    const q = P({ kind: 'question', status: 'open', stale: true });
    assert.equal(staleLabel(q, agents), 'stale · passed to lead');
    assert.equal(staleLabel(P({ kind: 'question', status: 'open' }), agents), null);
    assert.equal(staleLabel(P({ kind: 'question', status: 'answered', stale: true }), agents), null);
    assert.equal(staleLabel(P({ kind: 'question', status: 'open', stale: true }), []), 'stale');
  });
});

// ------------------------------------------------------------------ view
describe('board view', () => {
  test('lanes split and activity stays collapsed', () => {
    const { el } = setup([
      P({ id: 1, kind: 'done', text: 'shipped' }),
      P({ id: 2, kind: 'question', status: 'open', text: 'which port?' }),
      P({ id: 3, kind: 'answer', reply_to: 2, text: '8080' }),
      P({ id: 4, kind: 'info', text: 'note' }),
    ]);
    assert.equal(el.querySelector('.lane-ann').querySelectorAll('.post').length, 2);
    assert.equal(el.querySelector('.lane-q').querySelectorAll('.post').length, 1);
    const act = el.querySelector('details.act');
    assert.ok(act, 'activity is a collapsed details element');
    assert.equal(act.hasAttribute('open'), false);
    assert.equal(act.querySelectorAll('.post').length, 1);
    assert.match(act.textContent, /Activity/);
  });

  test('answered questions collapse to a dashed line with no answer form', () => {
    const { el } = setup([
      P({ id: 2, kind: 'question', status: 'open', text: 'open q' }),
      P({ id: 5, kind: 'question', status: 'answered', text: 'shut q', answered_by: 'agent:lead' }),
    ]);
    const open = el.querySelector('.lane-q').querySelectorAll('.post')[1];
    const shut = el.querySelector('.lane-q').querySelectorAll('.post')[0];
    assert.equal(open.classList.contains('open'), true);
    assert.ok(open.querySelector('form.ans'), 'open questions keep an answer form');
    assert.equal(shut.classList.contains('answered'), true);
    assert.equal(shut.querySelector('form.ans'), null);
    assert.match(shut.textContent, /answered/);
  });

  test('announce form validates and posts the exact body', async () => {
    const { el, calls } = setup([P({ id: 1, kind: 'info' })]);
    const form = el.querySelectorAll('form.compose')[0];
    const input = form.querySelector('input');
    const err = form.querySelector('.err');
    input.value = '   ';
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(calls.length, 0);
    assert.equal(err.hidden, false);
    input.value = 'x'.repeat(501);
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(calls.length, 0);
    input.value = 'deployed v3';
    form.querySelector('select').value = 'done';
    form.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(calls, [['/api/announce', { ws: 'w1', kind: 'done', text: 'deployed v3' }]]);
    assert.equal(input.value, '');
  });

  test('ask and answer forms post exact bodies', async () => {
    const { el, calls } = setup([P({ id: 7, kind: 'question', status: 'open', text: 'port?' })]);
    const ask = el.querySelectorAll('form.compose')[1];
    ask.querySelector('input').value = 'when?';
    ask.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(calls[0], ['/api/ask', { ws: 'w1', text: 'when?' }]);
    const ans = el.querySelector('form.ans');
    ans.querySelector('input').value = '8080';
    ans.dispatchEvent(new Event('submit'));
    await tick();
    assert.deepEqual(calls[1], ['/api/answer', { id: 7, text: '8080' }]);
  });

  test('hostile text is always a text node', () => {
    const evil = '<img src=x onerror=alert(1)>';
    const { el } = setup([
      P({ id: 1, kind: 'info', text: evil, sender: evil }),
      P({ id: 2, kind: 'question', status: 'open', text: evil + '?', stale: true }),
    ]);
    assert.equal(el.querySelector('img'), null);
    const tx = el.querySelectorAll('.tx');
    for (const t of tx) {
      assert.equal(t.children.length, 0, 'no element children inside post text');
    }
    assert.match(serialize(el), /&lt;img/);
    assert.ok(!serialize(el).includes('<img'));
  });

  test('stale marker shows the parent the question passed to', () => {
    const { el } = setup([P({ id: 2, kind: 'question', status: 'open', stale: true })]);
    const badge = el.querySelector('.stale');
    assert.ok(badge);
    assert.equal(badge.textContent, 'stale · passed to lead');
  });

  test('hover highlights the sender and the target', () => {
    const { el } = setup([
      P({ id: 1, kind: 'done', sender: 'agent:a1', text: 'did x' }),
      P({ id: 2, kind: 'info', sender: 'agent:a1', text: 'more' }),
      P({ id: 3, kind: 'info', sender: 'agent:bob', text: 'other' }),
      P({ id: 4, kind: 'answer', sender: 'agent:bob', reply_to: 5, text: 'yes' }),
      P({ id: 5, kind: 'question', status: 'open', sender: 'agent:a1', text: 'q?' }),
    ]);
    const arts = el.querySelectorAll('.post');
    const first = arts.filter((a) => a.getAttribute('data-pid') === '1')[0];
    first.dispatchEvent(new Event('mouseover'));
    const mine = arts.filter((a) => a.getAttribute('data-from') === 'a1');
    const other = arts.filter((a) => a.getAttribute('data-pid') === '3');
    assert.ok(mine.length >= 2 && mine.every((a) => a.classList.contains('hl')));
    assert.ok(other.every((a) => !a.classList.contains('hl')));
    first.dispatchEvent(new Event('mouseout'));
    assert.ok(arts.every((a) => !a.classList.contains('hl')));
  });

  test('j and k move between posts in the board scope', () => {
    const { el } = setup([P({ id: 1, kind: 'info' }), P({ id: 2, kind: 'info' })]);
    const key = (k) => keys.handle({ key: k, target: doc.body, preventDefault() {} });
    assert.equal(key('j'), true);
    const arts = el.querySelectorAll('.post');
    assert.equal(doc.activeElement, arts[0]);
    assert.equal(key('j'), true);
    assert.equal(key('k'), true);
  });

  test('keyed lists keep node identity across patches', () => {
    const { el, store } = setup([P({ id: 1, kind: 'info', text: 'a' }), P({ id: 2, kind: 'info', text: 'b' })]);
    const before = el.querySelector('.lane-ann').querySelectorAll('.post');
    const s = store.get();
    store.set({ boards: { w1: { posts: [{ ...s.boards.w1.posts[0], text: 'a2' }, s.boards.w1.posts[1]], last: 2, open_questions: 0 } } });
    store.flush();
    const after = el.querySelector('.lane-ann').querySelectorAll('.post');
    assert.equal(after[0], before[0], 'order is kept');
    assert.equal(after[1], before[1], 'the reused node keeps its identity');
    assert.equal(after[1].querySelector('.tx').textContent, 'a2');
  });
});

// ------------------------------------------------------------------ project rules
describe('board hard rules', () => {
  const src = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  const files = {
    'board.js': src('../../orch/ui/js/views/board.js'),
    'board-post.js': src('../../orch/ui/js/components/board-post.js'),
    'board-forms.js': src('../../orch/ui/js/components/board-forms.js'),
    'board.css': src('../../orch/ui/css/board.css'),
  };

  test('no banned markup, script or network strings in the view or its css', () => {
    for (const [name, body] of Object.entries(files)) {
      for (const bad of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write',
        'eval(', 'new Function', 'javascript:', 'http://', 'https://']) {
        assert.ok(!body.includes(bad), `${name} contains ${bad}`);
      }
      assert.ok(!/[oO][nN][a-z]+=/.test(body.replace(/Announcements|Questions/g, '')), `${name} has an on*= attribute`);
      assert.ok(!/style=/.test(body), `${name} has a style= attribute`);
      assert.ok(!/fetch\(|XMLHttpRequest|WebSocket|EventSource/.test(body), `${name} uses the network directly`);
    }
  });

  test('budget: every board file stays small', () => {
    for (const [name, rel] of [['board.js', '../../orch/ui/js/views/board.js'],
      ['board-post.js', '../../orch/ui/js/components/board-post.js'],
      ['board-forms.js', '../../orch/ui/js/components/board-forms.js'],
      ['board.css', '../../orch/ui/css/board.css']]) {
      const n = statSync(new URL(rel, import.meta.url)).size;
      assert.ok(n <= 8192, `${name} is ${n} bytes`);
    }
  });

  test('every announce kind has a glyph and the lanes use words, not colour alone', () => {
    for (const k of ANNOUNCE_KINDS) assert.ok(k.length > 0);
    const { el } = setup([P({ id: 1, kind: 'blocked', text: 'stuck here' })]);
    const kind = el.querySelector('.kind');
    assert.match(kind.textContent, /blocked/);
    assert.ok(kind.querySelector('svg'));
    assert.ok(el.querySelector('.lane-ann').querySelector('h3').textContent.includes('Announcements'));
    assert.ok(el.querySelector('.lane-q').querySelector('h3').textContent.includes('Questions'));
  });
});

describe('board title', () => {
  test('shows the workspace name, keeps the id in a title attribute', () => {
    const { el, store } = setup([P()]);
    store.set({ workspaces: [{ id: 'w1', name: 'Alpha Service' }] });
    store.flush();
    const t = el.querySelector('.bd-title');
    assert.equal(t.textContent, 'Board · Alpha Service');
    assert.equal(t.getAttribute('title'), 'w1');
  });
});
