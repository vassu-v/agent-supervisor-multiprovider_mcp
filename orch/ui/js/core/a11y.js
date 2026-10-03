// Accessibility helpers: live-region announcements, focus trapping for dialogs, roving tabindex for the fleet
// treegrid, and the reduced-motion preference.

const REGION_IDS = { assertive: 'live-assertive', polite: 'live-polite' };
const announced = new Set();
const ANNOUNCED_MAX = 2000;

/**
 * Speak a message through one of the two live regions in index.html.
 * With an id, each id is announced once per page load (escalations, questions arrive on every refetch).
 * @param {string} msg
 * @param {'assertive'|'polite'} [level]
 * @param {string} [id]
 * @returns {boolean} false when skipped (already announced, or no region)
 */
export function announce(msg, level = 'polite', id) {
  if (id !== undefined) {
    const k = level + ':' + id;
    if (announced.has(k)) return false;
    if (announced.size >= ANNOUNCED_MAX) announced.clear();
    announced.add(k);
  }
  const region = document.getElementById(REGION_IDS[level] || REGION_IDS.polite);
  if (!region) return false;
  // clear then set, so the same text twice in a row is still read out
  region.textContent = '';
  setTimeout(() => { region.textContent = String(msg); }, 30);
  return true;
}

function isHidden(el) {
  return el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true' || el.hasAttribute('inert');
}

function isFocusable(el) {
  const tag = el.tagName.toLowerCase();
  if (el.hasAttribute('disabled')) return false;
  const ti = el.getAttribute('tabindex');
  if (ti !== null) return Number(ti) >= 0;
  if (tag === 'a') return el.hasAttribute('href');
  return tag === 'button' || tag === 'input' || tag === 'select' || tag === 'textarea' || tag === 'summary';
}

/** Focusable descendants of root in document order, skipping hidden subtrees. */
export function focusables(root) {
  const out = [];
  const walk = (node) => {
    for (const c of node.children) {
      if (isHidden(c)) continue;
      if (isFocusable(c)) out.push(c);
      walk(c);
    }
  };
  walk(root);
  return out;
}

/**
 * Keep Tab / Shift+Tab inside el and focus its first control (or el itself, which should have tabindex="-1").
 * @param {HTMLElement} el
 * @returns {() => void} release: removes the trap and returns focus to what had it before, if still on the page
 */
export function trapFocus(el) {
  const doc = el.ownerDocument;
  const previous = doc.activeElement;
  const onKey = (ev) => {
    if (ev.key !== 'Tab') return;
    const items = focusables(el);
    if (!items.length) { ev.preventDefault(); el.focus(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    const active = doc.activeElement;
    if (ev.shiftKey && (active === first || !el.contains(active))) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && (active === last || !el.contains(active))) { ev.preventDefault(); first.focus(); }
  };
  el.addEventListener('keydown', onKey);
  const items = focusables(el);
  (items[0] || el).focus();
  return () => {
    el.removeEventListener('keydown', onKey);
    if (previous && previous.isConnected && typeof previous.focus === 'function') previous.focus();
  };
}

/**
 * Roving tabindex over the rows of a list or treegrid: exactly one row has tabindex=0, arrows move between rows.
 * Arrow Right/Left call onToggle(row, true/false) on rows with aria-expanded; Left on a collapsed or leaf row
 * moves to its parent (the previous row with a lower aria-level). Enter calls onActivate(row).
 * Call refresh() after the rows changed.
 * @param {HTMLElement} container
 * @param {{ role?: string, onToggle?: (row: HTMLElement, expand: boolean) => void, onActivate?: (row: HTMLElement) => void }} [opts]
 * @returns {{ refresh: () => void, focus: (row: HTMLElement|number) => void, current: () => HTMLElement|null, move: (delta: number) => void, destroy: () => void }}
 */
export function roving(container, opts = {}) {
  const role = opts.role || 'row';
  let current = null;

  const rows = () => {
    const out = [];
    const walk = (node) => {
      for (const c of node.children) {
        if (isHidden(c)) continue;
        if (c.getAttribute('role') === role) out.push(c);
        walk(c);
      }
    };
    walk(container);
    return out;
  };

  function setCurrent(row, focus) {
    if (current && current !== row) current.setAttribute('tabindex', '-1');
    current = row;
    if (!row) return;
    row.setAttribute('tabindex', '0');
    if (focus) row.focus();
  }

  function refresh() {
    const all = rows();
    for (const r of all) if (r !== current) r.setAttribute('tabindex', '-1');
    setCurrent(all.includes(current) ? current : all[0] || null, false);
  }

  function move(delta) {
    const all = rows();
    if (!all.length) return;
    const i = all.indexOf(current);
    const next = Math.max(0, Math.min(all.length - 1, (i < 0 ? 0 : i + delta)));
    setCurrent(all[next], true);
  }

  function focusRow(target) {
    const all = rows();
    const row = typeof target === 'number' ? all[target] : target;
    if (row && all.includes(row)) setCurrent(row, true);
  }

  function parentOf(row, all) {
    const level = Number(row.getAttribute('aria-level') || 1);
    for (let i = all.indexOf(row) - 1; i >= 0; i--) {
      if (Number(all[i].getAttribute('aria-level') || 1) < level) return all[i];
    }
    return null;
  }

  const onKey = (ev) => {
    if (ev.target !== current) return; // keys inside a row's controls belong to those controls
    const all = rows();
    const expanded = current.getAttribute('aria-expanded');
    switch (ev.key) {
      case 'ArrowDown': move(1); break;
      case 'ArrowUp': move(-1); break;
      case 'Home': setCurrent(all[0], true); break;
      case 'End': setCurrent(all[all.length - 1], true); break;
      case 'ArrowRight':
        if (expanded === 'false' && opts.onToggle) opts.onToggle(current, true); else return;
        break;
      case 'ArrowLeft':
        if (expanded === 'true' && opts.onToggle) opts.onToggle(current, false);
        else { const p = parentOf(current, all); if (p) setCurrent(p, true); else return; }
        break;
      case 'Enter':
        if (opts.onActivate) opts.onActivate(current); else return;
        break;
      default: return;
    }
    ev.preventDefault();
  };
  // a click or programmatic focus on a row makes it the tab stop
  const onFocusIn = (ev) => {
    const all = rows();
    let n = ev.target;
    while (n && n !== container && !all.includes(n)) n = n.parentNode;
    if (n && n !== container && n !== current) setCurrent(n, false);
  };
  container.addEventListener('keydown', onKey);
  container.addEventListener('focusin', onFocusIn);
  refresh();

  return {
    refresh,
    focus: focusRow,
    current: () => current,
    move,
    destroy() {
      container.removeEventListener('keydown', onKey);
      container.removeEventListener('focusin', onFocusIn);
    },
  };
}

/** @returns {boolean} true when the user asked for reduced motion */
export function reducedMotion() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}
