// Same-origin JSON client for the daemon. Token: URL fragment (#t=...) -> sessionStorage -> memory. Plain ES module, no deps.
// createApi(env) takes injectable fetch/storage/location/history so it can be tested in node; the default export-bound
// functions below use the browser globals lazily.
const KEY = "switchyard.token";

export function createApi(env = {}) {
  const g = globalThis;
  let tok;                     // undefined = not yet resolved
  const authCbs = [];

  const store = () => {
    try { return env.storage !== undefined ? env.storage : g.sessionStorage; } catch (e) { return null; }
  };
  const loc = () => (env.location !== undefined ? env.location : g.location);
  const hist = () => (env.history !== undefined ? env.history : g.history);

  function resolve() {
    if (tok !== undefined) return tok;
    tok = null;
    const l = loc();
    const hash = l && typeof l.hash === "string" ? l.hash : "";
    const m = /^#(?:.*&)?t=([^&]*)/.exec(hash);
    if (m && m[1]) {
      try { tok = decodeURIComponent(m[1]); } catch (e) { tok = m[1]; }
      try { const s = store(); if (s) s.setItem(KEY, tok); } catch (e) { /* storage may be blocked */ }
      try {
        const h = hist();
        if (h && h.replaceState) h.replaceState(null, "", (l.pathname || "/") + (l.search || ""));
      } catch (e) { /* ignore */ }
      return tok;
    }
    try { const s = store(); const v = s && s.getItem(KEY); if (v) tok = v; } catch (e) { /* ignore */ }
    return tok;
  }

  function token() { return resolve(); }

  function setToken(t) {
    tok = t || null;
    try {
      const s = store();
      if (s) { if (tok) s.setItem(KEY, tok); else s.removeItem(KEY); }
    } catch (e) { /* ignore */ }
  }

  function onAuthError(cb) {
    authCbs.push(cb);
    return () => { const i = authCbs.indexOf(cb); if (i >= 0) authCbs.splice(i, 1); };
  }

  async function request(method, path, body, opts) {
    const f = env.fetch || (g.fetch && g.fetch.bind(g));
    const headers = { Accept: "application/json" };
    const t = resolve();
    if (t) headers.Authorization = "Bearer " + t;
    const init = { method, headers };
    if (opts && opts.signal) init.signal = opts.signal;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await f(path, init);
    } catch (e) {
      const aborted = !!(e && e.name === "AbortError");
      return { error: aborted ? "aborted" : String((e && e.message) || e), status: 0, aborted };
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (res.status === 401) {
      for (const cb of authCbs.slice()) { try { cb(); } catch (e) { /* a bad listener must not break the client */ } }
    }
    if (!res.ok) {
      const msg = data && typeof data === "object" && data.error ? String(data.error) : "HTTP " + res.status;
      return { error: msg, status: res.status };
    }
    if (data && typeof data === "object" && !Array.isArray(data) && data.error) {
      return { error: String(data.error), status: res.status };
    }
    return data;
  }

  return {
    get: (path, opts) => request("GET", path, undefined, opts),
    post: (path, body) => request("POST", path, Object.assign({ by: "dashboard" }, body || {})),
    token, setToken, onAuthError,
  };
}

const def = createApi();
export const get = def.get;
export const post = def.post;
export const token = def.token;
export const setToken = def.setToken;
export const onAuthError = def.onAuthError;
