// Pure maths for the models view (#/models). No DOM, no network, no clock
// reads: callers pass timestamps in. Tested in tests/ui/test_models.mjs.

export const REFRESH_MS = 30000;
export const MODELS_URL = '/api/models?limit=500';

/** input + output tokens of one usage record (cache reads are not spend). */
export function agentTokens(usage) {
  const u = usage || {};
  return (Number(u.input) || 0) + (Number(u.output) || 0);
}

/**
 * Per-model rollup over agent rows: [{provider, model, key, agents, tokens}].
 * Sorted by agent count, then tokens, then provider/model.
 */
export function modelRollup(agents) {
  const by = new Map();
  for (const a of agents || []) {
    const provider = a && a.provider ? String(a.provider) : '';
    const model = a && a.model ? String(a.model) : '';
    const key = provider + '' + model;
    let r = by.get(key);
    if (!r) {
      r = { provider, model, key, agents: 0, tokens: 0 };
      by.set(key, r);
    }
    r.agents += 1;
    r.tokens += agentTokens(a && a.usage);
  }
  const lt = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  return [...by.values()].sort((x, y) =>
    (y.agents - x.agents) || (y.tokens - x.tokens) ||
    lt(x.provider, y.provider) || lt(x.model, y.model));
}

const capWord = (v) => (v === true ? 'yes' : v === false ? 'no' : String(v));

/**
 * Adapter capability matrix: {cols: [provider names],
 * rows: [{cap, cells: [{name, display}]}]}. Union of cap keys in first-seen
 * order; unknown cells render as an em dash.
 */
export function capsTable(providers) {
  const rows = providers || [];
  const cols = [];
  for (const p of rows) {
    if (p && typeof p.name === 'string' && !cols.includes(p.name)) cols.push(p.name);
  }
  const caps = [];
  for (const p of rows) {
    const c = (p && p.caps) || {};
    for (const k of Object.keys(c)) if (!caps.includes(k)) caps.push(k);
  }
  const byName = new Map(rows.map((p) => [p && p.name, p]));
  return {
    cols,
    rows: caps.map((cap) => ({
      cap,
      cells: cols.map((n) => {
        const c = ((byName.get(n) || {}).caps) || {};
        const v = Object.prototype.hasOwnProperty.call(c, cap) ? c[cap] : null;
        return { name: n, display: v === null || v === undefined ? '—' : capWord(v) };
      }),
    })),
  };
}

/** True when a models fetch may start (first load, or 30 s since the last). */
export function shouldRefresh(lastFetchMs, nowMs) {
  if (!lastFetchMs) return true;
  return (nowMs - lastFetchMs) >= REFRESH_MS;
}

/** Body for POST /api/provider that flips one provider switch. */
export function toggleBody(name, enabledNow) {
  return { name, enabled: !enabledNow };
}
