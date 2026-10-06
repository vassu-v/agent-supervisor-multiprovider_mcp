// Hash routes. A route is { view, params }; the board's workspace is the path segment (#/board/<ws>) and is
// exposed as params.ws like everywhere else. Invalid ids and unknown params are dropped, never echoed.
//
//   #/fleet?ws=all|<id>&st=&prov=&owner=&q=     (default)
//   #/timeline?ws=&span=15m|1h|6h
//   #/board/<ws>
//   #/models   #/escalations   #/audit
//   ...&agent=<id> on any view opens the drawer

export const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
export const VIEWS = ['fleet', 'timeline', 'board', 'models', 'escalations', 'audit'];
export const SPANS = ['15m', '1h', '6h'];
const LIST_RE = /^[A-Za-z0-9_-]{1,32}(,[A-Za-z0-9_-]{1,32}){0,15}$/; // st / prov: comma lists of plain words
const Q_MAX = 200;

const VALIDATE = {
  ws: (v) => v === 'all' || ID_RE.test(v),
  agent: (v) => ID_RE.test(v),
  owner: (v) => ID_RE.test(v),
  st: (v) => LIST_RE.test(v),
  prov: (v) => LIST_RE.test(v),
  span: (v) => SPANS.includes(v),
  q: (v) => v.length > 0 && v.length <= Q_MAX,
};
const ORDER = ['ws', 'st', 'prov', 'owner', 'q', 'span', 'agent'];

function decode(s) {
  try { return decodeURIComponent(s.replace(/\+/g, ' ')); } catch { return null; }
}

function cleanParams(raw) {
  const out = {};
  for (const name of ORDER) {
    const v = raw[name];
    if (typeof v === 'string' && VALIDATE[name](v)) out[name] = v;
  }
  return out;
}

/**
 * Parse a location hash (defaults to the current one). Anything that is not a "#/..." route, such as the
 * "#t=<token>" fragment before api.js strips it, parses as the fleet.
 * @param {string} [hash]
 * @returns {{ view: string, params: { ws?: string, st?: string, prov?: string, owner?: string, q?: string, span?: string, agent?: string } }}
 */
export function parse(hash) {
  const h = hash === undefined ? (typeof location === 'object' ? location.hash : '') : hash;
  if (!h.startsWith('#/')) return { view: 'fleet', params: {} };
  const [path, query = ''] = h.slice(2).split('?', 2);
  const segs = path.split('/');
  const raw = {};
  for (const part of query.split('&')) {
    if (!part) continue;
    const i = part.indexOf('=');
    const name = decode(i < 0 ? part : part.slice(0, i));
    const value = decode(i < 0 ? '' : part.slice(i + 1));
    if (name !== null && value !== null && Object.prototype.hasOwnProperty.call(VALIDATE, name)) raw[name] = value;
  }
  let view = VIEWS.includes(segs[0]) ? segs[0] : 'fleet';
  if (view === 'board') {
    const ws = decode(segs[1] || '');
    if (ws && ID_RE.test(ws)) raw.ws = ws; else { delete raw.ws; view = 'fleet'; } // a board needs a workspace
  }
  return { view, params: cleanParams(raw) };
}

/**
 * Build the hash for a route. Invalid params are dropped.
 * @param {string} view
 * @param {object} [params]
 * @returns {string}  e.g. "#/board/w1?agent=a1"
 */
export function format(view, params = {}) {
  const p = cleanParams(params);
  let path = VIEWS.includes(view) ? view : 'fleet';
  if (path === 'board') {
    if (p.ws && p.ws !== 'all') path += '/' + encodeURIComponent(p.ws); else path = 'fleet';
    if (path !== 'fleet') delete p.ws;
  }
  const q = ORDER.filter((k) => k in p).map((k) => k + '=' + encodeURIComponent(p[k])).join('&');
  return '#/' + path + (q ? '?' + q : '');
}

/**
 * Navigate. params replace the current ones entirely; use patch() to change a few.
 * @param {string|{view: string, params?: object}} route
 * @param {object} [params]
 */
export function go(route, params) {
  const view = typeof route === 'string' ? route : route.view;
  const p = params || (typeof route === 'object' && route.params) || {};
  const next = format(view, p);
  if (location.hash !== next) location.hash = next;
}

/**
 * Change some params of the current route; a value of null/undefined/'' removes that param.
 * @param {object} changes
 */
export function patch(changes) {
  const cur = parse();
  const params = { ...cur.params };
  for (const [k, v] of Object.entries(changes)) {
    if (v === null || v === undefined || v === '') delete params[k]; else params[k] = String(v);
  }
  go(cur.view, params);
}

/**
 * Call cb(route) on every hash change. Returns an unsubscribe function.
 * @param {(route: ReturnType<typeof parse>) => void} cb
 * @returns {() => void}
 */
export function onChange(cb) {
  const handler = () => cb(parse());
  window.addEventListener('hashchange', handler);
  return () => window.removeEventListener('hashchange', handler);
}
