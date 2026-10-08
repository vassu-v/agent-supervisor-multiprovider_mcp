// Long-poll change feed. start(onDirty, onState?) (onState(up:boolean, reply) on the first failure / first success after) calls onDirty({reset, agents:[], boards:[], escalations, audit, providers, workspaces})
// whenever something changed. One pending request when idle; backs off 1s..15s on errors; pauses while the tab is hidden;
// coalesces so the "list" group fires at most every 500 ms and boards at most every 1 s.
import * as defaultApi from "./api.js";

export const MIN = { list: 500, board: 1000, events: 250 };   // minimum refetch intervals in ms (events is for the drawer)
const FLAGS = ["escalations", "audit", "providers", "workspaces"];

const empty = () => ({ reset: false, agents: [], boards: [], escalations: false, audit: false, providers: false, workspaces: false });

export function createChanges(env = {}) {
  const api = env.api || defaultApi;
  const doc = env.document !== undefined ? env.document : globalThis.document;
  const st = env.setTimeout || globalThis.setTimeout.bind(globalThis);
  const ct = env.clearTimeout || globalThis.clearTimeout.bind(globalThis);
  const nowFn = env.now || (() => Date.now());
  const minList = env.minList != null ? env.minList : MIN.list;
  const minBoard = env.minBoard != null ? env.minBoard : MIN.board;
  const bo = env.backoff || { min: 1000, max: 15000 };
  const wait = env.wait != null ? env.wait : 25;

  let gen = 0, running = false, rev = null, cb = null, stCb = null, failed = false, ctl = null, visWake = null, stopSleep = null;
  let pendList = empty(), pendBoard = [], lastList = 0, lastBoard = 0, tList = null, tBoard = null;

  const hidden = () => !!(doc && doc.hidden);
  const sleep = (ms) => new Promise((res) => { const t = st(res, ms); stopSleep = () => { ct(t); res(); }; });

  function mergeInto(p, r) {
    if (r.reset) p.reset = true;
    for (const a of r.agents || []) if (!p.agents.includes(a)) p.agents.push(a);
    for (const f of FLAGS) if (r[f]) p[f] = true;
  }
  const listDirty = (p) => p.reset || p.agents.length || FLAGS.some((f) => p[f]);

  function fireList() {
    tList = null;
    if (!listDirty(pendList) || !cb) return;
    const out = pendList; pendList = empty(); lastList = nowFn();
    out.boards = out.reset ? ["*"] : [];
    try { cb(out); } catch (e) { /* listener errors must not stop the feed */ }
  }
  function fireBoard() {
    tBoard = null;
    if (!pendBoard.length || !cb) return;
    const out = empty(); out.boards = pendBoard; pendBoard = []; lastBoard = nowFn();
    try { cb(out); } catch (e) { /* ignore */ }
  }
  function schedule(r) {
    mergeInto(pendList, r);
    if (r.reset) pendBoard = [];
    else for (const b of r.boards || []) if (!pendBoard.includes(b)) pendBoard.push(b);
    if (listDirty(pendList) && tList === null) tList = st(fireList, Math.max(0, lastList + minList - nowFn()));
    if (pendBoard.length && tBoard === null) tBoard = st(fireBoard, Math.max(0, lastBoard + minBoard - nowFn()));
  }

  function onVis() {
    if (!hidden()) { if (visWake) { const w = visWake; visWake = null; w(); } return; }
    if (ctl) { try { ctl.abort(); } catch (e) { /* ignore */ } }      // stop the pending poll while hidden
  }

  async function loop(my) {
    let delay = bo.min;
    while (my === gen) {
      if (hidden()) {
        await new Promise((res) => { visWake = res; });
        continue;
      }
      ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const q = (rev === null ? "" : "since=" + rev + "&") + "wait=" + wait;
      const r = await api.get("/api/changes?" + q, ctl ? { signal: ctl.signal } : undefined);
      if (my !== gen) return;
      if (r && r.error) {
        if (r.aborted) continue;
        if (r.status === 401) { running = false; return; }          // api.js already told the auth listeners
        if (!failed) { failed = true; state(false, r); }
        await sleep(delay);
        delay = Math.min(bo.max, delay * 2);
        continue;
      }
      delay = bo.min;
      if (failed) { failed = false; if (r) r.reset = true; state(true, r); }   // back after an outage: reload everything
      if (!r || typeof r.rev !== "number") continue;
      if (rev === null && !r.reset) r.reset = true;
      rev = r.rev;
      if (r.reset || (r.agents && r.agents.length) || (r.boards && r.boards.length) || FLAGS.some((f) => r[f])) schedule(r);
    }
  }

  function state(up, r) { if (stCb) { try { stCb(up, r); } catch (e) { /* ignore */ } } }
  function start(onDirty, onState) {
    if (running) stop();
    running = true; cb = onDirty; stCb = onState || null; failed = false; rev = null; gen += 1;
    if (doc && doc.addEventListener) doc.addEventListener("visibilitychange", onVis);
    loop(gen);
  }
  function stop() {
    running = false; gen += 1; cb = null; stCb = null;
    if (ctl) { try { ctl.abort(); } catch (e) { /* ignore */ } }
    if (tList !== null) { ct(tList); tList = null; }
    if (tBoard !== null) { ct(tBoard); tBoard = null; }
    pendList = empty(); pendBoard = [];
    if (doc && doc.removeEventListener) doc.removeEventListener("visibilitychange", onVis);
    if (stopSleep) stopSleep();
    if (visWake) { const w = visWake; visWake = null; w(); }
  }
  return { start, stop, _rev: () => rev };
}

const def = createChanges();
export const start = def.start;
export const stop = def.stop;
