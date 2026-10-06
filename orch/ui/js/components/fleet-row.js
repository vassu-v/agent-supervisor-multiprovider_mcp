// One agent row: rail gutter, identity, provider/model chips, spark, badges.
// Created once per agent id (+ density suffix, so a density switch rebuilds);
// updateRow() patches in place so an unchanged row costs no DOM mutations.
// Agent-authored strings are always text nodes (h() guarantees it). Slots for
// badges, paths and the effort warning exist only while non-empty. Compact
// rows (at scale) omit goal and last line, mirroring the compact stylesheet.

import { h, on, text } from '../core/h.js';
import { agentTokens } from '../core/derive.js';
import { tokens as fmtTokens } from '../core/fmt.js';
import { makeRail, railSig } from './fleet-rail.js';

const PROV_GLYPH = { agy: 'g-agy', claude: 'g-claude', opencode: 'g-opencode', codex: 'g-codex' };

function setAttr(el, name, value) {
  if (el.getAttribute(name) !== String(value)) el.setAttribute(name, String(value));
}

function glyphSvg(symbol) {
  return h('svg', { class: 'i', 'aria-hidden': 'true', focusable: 'false' }, h('use', { href: '#' + symbol }));
}

/** Child slot that exists only while sig is non-null. sig uniquely describes items. */
function setSlot(parent, key, sig, build) {
  const slots = parent._slots || (parent._slots = {});
  const cur = slots[key];
  if (cur && cur.sig === sig) return cur.node;
  if (cur && cur.node.parentNode === parent) cur.node.remove();
  if (sig === null) { delete slots[key]; return null; }
  const node = build();
  parent.appendChild(node);
  slots[key] = { sig, node };
  return node;
}

function badgesSlot(vm) {
  const sig = vm.badges.length ? vm.badges.map((b) => b.label).join(' ') : null;
  return { sig, build: () => h('span', { class: 'badges' }, vm.badges.map((b) => h('span', { class: b.cls }, b.label))) };
}

function pathsSlot(vm) {
  const sig = vm.paths.length ? vm.paths.join(' ') : null;
  return { sig, build: () => h('span', { class: 'paths' }, vm.paths.map((p) => h('span', { class: 'pt' }, p))) };
}

function sparkSvg(values) {
  const svg = h('svg', { viewBox: '0 0 48 16', 'aria-hidden': 'true', focusable: 'false' });
  const max = Math.max(1, ...values);
  values.forEach((v, i) => {
    const bh = Math.max(2, Math.round((14 * v) / max));
    svg.appendChild(h('rect', {
      class: i === values.length - 1 ? 'sb last' : 'sb',
      x: i * 4, y: 16 - bh, width: 3, height: bh,
    }));
  });
  return svg;
}

const sparkSig = (values) => (values ? values.join(',') : '');

/**
 * @param {object} vm  row view-model from fleet-model buildFleet()
 * @param {number} W  gutter width for this yard
 * @param {{open: (id: string) => void, selected: string|null, compact: boolean}} hooks
 */
export function createRow(vm, W, hooks) {
  const a = vm.agent;
  const st = h('span', { class: vm.pill }, glyphSvg(vm.glyph), vm.word);
  const l1 = h('div', { class: 'l1' }, h('span', { class: 'aid' }, a.id), st);
  const who = h('div', { class: 'who' }, l1);
  let goalText = null;
  let tool = null;
  if (!hooks.compact) {
    const b = badgesSlot(vm);
    if (b.sig !== null) setSlot(l1, 'badges', b.sig, b.build);
    const goal = h('div', { class: 'goal goal-wrap' }, a.goal || '');
    goalText = goal.firstChild;
    const p = pathsSlot(vm);
    if (p.sig !== null) setSlot(goal, 'paths', p.sig, p.build);
    tool = h('div', { class: 'tool-line' }, vm.toolLine || '');
    if (!vm.toolLine) tool.setAttribute('hidden', '');
    who.append(goal, tool);
  } else if (vm.badges.length) {
    const b = badgesSlot(vm);
    setSlot(l1, 'badges', b.sig, b.build);
  }
  const eff = h('span', { class: 'eff', title: a.effort_applied ? `effort: ${a.effort_applied}` : 'effort: none' });
  const pips = [h('i'), h('i'), h('i'), h('i')];
  pips.forEach((el, i) => { if (i < vm.pips) el.setAttribute('class', 'on'); });
  eff.append(...pips);
  const prov = a.provider || 'other';
  const mWord = h('span', null, (vm.pips ? '' : '· ') + vm.effortWord);
  const m = h('span', { class: 'm' }, (a.model || '') + ' ', eff, mWord);
  if (vm.effortWarn) m.appendChild(h('span', { class: 'eff-warn', title: vm.effortWarn }, '!'));
  const pm = h('div', { class: 'pm' },
    h('span', { class: 'p p-' + prov }, glyphSvg(PROV_GLYPH[prov] || 'g-other'), prov),
    m);
  const tok = h('div', { class: 'tok' });
  let spark = null;
  if (vm.spark) { spark = sparkSvg(vm.spark); tok.appendChild(spark); }
  const tokV = h('span', { class: 'v' }, fmtTokens(agentTokens(a)));
  tok.appendChild(tokV);
  const row = h('div', {
    class: 'row' + (a.status === 'dead' ? ' dead' : ''),
    role: 'row', 'aria-level': vm.depth + 1,
    'aria-selected': hooks.selected === a.id ? 'true' : 'false',
    tabindex: '-1', 'data-id': a.id,
  });
  row.appendChild(makeRail(vm, W));
  const pName = pm.firstChild.lastChild;
  row.append(h('div'), who, pm, tok);
  on(row, 'click', () => hooks.open(a.id));
  row._f = {
    W, st, stWord: st.lastChild, l1, goalText, tool, pm, pName,
    pips, eff, m, mWord, tok, spark, tokV,
    railSig: railSig(vm, W), sparkSig: sparkSig(vm.spark), prov,
    compact: hooks.compact, badgeSig: vm.badges.length ? 1 : 0, warn: vm.effortWarn || null,
  };
  return row;
}

/** Patch a row created by createRow(); touches the DOM only where vm changed. */
export function updateRow(row, vm, W, hooks) {
  const a = vm.agent;
  const f = row._f;
  if (f.W !== W || f.railSig !== railSig(vm, W)) {
    const rail = makeRail(vm, W);
    row.replaceChildren(rail, ...[...row.childNodes].slice(1));
    f.W = W;
    f.railSig = railSig(vm, W);
  }
  setAttr(row, 'aria-level', vm.depth + 1);
  setAttr(row, 'aria-selected', hooks.selected === a.id ? 'true' : 'false');
  row.classList.toggle('dead', a.status === 'dead');
  if (f.st.getAttribute('class') !== vm.pill) f.st.setAttribute('class', vm.pill);
  const use = f.st.firstChild.firstChild;
  const wantGlyph = '#' + vm.glyph;
  if (use.getAttribute('href') !== wantGlyph) use.setAttribute('href', wantGlyph);
  text(f.stWord, vm.word);
  const b = badgesSlot(vm);
  setSlot(f.l1, 'badges', b.sig, b.build);
  if (!f.compact) {
    text(f.goalText, a.goal || '');
    const p = pathsSlot(vm);
    setSlot(f.goalText.parentNode, 'paths', p.sig, p.build);
    text(f.tool, vm.toolLine || '');
    if (vm.toolLine) f.tool.removeAttribute('hidden'); else f.tool.setAttribute('hidden', '');
  }
  if (f.prov !== (a.provider || 'other')) {
    const prov = a.provider || 'other';
    f.prov = prov;
    const p = f.pm.firstChild;
    p.setAttribute('class', 'p p-' + prov);
    p.firstChild.firstChild.setAttribute('href', '#' + (PROV_GLYPH[prov] || 'g-other'));
    text(f.pName, prov);
  }
  text(f.m.firstChild, (a.model || '') + ' ');
  f.pips.forEach((el, i) => {
    const should = i < vm.pips;
    if (el.hasAttribute('class') !== should) {
      if (should) el.setAttribute('class', 'on'); else el.removeAttribute('class');
    }
  });
  setAttr(f.eff, 'title', a.effort_applied ? `effort: ${a.effort_applied}` : 'effort: none');
  text(f.mWord, (vm.pips ? '' : '· ') + vm.effortWord);
  const hasWarn = !!vm.effortWarn;
  f.warn = vm.effortWarn || null;
  const cur = [...f.m.childNodes].find((n) => n.nodeType === 1 && n.getAttribute('class') === 'eff-warn');
  if (hasWarn && !cur) f.m.appendChild(h('span', { class: 'eff-warn', title: vm.effortWarn }, '!'));
  else if (!hasWarn && cur) cur.remove();
  else if (cur) setAttr(cur, 'title', vm.effortWarn);
  if (f.sparkSig !== sparkSig(vm.spark)) {
    f.sparkSig = sparkSig(vm.spark);
    if (f.spark) { f.spark.remove(); f.spark = null; }
    if (vm.spark) { f.spark = sparkSvg(vm.spark); f.tok.insertBefore(f.spark, f.tokV); }
  }
  text(f.tokV, fmtTokens(agentTokens(a)));
}
