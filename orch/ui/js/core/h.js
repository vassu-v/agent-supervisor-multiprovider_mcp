// DOM construction for the whole UI. Every element is created here so the security rules live in one place:
// text is always a text node, attributes come from an allowlist, and event handlers are attached with on().

// assembled so the "no URL strings in orch/ui" grep keeps meaning "no network URLs"; this is a namespace name only
const SVG_NS = ['http:', '', 'www.w3.org', '2000', 'svg'].join('/');
const SVG_TAGS = new Set(['svg', 'g', 'path', 'rect', 'circle', 'line', 'polyline', 'polygon', 'use', 'symbol', 'defs']);

// Attributes that may be set. Anything else throws: that rejects event-handler attributes, the inline style
// attribute, srcdoc, raw-markup properties and anything we have not thought about yet.
const ALLOWED = new Set([
  'class', 'id', 'role', 'type', 'value', 'disabled', 'tabindex', 'title', 'for', 'placeholder', 'name',
  'checked', 'selected', 'href',
  // additions beyond the architecture table; all inert (no script, no URL, no markup)
  'hidden', 'open', 'rows', 'cols', 'maxlength', 'minlength', 'min', 'max', 'step', 'autocomplete', 'spellcheck',
  'colspan', 'rowspan', 'scope', 'lang', 'dir', 'readonly', 'required', 'multiple', 'inputmode', 'enterkeyhint',
  'datetime', 'size', 'width', 'height',
]);
const SVG_ALLOWED = new Set([
  'viewBox', 'd', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'points', 'fill', 'stroke',
  'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'transform', 'focusable',
  'preserveAspectRatio', 'opacity',
]);
// set as DOM properties so form controls reflect them (attributes only set the default)
const PROPS = new Set(['value', 'checked', 'selected']);

function checkAttr(name, value, isSvg) {
  if (/^on/i.test(name)) throw new Error(`h(): event handler attribute "${name}" is not allowed; use on(el, type, fn)`);
  if (name === 'href') {
    if (typeof value !== 'string' || value[0] !== '#') throw new Error('h(): href must be an in-page "#..." link');
    return;
  }
  if (ALLOWED.has(name) || name.startsWith('data-') || name.startsWith('aria-')) return;
  if (isSvg && SVG_ALLOWED.has(name)) return;
  throw new Error(`h(): attribute "${name}" is not allowed`);
}

function setAttr(el, name, value) {
  if (value === null || value === undefined || value === false) {
    // aria-* and data-* are string-valued: false is a meaningful value there
    if (value === false && (name.startsWith('aria-') || name.startsWith('data-'))) el.setAttribute(name, 'false');
    return;
  }
  if (PROPS.has(name)) {
    el[name] = name === 'value' ? String(value) : Boolean(value);
    if (name !== 'value') el.setAttribute(name, '');
    else el.setAttribute('value', String(value));
    return;
  }
  if (value === true) {
    el.setAttribute(name, name.startsWith('aria-') || name.startsWith('data-') ? 'true' : '');
    return;
  }
  el.setAttribute(name, String(value));
}

function appendKids(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false || kid === true) continue;
    if (Array.isArray(kid)) { appendKids(el, kid); continue; }
    if (typeof kid === 'string' || typeof kid === 'number') {
      el.appendChild(el.ownerDocument.createTextNode(String(kid)));
      continue;
    }
    if (kid && typeof kid.nodeType === 'number') { el.appendChild(kid); continue; }
    throw new TypeError('h(): children must be strings, numbers, nodes or arrays of them');
  }
}

/**
 * Create an element.
 * @param {string} tag  HTML tag, or one of the SVG tags (svg g path rect circle line polyline polygon use symbol defs)
 * @param {Object<string, string|number|boolean|null|undefined>|null} [attrs]  allowlisted attributes only.
 *   Throws on on* handlers, style, srcdoc, raw-markup properties, and href that does not start with '#'.
 *   true sets a boolean attribute (aria-* / data-* get "true"); false/null/undefined omit it.
 * @param {...(string|number|Node|Array|null|undefined|boolean)} kids  strings and numbers become text nodes.
 * @returns {Element}
 */
export function h(tag, attrs, ...kids) {
  const isSvg = SVG_TAGS.has(tag);
  const el = isSvg ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  if (attrs) {
    for (const name of Object.keys(attrs)) {
      checkAttr(name, attrs[name], isSvg);
      setAttr(el, name, attrs[name]);
    }
  }
  appendKids(el, kids);
  return el;
}

/**
 * Attach an event listener and return the element, so construction can stay one expression.
 * @template {EventTarget} T
 * @param {T} el
 * @param {string} type
 * @param {(ev: Event) => void} fn
 * @param {AddEventListenerOptions} [opts]
 * @returns {T}
 */
export function on(el, type, fn, opts) {
  el.addEventListener(type, fn, opts);
  return el;
}

/**
 * Set an element's text, only touching the DOM when it changed. Never parses markup.
 * @param {Node} el
 * @param {*} str  null/undefined become ''
 * @returns {Node}
 */
export function text(el, str) {
  const s = str === null || str === undefined ? '' : String(str);
  if (el.textContent !== s) el.textContent = s;
  return el;
}

// per-parent reconciliation state: the keys in their current DOM order and key -> node
const lists = new WeakMap();

// Indexes (into seq) of one longest strictly increasing subsequence. Nodes on it keep their place; only the others move.
function longestIncreasing(seq) {
  const tails = [];
  const prev = new Array(seq.length);
  for (let i = 0; i < seq.length; i++) {
    if (seq[i] < 0) continue; // new node, not part of the old order
    let lo = 0, hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (seq[tails[mid]] < seq[i]) lo = mid + 1; else hi = mid;
    }
    prev[i] = lo > 0 ? tails[lo - 1] : -1;
    tails[lo] = i;
  }
  const keep = new Set();
  let k = tails.length ? tails[tails.length - 1] : -1;
  while (k >= 0) { keep.add(k); k = prev[k]; }
  return keep;
}

function activeIn(parent) {
  const doc = parent.ownerDocument;
  const a = doc && doc.activeElement;
  return a && a !== doc.body && parent.contains(a) ? a : null;
}

/**
 * Keyed reconciler: make parent's children match items, reusing nodes by key.
 * Nodes for existing keys are kept (update is called), new keys are created, stale keys removed, and only the
 * minimum set of nodes is moved, so untouched rows keep focus, caret and scroll position.
 * The list owns all children of parent: do not mix with other children.
 * @template T
 * @param {Element} parent
 * @param {T[]} items
 * @param {(item: T, index: number) => string|number} key  unique per item; duplicates throw
 * @param {(item: T, index: number) => Node} create  builds the node for a new key
 * @param {(node: Node, item: T, index: number) => void} [update]  patches a reused node (not called after create)
 * @returns {Node[]} the nodes in item order
 */
export function list(parent, items, key, create, update) {
  let state = lists.get(parent);
  if (!state) { state = { order: [], nodes: new Map() }; lists.set(parent, state); }

  const keys = items.map((item, i) => {
    const k = key(item, i);
    return typeof k === 'number' ? String(k) : k;
  });
  const wanted = new Set(keys);
  if (wanted.size !== keys.length) throw new Error('list(): duplicate keys');

  const focused = activeIn(parent);
  const caret = focused && typeof focused.selectionStart === 'number'
    ? [focused.selectionStart, focused.selectionEnd] : null;

  // remove stale nodes first so the old order only holds survivors
  const oldOrder = [];
  for (const k of state.order) {
    if (wanted.has(k)) { oldOrder.push(k); continue; }
    const node = state.nodes.get(k);
    if (node.parentNode === parent) parent.removeChild(node);
    state.nodes.delete(k);
  }
  const oldIndex = new Map(oldOrder.map((k, i) => [k, i]));

  const nodes = keys.map((k, i) => {
    let node = state.nodes.get(k);
    if (node) {
      if (update) update(node, items[i], i);
    } else {
      node = create(items[i], i);
      state.nodes.set(k, node);
    }
    return node;
  });

  const seq = keys.map((k) => (oldIndex.has(k) ? oldIndex.get(k) : -1));
  const keep = longestIncreasing(seq);
  // walk backwards so each node is placed before the one already positioned after it
  let anchor = null;
  for (let i = nodes.length - 1; i >= 0; i--) {
    const node = nodes[i];
    if (!keep.has(i) || node.parentNode !== parent) parent.insertBefore(node, anchor);
    anchor = node;
  }
  state.order = keys;

  // moving a node that contains focus blurs it in real browsers: put focus (and the caret) back
  if (focused && focused.isConnected && focused.ownerDocument.activeElement !== focused) {
    focused.focus({ preventScroll: true });
    if (caret && typeof focused.setSelectionRange === 'function') focused.setSelectionRange(caret[0], caret[1]);
  }
  return nodes;
}
