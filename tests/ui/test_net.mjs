// node --test tests/ui/test_net.mjs : orch/ui/js/net/{api,changes}.js with injected fetch/storage/location/document.
import test from "node:test";
import assert from "node:assert/strict";
import { createApi } from "../../orch/ui/js/net/api.js";
import { createChanges } from "../../orch/ui/js/net/changes.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const resp = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
const mem = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m }; };

function env(extra = {}) {
  const calls = [];
  const e = {
    storage: mem(),
    location: { hash: "", pathname: "/ui/", search: "?x=1" },
    history: { replaceState: (...a) => calls.push(a) },
    fetch: async () => resp(200, {}),
    ...extra,
  };
  return { e, calls };
}

test("token: fragment is moved to sessionStorage and stripped", () => {
  const { e, calls } = env({ location: { hash: "#t=abc123", pathname: "/ui/", search: "?x=1" } });
  const api = createApi(e);
  assert.equal(api.token(), "abc123");
  assert.equal(e.storage.getItem("switchyard.token"), "abc123");
  assert.deepEqual(calls[0], [null, "", "/ui/?x=1"]);
});

test("token: falls back to storage, then null; setToken persists; storage errors are tolerated", () => {
  const a = env(); a.e.storage.setItem("switchyard.token", "stored");
  assert.equal(createApi(a.e).token(), "stored");
  assert.equal(createApi(env().e).token(), null);
  const b = env(); const api = createApi(b.e); api.setToken("zzz");
  assert.equal(api.token(), "zzz"); assert.equal(b.e.storage.getItem("switchyard.token"), "zzz");
  api.setToken(null); assert.equal(b.e.storage.getItem("switchyard.token"), null);
  const bad = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  const c = env({ storage: bad, location: { hash: "#t=q", pathname: "/", search: "" }, history: { replaceState() { throw new Error("x"); } } });
  const api2 = createApi(c.e);
  assert.equal(api2.token(), "q"); api2.setToken("r"); assert.equal(api2.token(), "r");
});

test("get/post: bearer header, JSON in/out, by=dashboard", async () => {
  const seen = [];
  const { e } = env({ location: { hash: "#t=tok", pathname: "/ui/", search: "" }, fetch: async (p, i) => { seen.push([p, i]); return resp(200, { ok: 1 }); } });
  const api = createApi(e);
  assert.deepEqual(await api.get("/api/list?all=1"), { ok: 1 });
  assert.equal(seen[0][0], "/api/list?all=1");
  assert.equal(seen[0][1].method, "GET");
  assert.equal(seen[0][1].headers.Authorization, "Bearer tok");
  assert.equal(seen[0][1].body, undefined);
  await api.post("/api/send", { id: "a1", msg: "hi" });
  assert.equal(seen[1][1].method, "POST");
  assert.equal(seen[1][1].headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(seen[1][1].body), { by: "dashboard", id: "a1", msg: "hi" });
});

test("no token: no Authorization header", async () => {
  let h;
  const { e } = env({ fetch: async (p, i) => { h = i.headers; return resp(200, []); } });
  assert.deepEqual(await createApi(e).get("/api/health"), []);
  assert.equal(h.Authorization, undefined);
});

test("errors become {error,status}; 401 calls auth listeners; network failure is status 0", async () => {
  let n = 0;
  const { e } = env({ fetch: async () => { n++; if (n === 1) return resp(400, { error: "bad thing", kind: "ValueError" }); if (n === 2) return resp(401, { error: "bad or missing token" }); if (n === 3) return resp(500, null); if (n === 4) return resp(200, { error: "soft" }); throw new Error("down"); } });
  const api = createApi(e);
  let auth = 0; const off = api.onAuthError(() => { auth++; });
  assert.deepEqual(await api.get("/x"), { error: "bad thing", status: 400 });
  assert.deepEqual(await api.get("/x"), { error: "bad or missing token", status: 401 });
  assert.equal(auth, 1);
  assert.deepEqual(await api.post("/x", {}), { error: "HTTP 500", status: 500 });
  assert.deepEqual(await api.get("/x"), { error: "soft", status: 200 });
  const r = await api.get("/x");
  assert.equal(r.status, 0); assert.match(r.error, /down/);
  off(); n = 1; // 401 again with the listener removed
  await api.get("/x"); assert.equal(auth, 1);
});

// ------------------------------------------------------------------ changes
function feed(replies, extra = {}) {
  const urls = [];
  let i = 0;
  const api = { get: async (u) => { urls.push(u); const r = replies[Math.min(i++, replies.length - 1)]; if (typeof r === "function") return r(); await sleep(2); return r; } };
  return { api, urls };
}
const R = (rev, o = {}) => ({ rev, reset: false, agents: [], boards: [], escalations: false, audit: false, providers: false, workspaces: false, ...o });
const fast = { backoff: { min: 5, max: 20 }, minList: 30, minBoard: 60 };

test("changes: first poll has no since and counts as reset; then since=rev", async () => {
  const { api, urls } = feed([R(5), R(5), R(6, { agents: ["a1"] }), R(6)]);
  const got = [];
  const c = createChanges({ api, document: null, ...fast });
  c.start((d) => got.push(d));
  await sleep(150); c.stop();
  assert.equal(urls[0], "/api/changes?wait=25");
  assert.equal(urls[1], "/api/changes?since=5&wait=25");
  assert.equal(urls[2], "/api/changes?since=5&wait=25");
  assert.equal(urls[3], "/api/changes?since=6&wait=25");
  assert.equal(got[0].reset, true);
  assert.deepEqual(got[0].boards, ["*"]);
  assert.deepEqual(got[1].agents, ["a1"]);
  assert.equal(got[1].reset, false);
});

test("changes: server reset flag passes through; empty replies do not fire", async () => {
  const { api } = feed([R(3), R(3), R(1, { reset: true }), R(1)]);
  const got = [];
  const c = createChanges({ api, document: null, ...fast });
  c.start((d) => got.push(d));
  await sleep(150); c.stop();
  assert.equal(got.length, 2);
  assert.equal(got[1].reset, true);
});

test("changes: backoff on error grows 1s..15s (scaled) and recovers", async () => {
  const stamps = [];
  const replies = [() => { stamps.push(Date.now()); return { error: "x", status: 500 }; }, () => { stamps.push(Date.now()); return { error: "x", status: 500 }; },
    () => { stamps.push(Date.now()); return { error: "x", status: 429 }; }, () => { stamps.push(Date.now()); return { error: "x", status: 0 }; }, R(9, { audit: true }), R(9)];
  const { api } = feed(replies);
  const got = [];
  const c = createChanges({ api, document: null, backoff: { min: 20, max: 60 }, minList: 5, minBoard: 5 });
  c.start((d) => got.push(d));
  await sleep(400); c.stop();
  const gaps = stamps.slice(1).map((t, i) => t - stamps[i]);
  assert.ok(gaps[0] >= 18 && gaps[1] >= 38 && gaps[2] >= 55, JSON.stringify(gaps));
  assert.ok(gaps[2] < 120, JSON.stringify(gaps));
  assert.equal(got.length, 1);
  assert.equal(got[0].audit, true);
});

test("changes: 401 stops the loop", async () => {
  let n = 0;
  const api = { get: async () => { n++; return { error: "no", status: 401 }; } };
  const c = createChanges({ api, document: null, ...fast });
  c.start(() => {});
  await sleep(100); c.stop();
  assert.equal(n, 1);
});

test("changes: paused while the document is hidden, resumes on visibilitychange", async () => {
  const listeners = [];
  const doc = { hidden: true, addEventListener: (t, f) => listeners.push(f), removeEventListener() {} };
  const { api, urls } = feed([R(1)]);
  const c = createChanges({ api, document: doc, ...fast });
  c.start(() => {});
  await sleep(60);
  assert.equal(urls.length, 0);
  doc.hidden = false; listeners.forEach((f) => f());
  await sleep(40); c.stop();
  assert.ok(urls.length >= 1);
});

test("changes: list group and boards are throttled and merged", async () => {
  const replies = [R(1), R(2, { agents: ["a1"] }), R(3, { agents: ["a2"], boards: ["w1"] }), R(4, { agents: ["a1"], boards: ["w2"] }), R(4)];
  const { api } = feed(replies);
  const got = []; const t = [];
  const c = createChanges({ api, document: null, backoff: { min: 5, max: 20 }, minList: 80, minBoard: 120 });
  c.start((d) => { got.push(d); t.push(Date.now()); });
  await sleep(500); c.stop();
  const list = got.filter((d) => d.agents.length || d.reset);
  assert.equal(list[0].reset, true);
  const merged = list.slice(1).flatMap((d) => d.agents);
  assert.deepEqual([...new Set(merged)].sort(), ["a1", "a2"]);
  assert.ok(list.length <= 3);
  const boards = got.flatMap((d) => (d.reset ? [] : d.boards)).sort();
  assert.deepEqual(boards, ["w1", "w2"]);
});

test("changes: stop() ends the loop (no further requests)", async () => {
  const { api, urls } = feed([R(1)]);
  const c = createChanges({ api, document: null, ...fast });
  c.start(() => {});
  await sleep(40); c.stop();
  const n = urls.length; await sleep(60);
  assert.equal(urls.length, n);
});
