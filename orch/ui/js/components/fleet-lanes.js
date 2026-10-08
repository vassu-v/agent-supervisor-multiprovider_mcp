// Per-yard board rail: green Announcements lane and pink Questions lane.
// Posts are keyed lists patched in place; all text goes through h()/text().

import { h, on, text, list } from '../core/h.js';
import { ago } from '../core/fmt.js';

const senderOf = (p) => String(p.sender || '').replace(/^agent:/, '');
const show = (el, yes) => (yes ? el.removeAttribute('hidden') : el.setAttribute('hidden', ''));

function createPost(p, now, hooks, y, q) {
  const kind = h('span', { class: 'kind' }, p.kind || 'info');
  const snd = h('span', { class: 'snd' }, senderOf(p));
  const tm = h('span', null, ago(p.ts, now));
  const stale = h('span', { class: 'stale', hidden: true }, 'stale');
  const tx = h('p', { class: 'tx' }, p.text || '');
  const kids = [h('div', { class: 'h' }, kind, snd, tm, q ? stale : null), tx];
  if (q) kids.push(on(h('button', { type: 'button', class: 'lk', 'aria-label': `Answer ${senderOf(p)} on the board` }, 'Answer'),
    'click', () => hooks.board(y.id)));
  const art = h('article', { class: 'post', 'data-from': senderOf(p) }, ...kids);
  art._p = { kind, snd, tm, tx, stale };
  return art;
}

function updatePost(art, p, now) {
  const r = art._p;
  text(r.kind, p.kind || 'info');
  text(r.snd, senderOf(p));
  text(r.tm, ago(p.ts, now));
  text(r.tx, p.text || '');
  show(r.stale, p.stale === true);
}

function lane(cls, label) {
  const c = h('span', { class: 'c' }, '0');
  const posts = h('div', { class: 'posts' });
  const none = h('p', { class: 'muted', hidden: true }, 'No open questions');
  const el = h('section', { class: cls, 'aria-label': label },
    h('h3', { class: 'lane-h' }, h('span', { class: 'sw', 'aria-hidden': 'true' }), label, c), posts, cls === 'lane-q' ? none : null);
  return { el, c, posts, none };
}

export function createBoard(y, hooks) {
  const a = lane('lane-ann', 'Announcements');
  const q = lane('lane-q', 'Questions');
  const aside = h('aside', { class: 'board', 'aria-label': 'Board for ' + y.name }, a.el, q.el);
  on(aside, 'pointerover', (ev) => {
    let n = ev.target;
    while (n && n !== aside && !(n.getAttribute && n.getAttribute('data-from'))) n = n.parentNode;
    hooks.hl(n && n !== aside ? n.getAttribute('data-from') : null);
  });
  on(aside, 'pointerout', () => hooks.hl(null));
  aside._l = { a, q };
  return aside;
}

export function updateBoard(aside, y, now, hooks) {
  const { a, q } = aside._l;
  list(a.posts, y.board, (p) => p.id, (p) => createPost(p, now, hooks, y, false), (el, p) => updatePost(el, p, now));
  list(q.posts, y.qPosts, (p) => p.id, (p) => createPost(p, now, hooks, y, true), (el, p) => updatePost(el, p, now));
  text(a.c, String(y.board.length));
  text(q.c, `${y.questions} open`);
  show(q.none, y.qPosts.length === 0);
}
