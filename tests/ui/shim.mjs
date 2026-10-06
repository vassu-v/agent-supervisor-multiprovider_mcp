// A tiny fake DOM for node tests: enough for h(), list(), a11y and view tests that count nodes and check identity.
// Not a browser: no CSS, no layout, no parsing of markup (there is deliberately no innerHTML).
//
//   import { install } from './shim.mjs';
//   const document = install();          // sets globalThis.document (and window) to a fresh fake document
//   ... document.body.appendChild(h('div', null, 'x'));
//   serialize(node)                      // markup string, text escaped, for assertions
//   counts.inserts / counts.removes      // DOM mutation counters (reset with resetCounts())

export const counts = { inserts: 0, removes: 0, creates: 0 };
export function resetCounts() { counts.inserts = 0; counts.removes = 0; counts.creates = 0; }

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.bubbles = init.bubbles !== false;
    Object.assign(this, init);
    this.defaultPrevented = false;
    this.propagationStopped = false;
    this.target = null;
    this.currentTarget = null;
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.propagationStopped = true; }
}

class Node {
  constructor(doc, nodeType) {
    this.ownerDocument = doc;
    this.nodeType = nodeType;
    this.parentNode = null;
    this.childNodes = [];
    this._listeners = new Map();
  }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const s = this.parentNode.childNodes;
    return s[s.indexOf(this) + 1] || null;
  }
  get previousSibling() {
    if (!this.parentNode) return null;
    const s = this.parentNode.childNodes;
    return s[s.indexOf(this) - 1] || null;
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === this.ownerDocument;
  }
  contains(other) {
    for (let n = other; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  get textContent() {
    if (this.nodeType === 3) return this.data;
    return this.childNodes.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    if (this.nodeType === 3) { this.data = String(v); return; }
    for (const c of [...this.childNodes]) this.removeChild(c);
    const s = v === null || v === undefined ? '' : String(v);
    if (s) this.appendChild(this.ownerDocument.createTextNode(s));
  }
  _detach() {
    const p = this.parentNode;
    if (!p) return;
    // a real browser blurs a focused element when it (or an ancestor) leaves its place
    const doc = this.ownerDocument;
    if (doc.activeElement && this.contains(doc.activeElement)) doc.activeElement = doc.body;
    p.childNodes.splice(p.childNodes.indexOf(this), 1);
    this.parentNode = null;
  }
  appendChild(node) { return this.insertBefore(node, null); }
  insertBefore(node, ref) {
    if (node.nodeType === 11) { for (const c of [...node.childNodes]) this.insertBefore(c, ref); return node; }
    if (node.contains(this)) throw new Error('HierarchyRequestError');
    if (ref && ref.parentNode !== this) throw new Error('NotFoundError: ref is not a child');
    node._detach();
    const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
    this.childNodes.splice(i, 0, node);
    node.parentNode = this;
    counts.inserts++;
    return node;
  }
  removeChild(node) {
    if (node.parentNode !== this) throw new Error('NotFoundError');
    node._detach();
    counts.removes++;
    return node;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceChildren(...nodes) {
    for (const c of [...this.childNodes]) this.removeChild(c);
    for (const n of nodes) this.appendChild(typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n);
  }
  append(...nodes) { for (const n of nodes) this.appendChild(typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n); }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const l = this._listeners.get(type);
    if (l && l.includes(fn)) l.splice(l.indexOf(fn), 1);
  }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    for (let n = this; n; n = ev.bubbles ? n.parentNode : null) {
      ev.currentTarget = n;
      for (const fn of [...(n._listeners.get(ev.type) || [])]) fn.call(n, ev);
      if (ev.propagationStopped) break;
    }
    return !ev.defaultPrevented;
  }
}

class Text extends Node {
  constructor(doc, data) { super(doc, 3); this.data = data; }
  get nodeValue() { return this.data; }
}

class ClassList {
  constructor(el) { this.el = el; }
  _get() { return (this.el.getAttribute('class') || '').split(/\s+/).filter(Boolean); }
  _set(list) { this.el.setAttribute('class', list.join(' ')); }
  contains(c) { return this._get().includes(c); }
  add(...cs) { const l = this._get(); for (const c of cs) if (!l.includes(c)) l.push(c); this._set(l); }
  remove(...cs) { this._set(this._get().filter((c) => !cs.includes(c))); }
  toggle(c, force) {
    const has = this.contains(c);
    const want = force === undefined ? !has : Boolean(force);
    if (want && !has) this.add(c);
    if (!want && has) this.remove(c);
    return want;
  }
}

class Element extends Node {
  constructor(doc, tag, ns) {
    super(doc, 1);
    this.namespaceURI = ns || null;
    this.localName = tag;
    this.tagName = ns ? tag : tag.toUpperCase();
    this._attrs = new Map();
    this.classList = new ClassList(this);
    this.style = { _props: new Map(), setProperty(k, v) { this._props.set(k, String(v)); }, getPropertyValue(k) { return this._props.get(k) || ''; }, removeProperty(k) { this._props.delete(k); } };
    if (tag === 'input' || tag === 'textarea') { this.value = ''; this.selectionStart = 0; this.selectionEnd = 0; }
    counts.creates++;
  }
  setAttribute(name, value) { this._attrs.set(String(name), String(value)); }
  getAttribute(name) { return this._attrs.has(name) ? this._attrs.get(name) : null; }
  hasAttribute(name) { return this._attrs.has(name); }
  removeAttribute(name) { this._attrs.delete(name); }
  toggleAttribute(name, force) {
    const want = force === undefined ? !this._attrs.has(name) : Boolean(force);
    if (want) this._attrs.set(name, ''); else this._attrs.delete(name);
    return want;
  }
  get attributes() { return [...this._attrs].map(([name, value]) => ({ name, value })); }
  get id() { return this.getAttribute('id') || ''; }
  get className() { return this.getAttribute('class') || ''; }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(v) { this.toggleAttribute('hidden', Boolean(v)); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(v) { this.toggleAttribute('disabled', Boolean(v)); }
  get dataset() {
    const el = this;
    const toAttr = (k) => 'data-' + k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
    return new Proxy({}, {
      get: (_, k) => (typeof k === 'string' ? el.getAttribute(toAttr(k)) ?? undefined : undefined),
      set: (_, k, v) => { el.setAttribute(toAttr(k), v); return true; },
      deleteProperty: (_, k) => { el.removeAttribute(toAttr(k)); return true; },
    });
  }
  focus() { this.ownerDocument.activeElement = this; this.dispatchEvent(new FakeEvent('focusin')); }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  /** Tag, #id, .class and [attr] / [attr="v"] selectors only, one compound selector (no combinators). */
  querySelectorAll(sel) {
    const m = matcher(sel);
    const out = [];
    const walk = (n) => { for (const c of n.children) { if (m(c)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function matcher(sel) {
  const parts = sel.match(/^([a-zA-Z][\w-]*)?((?:[#.][\w-]+|\[[^\]]+\])*)$/);
  if (!parts) throw new Error('shim: unsupported selector ' + sel);
  const tag = parts[1] ? parts[1].toLowerCase() : null;
  const tests = [];
  for (const t of parts[2].match(/[#.][\w-]+|\[[^\]]+\]/g) || []) {
    if (t[0] === '#') tests.push((e) => e.getAttribute('id') === t.slice(1));
    else if (t[0] === '.') tests.push((e) => e.classList.contains(t.slice(1)));
    else {
      const [, name, value] = t.match(/^\[([\w-]+)(?:="?([^"]*)"?)?\]$/);
      tests.push((e) => (value === undefined ? e.hasAttribute(name) : e.getAttribute(name) === value));
    }
  }
  return (e) => (!tag || e.localName === tag) && tests.every((f) => f(e));
}

class Document extends Node {
  constructor() {
    super(null, 9);
    this.ownerDocument = this;
    this.documentElement = new Element(this, 'html');
    this.body = new Element(this, 'body');
    this.appendChild(this.documentElement);
    this.documentElement.appendChild(this.body);
    this.activeElement = this.body;
  }
  get isConnected() { return true; }
  createElement(tag) { return new Element(this, String(tag).toLowerCase()); }
  createElementNS(ns, tag) { return new Element(this, tag, ns); }
  createTextNode(s) { return new Text(this, String(s)); }
  createDocumentFragment() { const f = new Node(this, 11); return f; }
  getElementById(id) { return this.documentElement.querySelector('#' + id); }
  querySelectorAll(sel) { return this.documentElement.querySelectorAll(sel); }
  querySelector(sel) { return this.documentElement.querySelector(sel); }
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Markup for a node, with text escaped: what a browser would serialise. */
export function serialize(node) {
  if (node.nodeType === 3) return esc(node.data);
  if (node.nodeType !== 1) return node.childNodes.map(serialize).join('');
  const attrs = node.attributes.map((a) => ` ${a.name}="${esc(a.value)}"`).join('');
  return `<${node.localName}${attrs}>${node.childNodes.map(serialize).join('')}</${node.localName}>`;
}

/** Install a fresh fake document as globalThis.document (and a minimal window). Returns it. */
export function install() {
  const doc = new Document();
  globalThis.document = doc;
  globalThis.window = globalThis.window || { addEventListener() {}, removeEventListener() {} };
  resetCounts();
  return doc;
}

export { FakeEvent as Event, Document, Element, Text };
