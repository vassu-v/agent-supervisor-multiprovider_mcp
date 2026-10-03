// Rail gutter geometry: the tree drawn as track. Coordinates live in a fixed
// 48-unit space; the svg stretches to the row height via CSS (no layout reads).
// railShapes() is pure (tested); makeRail() wraps it in an svg via h().

import { h } from '../core/h.js';

export const RAIL_H = 48;
export const PITCH = 18;
export const X0 = 18;
export const MAX_DEPTH = 3;
const Y = 20; // platform height (matches the 48px row centre)

export const xFor = (d) => X0 + Math.max(0, Math.min(MAX_DEPTH, d)) * PITCH;

export function railWidth(maxDepth) {
  return xFor(maxDepth) + 30; // gutter is at most 102px
}

/**
 * @param {{depth: number, last: boolean, hasChildren: boolean, cont: number[], platform: 'busy'|'idle'|'dead'|'wait', question: boolean}} vm
 * @param {number} W  gutter width from railWidth()
 * @returns {[string, object][]} svg child tag + attrs
 */
export function railShapes(vm, W) {
  const out = [];
  const lines = [];
  const d = Math.max(0, Math.min(MAX_DEPTH, vm.depth || 0));
  const xd = xFor(d);
  const end = W - 3;
  for (const k of vm.cont || []) lines.push(`M${xFor(k)} 0V${RAIL_H}`);
  if (d > 0) {
    const px = xFor(d - 1);
    lines.push(vm.last ? `M${px} 0V${Y - 8}` : `M${px} 0V${RAIL_H}`);
    lines.push(`M${px} ${Y - 8}Q${px} ${Y} ${px + 8} ${Y}H${xd}`);
  }
  if (vm.hasChildren) lines.push(`M${xd} ${Y}V${RAIL_H}`);
  if (vm.platform === 'busy' || vm.platform === 'wait') {
    const e2 = vm.platform === 'wait' ? end - 9 : end;
    if (lines.length) out.push(['path', { class: 'r-line', d: lines.join('') }]);
    out.push(['rect', { class: 'r-occ', x: xd + 4, y: Y - 3, width: Math.max(4, e2 - xd - 4), height: 6, rx: 1.5 }]);
    if (vm.platform === 'wait') {
      out.push(['path', { class: 'r-sigpost', d: `M${end - 2} ${Y - 2}V${Y + 8}` }]);
      out.push(['circle', { class: 'r-sig', cx: end - 2, cy: Y - 4, r: 4.5 }]);
    }
    out.push(['circle', { class: 'r-node-busy', cx: xd, cy: Y, r: 5 }]);
  } else if (vm.platform === 'idle') {
    lines.push(`M${xd} ${Y}H${end}`);
    out.push(['path', { class: 'r-line', d: lines.join('') }]);
    out.push(['circle', { class: 'r-node-idle', cx: xd, cy: Y, r: 4.5 }]);
    if (vm.question) out.push(['path', { class: 'r-q', d: `M${end - 5} ${Y - 5}l5 5-5 5-5-5Z` }]);
  } else {
    if (lines.length) out.push(['path', { class: 'r-line', d: lines.join('') }]);
    out.push(['path', { class: 'r-hollow-o', d: `M${xd + 2} ${Y}H${end}` }]);
    out.push(['path', { class: 'r-hollow-i', d: `M${xd + 2} ${Y}H${end - 1.5}` }]);
    out.push(['rect', { class: 'r-stop', x: xd - 1.5, y: Y - 6, width: 3.5, height: 12, rx: 1 }]);
  }
  return out;
}

/** Signature: when it is unchanged the rail svg can be left alone. */
export function railSig(vm, W) {
  return [vm.depth, vm.last ? 1 : 0, vm.hasChildren ? 1 : 0, (vm.cont || []).join(','),
    vm.platform, vm.question ? 1 : 0, W].join('|');
}

export function makeRail(vm, W) {
  const svg = h('svg', {
    class: 'rail', width: W, height: RAIL_H,
    viewBox: `0 0 ${W} ${RAIL_H}`, preserveAspectRatio: 'none', 'aria-hidden': 'true',
  });
  for (const [tag, attrs] of railShapes(vm, W)) svg.appendChild(h(tag, attrs));
  return svg;
}
