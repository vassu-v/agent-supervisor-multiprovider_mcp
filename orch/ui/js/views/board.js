// Board view (#/board/<ws>): green announcements lane, pink questions lane,
// collapsed activity, post / ask / answer forms.
//
// Reads the boards slice only (written by core/sync.js) and writes through
// api.post. New-question speech comes from core/sync.js once per id; this
// view never speaks. Keys scope: "board".
import { h, on, text, list } from '../core/h.js';
import { safeId } from '../core/fmt.js';
import { pushScope, bind } from '../core/keys.js';
import { splitPosts, isOpen, postNode, updatePost } from '../components/board-post.js';
import { ANNOUNCE_KINDS, announceBody, askBody, composeForm } from '../components/board-forms.js';

let cur = null;

// Mount the board for the workspace in the current route.
export function mount(el, store, api) {
  unmount();
  const st = { el, store, api, unsubs: [], unbinds: [], popScope: null };
  cur = st;
  const ctx = { api, ws: null, agents: [], byId: new Map() };

  const title = h('h2', { class: 'bd-title mono' }, 'Board');
  const empty = h('p', { class: 'empty', hidden: true }, 'Pick a workspace to see its board.');
  const annCount = h('span', { class: 'c' }, '0');
  const qCount = h('span', { class: 'c' }, '0');
  const annPosts = h('div', { class: 'lane-posts' });
  const qPosts = h('div', { class: 'lane-posts' });
  const laneAnn = h('section', { class: 'lane-ann', 'aria-label': 'Announcements' },
    h('h3', { class: 'lane-h' },
      h('span', { class: 'sw', 'aria-hidden': 'true' }), 'Announcements ', annCount),
    annPosts);
  const laneQ = h('section', { class: 'lane-q', 'aria-label': 'Questions' },
    h('h3', { class: 'lane-h' },
      h('span', { class: 'sw', 'aria-hidden': 'true' }), 'Questions ', qCount),
    qPosts);
  const wrap = h('div', { class: 'bd-wrap' }, laneAnn, laneQ);
  const actN = h('span', { class: 'n' }, '0');
  const actPosts = h('div', { class: 'act-posts' });
  const act = h('details', { class: 'act' }, h('summary', null, 'Activity (', actN, ')'), actPosts);
  const forms = h('div', { class: 'bd-forms' });
  const root = h('section', { class: 'board bd', 'aria-label': 'Board' }, title, empty, wrap, act, forms);
  el.append(root);

  // Hover or keyboard focus on a post lights its sender and target.
  function highlight(ev, add) {
    let n = ev.target;
    while (n && n !== root) {
      if (n.tagName === 'ARTICLE' && n.classList.contains('post')) break;
      n = n.parentNode;
    }
    if (!n || n === root) return;
    const from = n.getAttribute('data-from');
    const to = n.getAttribute('data-to');
    for (const art of root.querySelectorAll('.post')) {
      const f = art.getAttribute('data-from');
      const t = art.getAttribute('data-to');
      const hit = art === n
        || (from && (f === from || t === from))
        || (to && (f === to || t === to));
      art.classList.toggle('hl', add ? !!hit : false);
    }
  }
  on(root, 'mouseover', (ev) => highlight(ev, true));
  on(root, 'mouseout', (ev) => highlight(ev, false));
  on(root, 'focusin', (ev) => highlight(ev, true));
  on(root, 'focusout', (ev) => highlight(ev, false));

  function step(d) {
    const arts = root.querySelectorAll('.post');
    if (!arts.length) return;
    const active = root.ownerDocument.activeElement;
    let i = -1;
    for (let k = 0; k < arts.length; k++) {
      if (arts[k] === active) { i = k; break; }
    }
    const at = i < 0 ? (d > 0 ? 0 : arts.length - 1) : Math.max(0, Math.min(arts.length - 1, i + d));
    arts[at].focus();
  }

  let formsWs = null;
  function render() {
    const s = store.get();
    const route = s.ui && s.ui.route ? s.ui.route.params : {};
    const id = safeId(route.ws);
    if (!id) {
      empty.hidden = false;
      text(title, 'Board');
      title.removeAttribute('title');
      wrap.hidden = true;
      act.hidden = true;
      forms.hidden = true;
      return;
    }
    empty.hidden = true;
    wrap.hidden = false;
    act.hidden = false;
    forms.hidden = false;
    ctx.ws = id;
    const board = (s.boards || {})[id] || { posts: [] };
    ctx.agents = s.agents || [];
    ctx.byId = new Map((board.posts || []).map((p) => [Number(p.id), p]));
    const parts = splitPosts(board.posts || []);
    const w = (s.workspaces || []).find((x) => x.id === id);
    text(title, 'Board · ' + ((w && w.name) || id));
    title.setAttribute('title', id);
    text(annCount, String(parts.ann.length));
    const openN = parts.questions.filter(isOpen).length;
    text(qCount, openN ? openN + ' open' : '0 open');
    text(actN, String(parts.activity.length));
    const mk = (p) => postNode(p, ctx);
    const up = (node, p) => updatePost(node, p, ctx);
    list(annPosts, parts.ann, (p) => p.id, mk, up);
    list(qPosts, parts.questions, (p) => p.id, mk, up);
    list(actPosts, parts.activity, (p) => p.id, mk, up);
    if (formsWs !== id) {
      formsWs = id;
      forms.replaceChildren();
      forms.append(
        composeForm(ctx, 'Announce', ANNOUNCE_KINDS, '/api/announce', 'Announce',
          (kind, v) => announceBody(id, kind, v)),
        composeForm(ctx, 'Ask', null, '/api/ask', 'Ask', (_k, v) => askBody(id, v)));
    }
  }

  st.unsubs.push(store.subscribe((s) => s.boards, render));
  st.unsubs.push(store.subscribe((s) => s.agents, render));
  st.unsubs.push(store.subscribe((s) => s.workspaces, render));
  st.unsubs.push(store.subscribe((s) => s.ui.route, render));
  st.popScope = pushScope('board');
  st.unbinds.push(bind('j', () => step(1), 'board', 'Next post'));
  st.unbinds.push(bind('k', () => step(-1), 'board', 'Previous post'));
  render();
  return () => unmount();
}

// Free subscriptions, keys and scope; clear the element.
export function unmount() {
  if (!cur) return;
  for (const u of cur.unsubs) {
    try { u(); } catch { /* ignore */ }
  }
  for (const b of cur.unbinds) {
    try { b(); } catch { /* ignore */ }
  }
  if (cur.popScope) {
    try { cur.popScope(); } catch { /* ignore */ }
  }
  try { cur.el.replaceChildren(); } catch { /* ignore */ }
  cur = null;
}
