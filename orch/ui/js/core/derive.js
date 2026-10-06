// Pure functions over daemon data. No DOM, no clock reads: callers pass `now` (Unix seconds).
//
// Agent rows come from /api/list?all=1&tree=1 (see AgentRec.info() in orch/core.py): id status provider model owner
// parent depth children workspace workspace_name goal paths turns queued usage{input,output,cache_read} restarts caps
// created last_text effort effort_applied effort_warning, plus (from E2) last_event{ts,type} current_tool turn_t0
// pending_escalation. Board posts: id ts ws sender sender_kind kind text paths reply_to status.

export const QUIET_S = 120;
export const STUCK_S = 600;

/** Attention ranks, lowest first. */
export const RANK = { escalation: 0, stuck: 1, blocked: 2, busy: 3, idle: 4, dead: 5 };

const isBusy = (a) => a.status === 'busy' || a.status === 'starting';

/**
 * Silence on a busy agent: null (fine or not busy), 'quiet' (>= 120 s without an event) or 'stuck?' (>= 600 s).
 * Falls back to turn_t0 when last_event is missing; with neither it cannot judge and returns null.
 * @param {object} agent
 * @param {number} now  Unix seconds
 * @returns {null|'quiet'|'stuck?'}
 */
export function stuck(agent, now) {
  if (!agent || !isBusy(agent)) return null;
  const t = (agent.last_event && agent.last_event.ts) || agent.turn_t0;
  if (!Number.isFinite(t)) return null;
  const quiet = now - t;
  if (quiet >= STUCK_S) return 'stuck?';
  if (quiet >= QUIET_S) return 'quiet';
  return null;
}

/**
 * Agent id from a board sender ("agent:<aid>", or a bare aid when sender_kind is 'agent'); null for humans/daemon.
 * @param {{sender: string, sender_kind?: string}} post
 * @returns {string|null}
 */
export function senderAgent(post) {
  const s = String(post.sender || '');
  if (s.startsWith('agent:')) return s.slice(6) || null;
  return post.sender_kind === 'agent' ? s || null : null;
}

/**
 * Agents that are blocked according to the board: their latest post is a `blocked` announcement, or they have
 * an open question.
 * @param {object[]} posts  any order
 * @returns {Set<string>}
 */
export function blockedAgents(posts) {
  const latest = new Map();
  const asking = new Set();
  for (const p of posts || []) {
    const aid = senderAgent(p);
    if (!aid || p.kind === 'auto') continue;
    if (p.kind === 'question' && p.status === 'open') asking.add(aid);
    const cur = latest.get(aid);
    if (!cur || p.id > cur.id) latest.set(aid, p);
  }
  const out = new Set(asking);
  for (const [aid, p] of latest) if (p.kind === 'blocked') out.add(aid);
  return out;
}

/**
 * Agents with a pending escalation.
 * @param {object[]} escalations  /api/escalations rows {id, agent, state, ...}
 * @returns {Set<string>}
 */
export function escalatedAgents(escalations) {
  const out = new Set();
  for (const e of escalations || []) if (e.state === 'pending' && e.agent) out.add(e.agent);
  return out;
}

/**
 * Attention rank of one agent: escalation > stuck > blocked > busy > idle > dead (see RANK).
 * @param {object} agent
 * @param {{now: number, escalated?: Set<string>, blocked?: Set<string>}} ctx
 * @returns {number}
 */
export function attentionRank(agent, ctx) {
  if (agent.pending_escalation || (ctx.escalated && ctx.escalated.has(agent.id))) return RANK.escalation;
  if (agent.status === 'dead') return RANK.dead;
  if (stuck(agent, ctx.now) === 'stuck?') return RANK.stuck;
  if (ctx.blocked && ctx.blocked.has(agent.id)) return RANK.blocked;
  if (isBusy(agent)) return RANK.busy;
  return RANK.idle;
}

/**
 * Agents sorted by attention (stable: equal ranks keep their input order). Returns a new array.
 * @param {object[]} agents
 * @param {{now: number, escalated?: Set<string>, blocked?: Set<string>}} ctx
 * @returns {object[]}
 */
export function attention(agents, ctx) {
  return agents
    .map((a, i) => ({ a, i, r: attentionRank(a, ctx) }))
    .sort((x, y) => x.r - y.r || x.i - y.i)
    .map((x) => x.a);
}

/** input + output tokens (cache reads are not spend in the sense the header shows). */
export function agentTokens(agent) {
  const u = agent.usage || {};
  return (u.input || 0) + (u.output || 0);
}

/**
 * Per-workspace counts.
 * @param {object[]} agents
 * @param {Object<string, number>} [questions]  open question count per workspace id (from /api/board open_questions)
 * @returns {Object<string, {agents: number, live: number, busy: number, idle: number, dead: number, tokens: number, questions: number}>}
 *   keyed by workspace id ('' for agents without one)
 */
export function rollup(agents, questions = {}) {
  const out = {};
  const get = (ws) => out[ws] || (out[ws] = { agents: 0, live: 0, busy: 0, idle: 0, dead: 0, tokens: 0, questions: questions[ws] || 0 });
  for (const a of agents) {
    const r = get(a.workspace || '');
    r.agents++;
    if (a.status === 'dead') r.dead++;
    else { r.live++; if (isBusy(a)) r.busy++; else r.idle++; }
    r.tokens += agentTokens(a);
  }
  for (const ws of Object.keys(questions)) get(ws);
  return out;
}

const splitList = (s) => (s ? String(s).split(',').filter(Boolean) : null);

/**
 * Client-side filters from the URL. st/prov are comma lists; st also accepts 'live' (anything not dead).
 * q matches id, goal, model, provider, owner, workspace name and paths, case-insensitively.
 * @param {object[]} rows
 * @param {{st?: string, prov?: string, owner?: string, q?: string}} f
 * @returns {object[]} the same array when no filter is set
 */
export function filter(rows, f = {}) {
  const st = splitList(f.st);
  const prov = splitList(f.prov);
  const owner = f.owner || null;
  const q = f.q ? f.q.toLowerCase() : null;
  if (!st && !prov && !owner && !q) return rows;
  return rows.filter((a) => {
    if (st && !(st.includes(a.status) || (st.includes('live') && a.status !== 'dead'))) return false;
    if (prov && !prov.includes(a.provider)) return false;
    if (owner && a.owner !== owner) return false;
    if (q) {
      const hay = [a.id, a.goal, a.model, a.provider, a.owner, a.workspace_name, ...(a.paths || [])]
        .filter(Boolean).join('\n').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/**
 * Group rows by workspace, keeping input order inside each group and ordering groups by first appearance.
 * @param {object[]} rows
 * @param {object[]} [workspaces]  /api/workspaces rows {id, name, ...}; listed workspaces with no agents still appear, in that order
 * @returns {{id: string, name: string, workspace: object|null, agents: object[]}[]}
 */
export function byWorkspace(rows, workspaces = []) {
  const groups = new Map();
  for (const w of workspaces) groups.set(w.id, { id: w.id, name: w.name || w.id, workspace: w, agents: [] });
  for (const a of rows) {
    const id = a.workspace || '';
    let g = groups.get(id);
    if (!g) { g = { id, name: a.workspace_name || id || 'no workspace', workspace: null, agents: [] }; groups.set(id, g); }
    g.agents.push(a);
  }
  return [...groups.values()];
}

/**
 * Tree order for the rail gutter. Works on any subset (e.g. after filter): an agent whose parent is not in rows
 * becomes a root. Children keep their input order; roots keep theirs.
 * @param {object[]} rows
 * @returns {{agent: object, depth: number, last: boolean, hasChildren: boolean, ancestorsLast: boolean[]}[]}
 *   last: final child of its parent (draw ╰ instead of ├); ancestorsLast[d]: whether the ancestor at depth d was
 *   last, i.e. whether to stop drawing its vertical rail.
 */
export function tree(rows) {
  const ids = new Set(rows.map((a) => a.id));
  const kids = new Map();
  const roots = [];
  for (const a of rows) {
    if (a.parent && ids.has(a.parent) && a.parent !== a.id) {
      if (!kids.has(a.parent)) kids.set(a.parent, []);
      kids.get(a.parent).push(a);
    } else roots.push(a);
  }
  const out = [];
  const seen = new Set();
  const walk = (a, depth, last, ancestorsLast) => {
    if (seen.has(a.id)) return; // guards against parent cycles in bad data
    seen.add(a.id);
    const children = kids.get(a.id) || [];
    out.push({ agent: a, depth, last, hasChildren: children.length > 0, ancestorsLast });
    const next = [...ancestorsLast, last];
    children.forEach((c, i) => walk(c, depth + 1, i === children.length - 1, next));
  };
  roots.forEach((a, i) => walk(a, 0, i === roots.length - 1, []));
  return out;
}

/**
 * Header totals across the fleet.
 * @param {object[]} agents
 * @returns {{agents: number, live: number, busy: number, idle: number, dead: number, tokens: number}}
 */
export function totals(agents) {
  const t = { agents: 0, live: 0, busy: 0, idle: 0, dead: 0, tokens: 0 };
  for (const a of agents) {
    t.agents++;
    if (a.status === 'dead') t.dead++;
    else { t.live++; if (isBusy(a)) t.busy++; else t.idle++; }
    t.tokens += agentTokens(a);
  }
  return t;
}
