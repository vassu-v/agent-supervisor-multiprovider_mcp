// Keeps the shared store in step with the daemon: a full load at start (and on reset), then only the resources the
// change feed marks dirty. View-specific data (events, timeline, model lists, full escalation history) is fetched by
// the view that needs it, not here.

import { announce } from './a11y.js';
import { safeId } from './fmt.js';

const BOARD_N = 100;
const AUDIT_N = 200;

/**
 * @param {{get: Function, set: Function}} store
 * @param {{get: (path: string) => Promise<any>}} api  returns data, or {error, status} on failure
 * @returns {{ load: () => Promise<void>, onDirty: (d: object) => void, refresh: (what: string, ws?: string) => Promise<void> }}
 */
export function createSync(store, api) {
  const seenQuestions = new Set();
  const boardsLoaded = new Set();
  const boardSeq = new Map();
  let auditStale = true;

  const ok = (r) => r && !r.error;

  function markConn(r) {
    if (ok(r)) {
      const c = store.get().conn;
      if (c.state !== 'ok') store.set({ conn: { state: 'ok', error: '', at: Date.now() } });
      else store.set({ conn: { ...c, at: Date.now() } });
      return true;
    }
    if (r && r.aborted) return false;
    const state = r && r.status === 401 ? 'auth' : 'down';
    store.set({ conn: { state, error: (r && r.error) || 'no response', at: store.get().conn.at } });
    return false;
  }

  async function agents() {
    const r = await api.get('/api/list?all=1&tree=1');
    if (markConn(r) && Array.isArray(r)) store.set({ agents: r });
  }

  async function workspaces() {
    const r = await api.get('/api/workspaces');
    if (!markConn(r) || !Array.isArray(r)) return;
    store.set({ workspaces: r });
    // boards for workspaces we have not loaded yet
    await Promise.all(r.filter((w) => safeId(w.id) && !boardsLoaded.has(w.id)).map((w) => board(w.id)));
  }

  async function escalations() {
    const r = await api.get('/api/escalations');
    if (!markConn(r) || !Array.isArray(r)) return;
    store.set({ escalations: r });
    for (const e of r) {
      if (e.state !== 'pending') continue;
      const what = e.tool ? ` wants to run ${e.tool}` : ' needs a decision';
      announce(`Escalation: ${e.agent}${what}`, 'assertive', 'esc:' + e.id);
    }
  }

  async function providers() {
    const r = await api.get('/api/providers?models=0');
    if (markConn(r) && Array.isArray(r)) store.set({ providers: r });
  }

  async function board(ws) {
    if (!safeId(ws)) return;
    const seq = (boardSeq.get(ws) || 0) + 1;
    boardSeq.set(ws, seq);
    const r = await api.get('/api/board?ws=' + encodeURIComponent(ws) + '&n=' + BOARD_N);
    if (boardSeq.get(ws) !== seq) return; // a newer fetch for this board is in flight or done
    if (!markConn(r) || !r || !Array.isArray(r.posts)) return;
    const first = !boardsLoaded.has(ws);
    boardsLoaded.add(ws);
    // questions already open at first load are not news; only later ones are announced
    for (const p of r.posts) {
      if (p.kind !== 'question' || p.status !== 'open' || seenQuestions.has(p.id)) continue;
      seenQuestions.add(p.id);
      if (!first) announce(`New question from ${p.sender}`, 'polite', 'q:' + p.id);
    }
    store.set((s) => ({ boards: { ...s.boards, [ws]: r } }));
  }

  async function audit() {
    // only worth fetching while someone looks at it; otherwise remember that it is stale
    if (store.get().ui.route.view !== 'audit') { auditStale = true; return; }
    auditStale = false;
    const r = await api.get('/api/audit?n=' + AUDIT_N);
    if (markConn(r) && Array.isArray(r)) store.set({ audit: r });
  }

  async function load() {
    boardsLoaded.clear();
    await Promise.all([agents(), workspaces(), escalations(), providers(), audit()]);
  }

  function onDirty(d) {
    if (!d || d.reset) { load(); return; }
    if (d.agents && d.agents.length) agents();
    if (d.workspaces) workspaces();
    if (d.escalations) escalations();
    if (d.providers) providers();
    if (d.audit) audit();
    for (const ws of d.boards || []) board(ws);
  }

  /**
   * Refetch one resource now (after a write, or when a view opens).
   * @param {'agents'|'workspaces'|'escalations'|'providers'|'board'|'audit'} what
   * @param {string} [ws]  for 'board'
   */
  function refresh(what, ws) {
    switch (what) {
      case 'agents': return agents();
      case 'workspaces': return workspaces();
      case 'escalations': return escalations();
      case 'providers': return providers();
      case 'board': return board(ws);
      case 'audit': return auditStale || store.get().ui.route.view === 'audit' ? audit() : Promise.resolve();
      default: return Promise.resolve();
    }
  }

  return { load, onDirty, refresh };
}
