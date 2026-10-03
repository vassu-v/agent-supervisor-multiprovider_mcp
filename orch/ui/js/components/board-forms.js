// Board forms: client-side limits, exact server bodies, compose + answer forms.
// Posts through api.post only; api.post adds by: dashboard itself.
import { h, on, text } from '../core/h.js';

export const MAX_TEXT = 500;
export const MAX_RAW = 8000;
export const ANNOUNCE_KINDS = ['started', 'done', 'changed', 'blocked', 'info', 'handoff'];

// Client-side copy of the orch/board.py text limits. Returns an error or null.
export function validateText(t) {
  if (typeof t !== 'string') return 'text is required';
  if (t.length > MAX_RAW) return 'text is too long';
  if (!t.trim()) return 'text is required';
  if (t.trim().length > MAX_TEXT) return 'text is too long (max 500 chars)';
  return null;
}

// Exact POST body for /api/announce.
export function announceBody(ws, kind, t) {
  return { ws, kind: ANNOUNCE_KINDS.includes(kind) ? kind : 'info', text: String(t).trim() };
}

// Exact POST body for /api/ask.
export function askBody(ws, t) {
  return { ws, text: String(t).trim() };
}

// Exact POST body for /api/answer.
export function answerBody(id, t) {
  return { id: Number(id), text: String(t).trim() };
}

async function send(ev, ctx, input, btn, err, path, body) {
  ev.preventDefault();
  const bad = validateText(input.value);
  if (bad) {
    err.hidden = false;
    text(err, bad);
    input.focus();
    return;
  }
  err.hidden = true;
  text(err, '');
  btn.disabled = true;
  const r = await ctx.api.post(path, body(input.value));
  btn.disabled = false;
  if (r && r.error) {
    err.hidden = false;
    text(err, String(r.error));
    return;
  }
  input.value = '';
}

// Answer form for one open question. ctx: {api, ws}.
export function answerForm(ctx, id) {
  const input = h('input', {
    type: 'text', placeholder: 'Answer as dashboard…',
    'aria-label': 'Answer question ' + id, name: 'answer-' + id,
  });
  const btn = h('button', { type: 'submit' }, 'Answer');
  const err = h('p', { class: 'err', role: 'alert', hidden: true });
  const form = h('form', { class: 'ans' }, input, btn, err);
  on(form, 'submit', (ev) => send(ev, ctx, input, btn, err, '/api/answer', (v) => answerBody(id, v)));
  return form;
}

// Announce / ask composer. kinds is null for ask. makeBody(kind, value).
export function composeForm(ctx, label, kinds, path, submitWord, makeBody) {
  const input = h('input', {
    type: 'text', placeholder: label + ' as dashboard…', 'aria-label': label, name: label,
  });
  const err = h('p', { class: 'err', role: 'alert', hidden: true });
  const kids = [input];
  let getKind = null;
  if (kinds) {
    const sel = h('select', { 'aria-label': 'Kind', name: 'kind' });
    for (const k of kinds) sel.append(h('option', { value: k }, k));
    getKind = () => sel.value;
    kids.unshift(sel);
  }
  const btn = h('button', { type: 'submit' }, submitWord);
  kids.push(btn);
  const form = h('form', { class: 'compose' }, kids, err);
  on(form, 'submit', (ev) => send(ev, ctx, input, btn, err, path, (v) => makeBody(getKind ? getKind() : null, v)));
  return form;
}
