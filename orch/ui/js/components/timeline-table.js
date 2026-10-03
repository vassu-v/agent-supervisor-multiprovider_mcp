import { h, list } from '../core/h.js';
import { lanesFor } from './timeline-layout.js';
export function table() {
  const body = h('tbody');
  const wrap = h('div', { hidden: true }, h('table', null,
    h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'lane'), h('th', { scope: 'col' }, 'turns'),
      h('th', { scope: 'col' }, 'tools'), h('th', { scope: 'col' }, 'blocked'), h('th', { scope: 'col' }, 'esc'))), body));
  return { wrap, body };
}
export function grid(body, data, from, to) {
  list(body, lanesFor(data, from, to).map((l) => {
    const c = [0, 0, 0, 0];
    for (const m of l.marks) {
      if (m.kind === 'bucket') c[1] += m.count || 0;
      else if (m.kind === 'turn') c[0]++;
      else if (m.kind === 'tool') c[1]++;
      else if (m.kind === 'guard') c[2]++;
      else if (m.kind === 'esc') c[3]++;
    }
    return { key: l.title + c.join(','), id: l.title, c };
  }), (r) => r.key,
    (r) => h('tr', null, h('th', { scope: 'row' }, r.id),
      h('td', null, r.c[0]), h('td', null, r.c[1]), h('td', null, r.c[2]), h('td', null, r.c[3])));
}
