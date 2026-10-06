// The single app state. set() replaces top-level keys immutably; subscribers are called at most once per frame,
// and only when the value their selector returns changed (Object.is).

// One frame, but never stall: rAF does not fire in a hidden or undrawn page, so a timer backs it up.
const HIDDEN_FALLBACK_MS = 100;
function schedule(fn) {
  if (typeof requestAnimationFrame !== 'function') { setTimeout(fn, 0); return; }
  let done = false;
  const run = () => { if (!done) { done = true; fn(); } };
  requestAnimationFrame(run);
  setTimeout(run, HIDDEN_FALLBACK_MS);
}

const UNSET = Symbol('unset');

/**
 * @template S
 * @param {S} initial
 * @returns {{
 *   get: () => S,
 *   set: (patch: Partial<S> | ((s: S) => Partial<S>)) => void,
 *   subscribe: (selector: (s: S) => any, cb: (value: any, state: S) => void) => () => void,
 *   flush: () => void,
 * }}
 *  get()                 current state (treat as read-only)
 *  set(patch)            shallow-merge top-level keys; nested objects must be replaced, not mutated
 *  subscribe(sel, cb)    cb(value, state) runs in the next batch (also once initially), then whenever sel's result changes;
 *                        returns an unsubscribe function
 *  flush()               run pending callbacks now (tests, and before a synchronous read of the DOM)
 */
export function createStore(initial) {
  let state = initial;
  let pending = false;
  const subs = new Set();

  function flush() {
    pending = false;
    for (const sub of [...subs]) {
      if (!subs.has(sub)) continue; // unsubscribed by an earlier callback in this batch
      const value = sub.selector(state);
      if (sub.last !== UNSET && Object.is(value, sub.last)) continue;
      sub.last = value;
      sub.cb(value, state);
    }
  }

  function request() {
    if (pending) return;
    pending = true;
    schedule(() => { if (pending) flush(); });
  }

  return {
    get: () => state,
    set(patch) {
      const p = typeof patch === 'function' ? patch(state) : patch;
      if (!p) return;
      state = { ...state, ...p };
      request();
    },
    subscribe(selector, cb) {
      const sub = { selector, cb, last: UNSET };
      subs.add(sub);
      request();
      return () => { subs.delete(sub); };
    },
    flush,
  };
}

/**
 * Memoise a derived selector: compute runs only when one of the inputs changed identity.
 * Use it so subscribe() sees the same array/object back when nothing relevant changed.
 * @template S, R
 * @param {(s: S) => any[]} inputs  picks the inputs from state
 * @param {(...args: any[]) => R} compute
 * @returns {(s: S) => R}
 */
export function memo(inputs, compute) {
  let lastArgs = null;
  let lastResult;
  return (s) => {
    const args = inputs(s);
    if (lastArgs && args.length === lastArgs.length && args.every((a, i) => Object.is(a, lastArgs[i]))) return lastResult;
    lastArgs = args;
    lastResult = compute(...args);
    return lastResult;
  };
}
