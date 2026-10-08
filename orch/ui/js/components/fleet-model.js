// Fleet view-model: filtering, attention order, tree layout, badges, effort
// pips, sparks, yard grouping. DOM-free for node tests. Time passed as now.

import {
  attention, attentionRank, blockedAgents, escalatedAgents, stuck,
  filter, byWorkspace, tree, agentTokens, RANK,
} from '../core/derive.js';
import { ago } from '../core/fmt.js';

export const SCALE_ABOVE = 24; // live agents above which quiet yards collapse
export const FOLD_ABOVE = 8; // yard agents above which dead fold into "N finished"
export const MAX_BARS = 12; // spark bars per row, newest last
export const MAX_PATHS = 3; // path chips per row
export const ANN_KINDS = ['done', 'blocked', 'changed', 'handoff', 'started', 'info'];
export const EFFORT_PIPS = { low: 1, medium: 2, high: 3 };
export const EFFORT_WORD = { low: 'low', medium: 'med', high: 'high' };

const isLive = (a) => a.status !== 'dead';
const isBusy = (a) => a.status === 'busy' || a.status === 'starting';

// 0-3 pips from effort_applied; never reads model ids
export function effortPips(applied) {
  return EFFORT_PIPS[applied] || 0;
}

// chip word; 'free' without effort
export function effortWord(applied) {
  if (!applied) return 'free';
  return EFFORT_WORD[applied] || String(applied);
}

// per-turn tokens, newest last; null = unknown
export function sparkValues(agent) {
  if (Array.isArray(agent.turn_usage)) return agent.turn_usage.slice(-MAX_BARS);
  if (Array.isArray(agent.turns_full)) {
    const out = [];
    for (const t of agent.turns_full) {
      const u = t && t.usage;
      if (u) out.push((u.input || 0) + (u.output || 0));
    }
    if (out.length) return out.slice(-MAX_BARS);
  }
  return null;
}

// quiet, stuck?, restarted, needs review
export function badgesFor(agent, now) {
  const out = [];
  const q = stuck(agent, now);
  if (q === 'quiet') out.push({ label: 'quiet', cls: 'badge warn' });
  else if (q === 'stuck?') out.push({ label: 'stuck?', cls: 'badge danger' });
  if ((agent.restarts || 0) > 0) out.push({ label: `restarted ${agent.restarts}`, cls: 'badge' });
  if (agent.needs_review) out.push({ label: 'needs review', cls: 'badge' });
  return out;
}

// platform + status pill
export function statusFor(agent, held, heldTool, now) {
  if (agent.status === 'dead') {
    return {
      platform: 'dead', pill: 'st dead', glyph: 's-dead',
      word: agent.stopped_by ? 'stopped' : 'done',
    };
  }
  if (held) {
    return {
      platform: 'wait', pill: 'st wait', glyph: 's-wait',
      word: heldTool ? `held \u00B7 ${heldTool}` : 'held',
    };
  }
  if (agent.status === 'starting') {
    return { platform: 'busy', pill: 'st busy', glyph: 's-busy', word: 'starting' };
  }
  if (isBusy(agent)) {
    const t = (agent.turn_t0) || (agent.last_event && agent.last_event.ts);
    const el = t ? ago(t, now) : '';
    return { platform: 'busy', pill: 'st busy', glyph: 's-busy', word: el ? `busy ${el}` : 'busy' };
  }
  return { platform: 'idle', pill: 'st idle', glyph: 's-idle', word: 'idle' };
}

// agent ids with an open question
export function openQuestions(posts) {
  const out = new Set();
  for (const p of posts || []) {
    if (p.kind !== 'question' || p.status !== 'open') continue;
    const s = String(p.sender || '');
    const aid = s.startsWith('agent:') ? s.slice(6) : (p.sender_kind === 'agent' ? s : '');
    if (aid) out.add(aid);
  }
  return out;
}

const newest = (posts, ws, ok, n = 5) =>
  (posts || []).filter((p) => p.ws === ws && ok(p)).sort((a, b) => a.id - b.id).slice(-n).reverse();

/** Whole fleet snapshot for one render. */
export function buildFleet(s) {
  const agents = s.agents || [];
  const params = s.params || {};
  const now = s.now || 0;
  const openYards = s.openYards || new Set();
  const openDead = s.openDead || new Set();

  const rows = filter(agents, { st: params.st, prov: params.prov, owner: params.owner, q: params.q });
  const pending = (s.escalations || []).filter((e) => e.state === 'pending');
  const escSet = escalatedAgents(pending);
  const escTool = new Map(pending.filter((e) => e.tool).map((e) => [e.agent, e.tool]));
  const posts = [];
  for (const ws of Object.keys(s.boards || {})) {
    const b = s.boards[ws];
    if (b && Array.isArray(b.posts)) posts.push(...b.posts);
  }
  const blocked = blockedAgents(posts);
  const ctx = { now, escalated: escSet, blocked };
  const asked = openQuestions(posts);

  const deadWanted = String(params.st || '').split(',').includes('dead');
  const only = params.ws && params.ws !== 'all' ? params.ws : null;
  const groups = byWorkspace(rows, s.workspaces || []).filter((g) => g.agents.length > 0 && (!only || g.id === only));
  const totalLive = rows.filter(isLive).length;
  const scale = totalLive > SCALE_ABOVE;

  const yards = groups.map((g) => {
    const ordered = attention(g.agents, ctx);
    const nodes = tree(ordered);
    const dead = nodes.filter((n) => n.agent.status === 'dead');
    const canFold = g.agents.length > FOLD_ABOVE && dead.length > 0 && !deadWanted;
    const folded = canFold && !openDead.has(g.id);
    // re-derive: orphaned children become roots
    const shown = folded ? tree(ordered.filter((a) => a.status !== 'dead')) : nodes;
    let tokens = 0;
    let busy = 0;
    let live = 0;
    for (const a of g.agents) {
      tokens += agentTokens(a);
      if (a.status === 'dead') continue;
      live++;
      if (isBusy(a)) busy++;
    }
    const waiting = pending.filter((e) => e.workspace === g.id || g.agents.some((a) => a.id === e.agent)).length;
    const board = s.boards && s.boards[g.id];
    const questions = board && typeof board.open_questions === 'number' ? board.open_questions : 0;
    const hasAttention = waiting > 0 || questions > 0
      || g.agents.some((a) => attentionRank(a, ctx) <= RANK.busy);
    const collapsed = scale && !hasAttention && !openYards.has(g.id);
    return {
      id: g.id,
      name: g.name,
      root: (g.workspace && g.workspace.root) || '',
      branch: (g.workspace && g.workspace.branch) || null,
      sessions: (g.workspace && g.workspace.sessions) || [],
      agents: g.agents.length,
      live,
      busy,
      waiting,
      questions,
      tokens,
      collapsed,
      folded,
      canFold,
      deadCount: dead.length,
      maxDepth: shown.reduce((m, n) => Math.max(m, n.depth || 0), 0),
      board: newest(posts, g.id, (p) => ANN_KINDS.includes(p.kind)),
      qPosts: newest(posts, g.id, (p) => p.kind === 'question' && p.status === 'open'),
      rows: shown.map((n) => {
        const a = n.agent;
        const held = escSet.has(a.id) || !!a.pending_escalation;
        const tool = escTool.get(a.id) || null;
        const st = statusFor(a, held, tool, now);
        const tl = a.current_tool;
        const last = a.last_text || '';
        return {
          agent: a,
          depth: n.depth,
          last: n.last,
          hasChildren: n.hasChildren,
          cont: n.ancestorsLast.map((wasLast, i) => (wasLast ? -1 : i)).filter((i) => i >= 0),
          rank: attentionRank(a, ctx),
          ...st,
          badges: badgesFor(a, now),
          pips: effortPips(a.effort_applied),
          effortWord: effortWord(a.effort_applied),
          effortWarn: a.effort_warning || null,
          spark: sparkValues(a),
          toolLine: tl && last ? `${tl} \u00B7 ${last}` : (last || tl || null),
          question: asked.has(a.id),
          paths: (a.paths || []).slice(0, MAX_PATHS),
        };
      }),
    };
  });

  const liveByWs = new Map();
  for (const a of agents) {
    if (!isLive(a)) continue;
    const k = a.workspace || '';
    liveByWs.set(k, (liveByWs.get(k) || 0) + 1);
  }
  const tabs = [
    { id: 'all', name: 'All', count: agents.filter(isLive).length },
    ...(s.workspaces || []).map((w) => ({ id: w.id, name: w.name || w.id, count: liveByWs.get(w.id) || 0 })),
  ];

  return { tabs, yards, scale, totalLive, empty: yards.length === 0, selected: params.agent || null };
}
