// Board posts: lane split, stale marker, post nodes + keyed updates.
// All post text becomes text nodes. ctx: {api, ws, agents, byId}.
import { h, text } from '../core/h.js';
import { ago } from '../core/fmt.js';
import { senderAgent } from '../core/derive.js';
import { answerForm } from './board-forms.js';

const GLYPH = {
  started: 'k-started', done: 'k-done', changed: 'k-info', blocked: 's-wait',
  info: 'k-info', handoff: 'k-up', question: 'k-q', answer: 'k-q', auto: 'k-info',
};

export function isQuestion(p) { return !!p && p.kind === 'question'; }
export function isOpen(p) { return isQuestion(p) && p.status === 'open'; }
export function isAnswered(p) { return isQuestion(p) && p.status !== 'open'; }
export function isActivity(p) { return !!p && (p.kind === 'answer' || p.kind === 'auto'); }
export function isAnnounce(p) { return !!p && !isQuestion(p) && !isActivity(p); }

// Split posts into lanes, each newest-first.
export function splitPosts(posts) {
  const ann = [];
  const questions = [];
  const activity = [];
  for (const p of posts || []) {
    if (isQuestion(p)) questions.push(p);
    else if (isActivity(p)) activity.push(p);
    else ann.push(p);
  }
  ann.reverse();
  questions.reverse();
  activity.reverse();
  return { ann, questions, activity };
}

// The asker parent agent id, for the stale marker. Null when unknown.
export function staleParent(p, agents) {
  const asker = senderAgent(p || {});
  if (!asker) return null;
  const rec = (agents || []).find((a) => a && a.id === asker);
  return (rec && rec.parent) || null;
}

// Stale badge words for an open question, or null when fresh.
export function staleLabel(p, agents) {
  if (!p || p.stale !== true || !isOpen(p)) return null;
  const par = staleParent(p, agents);
  return par ? 'stale · passed to ' + par : 'stale';
}

function kindWord(p) {
  return isAnswered(p) ? 'answered' : String((p && p.kind) || 'info');
}

export function targetOf(p, byId) {
  if (!p || p.reply_to === null || p.reply_to === undefined) return '';
  const q = byId.get(Number(p.reply_to));
  if (!q) return '';
  return senderAgent(q) || String(q.sender || '');
}

export function postNode(p, ctx) {
  const open = isOpen(p);
  const art = h('article', {
    class: 'post' + (open ? ' open' : '') + (isAnswered(p) ? ' answered' : ''),
    tabindex: '-1', 'data-pid': String(p.id),
  });
  const from = senderAgent(p) || String(p.sender || '');
  art.setAttribute('data-from', from);
  const to = targetOf(p, ctx.byId);
  if (to) art.setAttribute('data-to', to);
  if (from || to) art.setAttribute('title', 'From ' + from + (to ? ' to ' + to : ''));
  const head = h('div', { class: 'h' },
    h('span', { class: 'kind' },
      h('svg', { class: 'i', 'aria-hidden': 'true', focusable: 'false' },
        h('use', { href: '#' + (GLYPH[p.kind] || 'k-info') })),
      ' ' + kindWord(p)),
    h('span', { class: 'snd' }, String(p.sender || '')),
    h('span', { class: 'ago' }, ago(p.ts)));
  const stale = staleLabel(p, ctx.agents);
  if (stale) head.append(h('span', { class: 'stale' }, stale));
  art.append(head);
  art.append(h('p', { class: 'tx' }, String(p.text || '')));
  if (Array.isArray(p.paths) && p.paths.length) {
    const ft = h('div', { class: 'ft' });
    for (const one of p.paths.slice(0, 3)) ft.append(h('span', { class: 'pt' }, String(one)));
    art.append(ft);
  }
  if (isAnswered(p) && p.answered_by) {
    art.append(h('div', { class: 'ft' },
      h('span', { class: 'wk' }, 'answered by ' + String(p.answered_by))));
  }
  if (open) art.append(answerForm(ctx, p.id));
  return art;
}

// Patch a reused node in place (keeps focus, caret, scroll).
export function updatePost(node, p, ctx) {
  node.classList.toggle('open', isOpen(p));
  node.classList.toggle('answered', isAnswered(p));
  const from = senderAgent(p) || String(p.sender || '');
  node.setAttribute('data-from', from);
  const to = targetOf(p, ctx.byId);
  if (to) node.setAttribute('data-to', to);
  else node.removeAttribute('data-to');
  text(node.querySelector('.tx'), String(p.text || ''));
  text(node.querySelector('.ago'), ago(p.ts));
  text(node.querySelector('.snd'), String(p.sender || ''));
  const kindEl = node.querySelector('.kind');
  if (kindEl) text(kindEl, ' ' + kindWord(p));
  const badge = node.querySelector('.stale');
  const stale = staleLabel(p, ctx.agents);
  if (stale && !badge) node.querySelector('.h').append(h('span', { class: 'stale' }, stale));
  else if (stale && badge) text(badge, stale);
  else if (!stale && badge) badge.remove();
  const form = node.querySelector('.ans');
  if (form) form.hidden = !isOpen(p);
  else if (isOpen(p)) node.append(answerForm(ctx, p.id));
}
