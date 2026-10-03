// Drawer actions: interrupt, send (queue / steer / interrupt), stop with an inline reason form.
// No prompt() anywhere. All writes go through api.post with the exact server bodies:
//   /api/interrupt {id}, /api/send {id, msg, mode}, /api/stop {id, reason}.
import { h, on, text } from '../core/h.js';
import { canSteer, canInterrupt, stopReasonError } from './drawer-logic.js';

// @param api  net client ({post})
// @param getId  () => current agent id or null
// @returns {{el: Element, update: (agent) => void, reset: () => void}}
export function buildActions(api, getId) {
  const intB = on(h('button', { type: 'button' }, 'Interrupt turn'), 'click', doInterrupt);
  const stopB = on(h('button', { type: 'button', class: 'btn-deny' }, 'Stop'), 'click', () => { stopF.hidden = false; text(stopE, ''); reason.focus(); });
  const msg = h('p', { class: 'muted dw-note', role: 'status' });
  const box = h('textarea', { id: 'dw-msg', name: 'message', rows: 2, maxlength: 4000, placeholder: 'Message to the agent…' });
  const mQ = h('input', { type: 'radio', name: 'dw-mode', value: 'queue', checked: true });
  const mS = h('input', { type: 'radio', name: 'dw-mode', value: 'steer' });
  const mI = h('input', { type: 'radio', name: 'dw-mode', value: 'interrupt' });
  const sendB = h('button', { type: 'submit' }, 'Send');
  const sendF = h('form', { class: 'dw-send' }, h('label', { for: 'dw-msg' }, 'Message'), box,
    h('div', { class: 'dw-modes', role: 'radiogroup', 'aria-label': 'Send mode' },
      h('label', null, mQ, ' Queue'), h('label', null, mS, ' Steer mid-turn'), h('label', null, mI, ' Interrupt and send')),
    h('div', { class: 'dw-row' }, sendB));
  on(sendF, 'submit', doSend);
  const reason = h('input', { type: 'text', id: 'dw-reason', name: 'reason', maxlength: 500, placeholder: 'Why is this agent stopping?', autocomplete: 'off' });
  const stopE = h('p', { class: 'dw-err', role: 'alert' });
  const stopGo = h('button', { type: 'submit' }, 'Stop now');
  const stopF = h('form', { class: 'dw-stop' }, h('label', { for: 'dw-reason' }, 'Reason (required)'),
    reason, stopE, h('div', { class: 'dw-row' }, stopGo, on(h('button', { type: 'button' }, 'Cancel'), 'click', hideStop)));
  stopF.hidden = true;
  on(stopF, 'submit', doStop);
  const el = h('div', null, h('div', { class: 'dw-row' }, intB, stopB), sendF, stopF, msg);

  async function doInterrupt() {
    const id = getId();
    if (!id) return;
    intB.disabled = true;
    text(msg, 'Interrupting…');
    const r = await api.post('/api/interrupt', { id });
    intB.disabled = false;
    text(msg, r && r.error ? 'Interrupt failed: ' + r.error : 'Interrupted.' + (r && r.how ? ' ' + r.how : ''));
  }
  async function doSend(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    const id = getId();
    if (!id) return;
    const m = String(box.value || '').trim();
    if (!m) { text(msg, 'Type a message first.'); box.focus(); return; }
    const mode = mS.checked ? 'steer' : mI.checked ? 'interrupt' : 'queue';
    sendB.disabled = true;
    const r = await api.post('/api/send', { id, msg: m, mode });
    sendB.disabled = false;
    if (r && r.error) text(msg, 'Send failed: ' + r.error);
    else { box.value = ''; text(msg, 'Sent (' + String((r && r.result) || mode) + ').' + (r && r.note ? ' ' + r.note : '')); }
  }
  function hideStop() { stopF.hidden = true; reason.value = ''; text(stopE, ''); }
  async function doStop(ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    const id = getId();
    if (!id) return;
    const v = String(reason.value || ''), err = stopReasonError(v);
    if (err) { text(stopE, err); reason.focus(); return; }
    stopGo.disabled = true;
    const r = await api.post('/api/stop', { id, reason: v.trim() });
    stopGo.disabled = false;
    if (r && r.error) text(stopE, 'Stop failed: ' + r.error);
    else { hideStop(); text(msg, 'Stopped.'); }
  }
  // Caps-gated controls: steer only when caps.steer, interrupt only when caps.interrupt.
  function update(a) {
    const live = Boolean(a && a.status !== 'dead'), busy = Boolean(a && (a.status === 'busy' || a.status === 'starting'));
    intB.disabled = !(live && busy && canInterrupt(a));
    intB.setAttribute('title', canInterrupt(a) ? 'Stop the current turn; the session is kept.' : 'This provider cannot be interrupted.');
    sendB.disabled = !live;
    stopB.disabled = !live;
    mS.disabled = !canSteer(a);
    if (!canSteer(a)) mS.checked = false;
    mI.disabled = !canInterrupt(a);
    if (!canInterrupt(a)) mI.checked = false;
    if (!mS.checked && !mI.checked) mQ.checked = true;
  }
  function reset() { hideStop(); text(msg, ''); }
  return { el, update, reset };
}
