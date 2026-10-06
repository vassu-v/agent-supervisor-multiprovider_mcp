import { h, text, list } from '../core/h.js';
import { layoutLane, layoutPosts } from './timeline-layout.js';
export function table() {
  const body = h('tbody');
  const wrap = h('div', { hidden: true }, h('table', null,
    h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'lane'), h('th', { scope: 'col' }, 'turns'),
      h('th', { scope: 'col' }, 'tools'), h('th', { scope: 'col' }, 'blocked'), h('th', { scope: 'col' }, 'esc'))), body));
  return { wrap, body };
}
// Counts come from the raw layout, before bucketing merges marks into 'bucket' kinds.
function rawLanes(data, from, to) {
  const items = (data && data.items) || [];
  const lanes = ((data && data.agents) || []).map((a) => ({ key: 'a:' + a.id, title: a.id, marks: layoutLane(a.id, items, from, to) }));
  const posts = layoutPosts(items, from, to);
  if (posts.length) lanes.push({ key: 'posts', title: 'board', marks: posts });
  return lanes;
}
export function grid(body, data, from, to) {
  list(body, rawLanes(data, from, to).map((l) => {
    const c = [0, 0, 0, 0];
    for (const m of l.marks) {
      if (m.kind === 'turn') c[0]++;
      else if (m.kind === 'tool') c[1]++;
      else if (m.kind === 'guard') c[2]++;
      else if (m.kind === 'esc') c[3]++;
    }
    return { key: l.key, id: l.title, c };
  }), (r) => r.key,
    (r) => h('tr', null, h('th', { scope: 'row' }, r.id),
      h('td', null, r.c[0]), h('td', null, r.c[1]), h('td', null, r.c[2]), h('td', null, r.c[3])),
    (tr, r) => {
      text(tr.querySelector('th'), r.id);
      const tds = tr.querySelectorAll('td');
      r.c.forEach((n, i) => text(tds[i], n));
    });
}
