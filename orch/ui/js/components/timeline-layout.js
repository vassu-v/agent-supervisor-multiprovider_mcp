export const MAX_MARKS = 2000;
const pc = (f) => (f * 100).toFixed(3) + '%';
export function frac(t, a, b) {
  if (!(b > a)) return 0;
  const f = (t - a) / (b - a);
  return f < 0 ? 0 : f > 1 ? 1 : f;
}
export function layoutLane(aid, items, from, to) {
  const out = [];
  let n = 0;
  for (const it of items || []) {
    if (!it || it.a !== aid) continue;
    if (it.k === 'turn') {
      const open = it.t1 === null || it.t1 === undefined;
      const t1 = open ? to : it.t1;
      const bad = it.ok === false;
      out.push({ key: 'turn:' + it.t0 + ':' + (open ? 'o' : it.t1) + ':' + (n++), kind: 'turn',
        x0: frac(it.t0, from, to), x1: frac(t1, from, to), cls: bad ? 'mk mk-busy mk-err' : 'mk mk-busy',
        label: 'turn ' + Math.max(0, Math.round(t1 - it.t0)) + 's' + (bad ? ', failed' : it.ok ? ', ok' : '') + (it.int ? ', interrupted' : '') });
    } else if (it.k === 'tool') {
      const x = frac(it.t0, from, to);
      out.push({ key: 'tool:' + it.t0 + ':' + (it.tool || '') + ':' + (n++), kind: 'tool', x0: x, x1: x,
        cls: it.ok === false ? 'mk mk-tool mk-err' : 'mk mk-tool', label: 'tool ' + (it.tool || '?') + (it.ok === false ? ', failed' : '') });
    } else if (it.k === 'guard' || it.k === 'esc') {
      const x = frac(it.t, from, to);
      const err = it.k === 'guard' || it.state === 'pending';
      out.push({ key: it.k + ':' + (it.k === 'esc' ? it.id : it.t + ':' + (n++)), kind: it.k, x0: x, x1: x,
        cls: 'mk mk-' + it.k + (err ? ' mk-err' : ''), label: it.k === 'esc' ? 'escalation ' + it.id + ', ' + it.state : 'blocked by guard' });
    }
  }
  return out;
}
export function layoutPosts(items, from, to) {
  const out = [];
  for (const it of items || []) {
    if (!it || it.k !== 'post') continue;
    const x = frac(it.t, from, to);
    out.push({ key: 'post:' + it.id, kind: 'post', x0: x, x1: x, cls: 'mk mk-post', label: (it.kind || 'post') + ' #' + it.id });
  }
  return out;
}
export function bucket(marks, max = MAX_MARKS) {
  if (!Number.isFinite(max) || max < 1) max = MAX_MARKS;
  if (marks.length <= max) return { marks, truncated: false };
  const slots = new Map();
  for (const m of marks) {
    const i = Math.min(max - 1, Math.floor(m.x0 * max));
    let s = slots.get(i);
    if (!s) { s = { key: 'b' + i, kind: 'bucket', x0: m.x0, x1: m.x1, count: 0, cls: 'mk mk-bucket' }; slots.set(i, s); }
    if (m.x0 < s.x0) s.x0 = m.x0;
    if (m.x1 > s.x1) s.x1 = m.x1;
    s.count++;
  }
  const out = [...slots.values()].sort((a, b) => a.x0 - b.x0).map((s) => ({ ...s, label: s.count + ' events' }));
  return { marks: out, truncated: true };
}
export function lanesFor(data, from, to) {
  const agents = (data && data.agents) || [];
  const items = (data && data.items) || [];
  const posts = layoutPosts(items, from, to);
  const share = Math.max(1, Math.floor(MAX_MARKS / Math.max(1, agents.length + (posts.length ? 1 : 0))));
  const lanes = agents.map((a) => {
    const b = bucket(layoutLane(a.id, items, from, to), share);
    return { key: 'a:' + a.id, id: a.id, title: a.id, marks: b.marks, truncated: b.truncated };
  });
  if (posts.length) {
    const b = bucket(posts, share);
    lanes.push({ key: 'posts', id: 'posts', title: 'board', marks: b.marks, truncated: b.truncated });
  }
  return lanes;
}
export { pc };
