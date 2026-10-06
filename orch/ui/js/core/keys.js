// Keyboard shortcuts. A sequence is one key ("j", "?", "Enter", "Escape") or a "g" prefix and a key ("g f").
// Bindings belong to a scope: "global" is always active; other scopes (e.g. "drawer", "fleet") only while pushed.
// The most recently pushed scope wins when two bindings share a sequence.

const PREFIX_MS = 1200;
const bindings = []; // { seq, fn, scope, label }
const scopes = ['global'];
let pendingPrefix = null;
let prefixTimer = null;

function isTyping(target) {
  if (!target || !target.tagName) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === 'textarea' || tag === 'select') return true;
  if (tag === 'input') {
    const type = (target.getAttribute('type') || 'text').toLowerCase();
    return !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(type);
  }
  return Boolean(target.isContentEditable);
}

function clearPrefix() {
  pendingPrefix = null;
  if (prefixTimer) { clearTimeout(prefixTimer); prefixTimer = null; }
}

function find(seq) {
  for (let i = scopes.length - 1; i >= 0; i--) {
    for (let j = bindings.length - 1; j >= 0; j--) {
      if (bindings[j].seq === seq && bindings[j].scope === scopes[i]) return bindings[j];
    }
  }
  return null;
}

/**
 * Register a shortcut.
 * @param {string} seq  "j", "?", "/", ".", "Enter", "Escape", or "g f" style two-key sequences
 * @param {(ev: KeyboardEvent) => void} fn
 * @param {string} [scope]  "global" (default) or a scope name made active with pushScope()
 * @param {string} [label]  shown in the "?" help list
 * @returns {() => void} unbind
 */
export function bind(seq, fn, scope = 'global', label = '') {
  const b = { seq, fn, scope, label };
  bindings.push(b);
  return () => {
    const i = bindings.indexOf(b);
    if (i >= 0) bindings.splice(i, 1);
  };
}

/**
 * Activate a scope (e.g. while the drawer is open). Returns a function that deactivates it.
 * @param {string} name
 * @returns {() => void}
 */
export function pushScope(name) {
  scopes.push(name);
  return () => {
    const i = scopes.lastIndexOf(name);
    if (i > 0) scopes.splice(i, 1);
  };
}

/**
 * Bindings for the help overlay: active scopes only, one entry per sequence, labelled ones only.
 * @returns {{seq: string, label: string, scope: string}[]}
 */
export function help() {
  const seen = new Set();
  const out = [];
  for (let i = scopes.length - 1; i >= 0; i--) {
    for (const b of bindings) {
      if (b.scope !== scopes[i] || !b.label || seen.has(b.seq)) continue;
      seen.add(b.seq);
      out.push({ seq: b.seq, label: b.label, scope: b.scope });
    }
  }
  return out;
}

/**
 * Handle one keydown. Exported for tests; start() wires it to the document.
 * @param {{key: string, target?: any, ctrlKey?: boolean, metaKey?: boolean, altKey?: boolean, preventDefault?: () => void}} ev
 * @returns {boolean} true when a binding ran
 */
export function handle(ev) {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return false;
  const key = ev.key;
  // Escape always works so a focused input can be left; everything else is ignored while typing
  if (key !== 'Escape' && isTyping(ev.target)) { clearPrefix(); return false; }

  let seq = key;
  if (pendingPrefix) {
    seq = pendingPrefix + ' ' + key;
    clearPrefix();
  } else if (key === 'g' && bindings.some((b) => b.seq.startsWith('g ') && scopes.includes(b.scope))) {
    pendingPrefix = 'g';
    prefixTimer = setTimeout(clearPrefix, PREFIX_MS);
    if (ev.preventDefault) ev.preventDefault();
    return false;
  }
  const b = find(seq);
  if (!b) return false;
  if (ev.preventDefault) ev.preventDefault();
  b.fn(ev);
  return true;
}

/**
 * Listen for keydown on target (default: document). Returns a stop function.
 * @param {EventTarget} [target]
 * @returns {() => void}
 */
export function start(target = document) {
  const listener = (ev) => { handle(ev); };
  target.addEventListener('keydown', listener);
  return () => target.removeEventListener('keydown', listener);
}

/** Forget every binding and scope (tests only). */
export function reset() {
  bindings.length = 0;
  scopes.length = 1;
  clearPrefix();
}
