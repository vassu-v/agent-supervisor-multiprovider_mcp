# Dashboard: IA and build plan

This plan covers structure, data, interaction and code. The visual direction (sibling docs) owns colour, type and the fleet metaphor. Renderers take plain data.

**Current UI weaknesses**
- It polls 4 endpoints every 2 s regardless of change, including all model lists. The All tab adds one board call per workspace.
- The All tab shows no agents.
- Panels are rebuilt with `innerHTML`, so scroll and focus are lost.
- Events are raw JSON for one agent. There is no current tool, stuck signal, effort or caps.
- Stop uses `prompt()`. Escalations show no workspace or deadline.
- There are no landmarks or `aria-live`.
- `GET /` embeds the admin token.
- The CSP needs `'unsafe-inline'`.

## 1. Five-second jobs

| Question | Answered by |
|---|---|
| Needs my decision? | Pinned escalation bar with workspace and deadline countdown. Header shows the open-question count. |
| What runs where? | Fleet: a yard per workspace with live/busy/idle/dead counts |
| Stuck or broken? | Attention sort. Badges: `quiet` (busy, no event 120 s), `stuck?` (600 s), `restarted N`, provider degraded |
| What is X doing / just did? | Unit shows current tool and last line. The drawer shows the stream. |
| Who is blocked on whom? | `blocked` posts and questions link to the asker. The tree is drawn. |
| Provider/model/effort, steerable? | Unit chips. Models view has the caps matrix. |
| Token spend? | Header totals. Models view has per-model rollup. Money data does not exist. |
| What did the board say? | Fleet rail shows the last 5 announcements. The Board view has the rest. |

## 2. Views and navigation

| Hash | View |
|---|---|
| `#/fleet?ws=all\|<id>&st=&prov=&owner=&q=` | Fleet (default). Grouped by workspace, then by tree. |
| `…&agent=<id>` on any view | Drawer: stream, turns, tools, goal/paths, provider/model/effort/caps. Actions: interrupt, send (queue; steer only if `caps.steer`; interrupt), stop with a reason form. |
| `#/timeline?ws=&span=15m\|1h\|6h` | Timeline: one swimlane per agent with turn bars, tool ticks, escalation and post markers |
| `#/board/<ws>` | Board: green and pink lanes, activity collapsed, post/ask/answer forms |
| `#/models` | Models & Providers: toggles, discovered models and efforts, agents per model, caps matrix |
| `#/escalations`, `#/audit` | Full history. The pending bar shows on every view. |

Hash ids must match `^[A-Za-z0-9_-]{1,64}$`; others are dropped.

**Keys**

| Key | Action |
|---|---|
| `g f/t/b/m/a` | Go to view |
| `/` | Search |
| `j` / `k` | Move selection |
| `Enter` | Open drawer |
| `Esc` | Close |
| `e` | First escalation |
| `.` | Density |
| `?` | Help |

Allow, deny and stop have no single-key shortcut.

**Filters**: status, provider, owner and text. They live in the URL and apply client-side.

**Density**: comfortable, compact, or auto (compact above 24 live agents). Stored in try/catch `localStorage`.

**Scale**
- 1 agent: the drawer is docked open.
- 10 agents: full units, tree-indented.
- 60 agents: compact units. Workspaces with no attention items collapse to a summary bar. Dead agents fold into "N finished".
- Attention order: escalation > stuck > blocked > busy > idle > dead.
- Fleet is not virtualised (about 900 nodes). The stream, timeline and audit are windowed.

## 3. Data

**Existing endpoints**

| View | Endpoints and fields |
|---|---|
| Fleet | `/api/list?all=1&tree=1` (`id status provider model owner parent depth workspace goal paths turns queued usage restarts caps needs_review stopped_by last_text`). `/api/workspaces`. `/api/board?ws=&kind=done,blocked,…&n=5` |
| Drawer | `/api/events?id=&since=<seq>` (incremental). `/api/status` `turns_full`, fetched on open and on turn change only. Writes: interrupt, send, stop. |
| Board | `/api/board?ws=&since=<last>` (`open_questions`, `stale`). Writes: announce, ask, answer. |
| Models | `/api/providers?models=0`. `/api/models?limit=500` only on view open; `refresh=1` at most once per 30 s. Write: `/api/provider`. |
| Escalations / Audit | `/api/escalations[?all=1]`, `/api/resolve`. `/api/audit?n=200`. |

**New or changed endpoints.** All admin-only; none are added to `AGENT_ALLOWED`.

1. **`GET /api/changes?since=<rev>&wait=25`** (long-poll). It works over HTTP/1.0 and sends the `Authorization` header, which `EventSource` cannot.
   - `Orchestrator` gains `rev` and a `Condition`. An O(1) `_bump(kind, key)` runs in `_on_event`, `_audit`, the board post paths, escalation create/resolve/timeout, provider toggle and spawn. Bumps coalesce into dirty sets.
   - At most 8 waiters; extra waiters get 429.
   - Reply: `{"rev":812,"reset":false,"agents":["a1"],"boards":["w1"],"escalations":true,"audit":true,"providers":false,"workspaces":true}`.
   - `reset` means refetch everything.
   - Streaming SSE is deferred.
2. **`info()` adds:**
   - `effort` `{"value":"medium","source":"opt|model_id|unknown"}`. No effort exists in code today; parse agy model-id suffixes.
   - `last_event` `{"ts","type"}`
   - `current_tool` (open `tool_start` by id)
   - `turn_t0`
   - `pending_escalation`
3. **Escalations add** `workspace` and `deadline` (`ts + pending_timeout_s`).
4. **`/api/providers` adds** adapter `caps`.
5. **`/api/models` entries add** `efforts` (list or `null`).
6. **`GET /api/timeline?ws=|all=1&since=&until=&max=2000`**. Text-free. Built from turns, events, guard hits, escalations and posts:
   `{"from":0,"to":0,"truncated":false,"agents":[{"id","provider","model","created","ended"}],"items":[{"a":"a1","k":"turn","t0":0,"t1":null,"ok":null,"int":false},{"a":"a1","k":"tool","t0":0,"t1":0,"tool":"Bash","ok":true},{"a":"a1","k":"esc","t":0,"id":"e1a2b","state":"pending"},{"a":null,"k":"post","t":0,"id":42,"kind":"question","ws":"w1"}]}`.
   Events are a 3000-item in-memory deque, so history is bounded.

Usage rollups are computed client-side; no endpoint is needed.

**Expectations at 60 agents**
- Refetch only dirty resources.
- Minimum refetch intervals: list 500 ms, board 1 s, events 250 ms.
- Error backoff from 1 s to 15 s. Pause while `document.hidden`.
- When idle: one pending request, no CPU use.

## 4. Implementation

**Serving**
- `GET /ui/<name>` serves from a whitelist dict built at startup, so no path traversal is possible.
- Headers: MIME type, `nosniff`, ETag, `no-cache`.
- `/` redirects to `/ui/`.
- Host/Origin checks apply as for the API.
- CSP: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`.
- Layout values are set through `el.style.setProperty` (CSSOM), never `style=` attributes.

**Token**
- The token is no longer written into the HTML.
- `agentctl.py dashboard` opens `/ui/#t=<token>`. The fragment is never sent to the server.
- `api.js` moves it to `sessionStorage` and strips the fragment with `history.replaceState`.
- With no token, the page shows a connect screen asking the user to paste the token.
- Agents can still read `token.txt`, so keep that limit stated in security.md.

**Layout** (no build step, no dependencies, ES modules; one owner per folder)

```
orch/ui/index.html  header, nav, #esc-bar, main, aside#drawer, two live regions
orch/ui/styles.css  structure; imports theme.css (visual direction)
js/app.js, js/core/{h,store,router,keys,a11y,fmt,derive}.js   E1
js/net/{api,changes}.js + server changes + fixtures           E2
js/views/{fleet,escalations}.js                               E3
js/views/{drawer,timeline}.js                                 E4
js/views/{board,models,audit}.js                              E5
components: js/components/<view>-*.js, owned by the view's engineer
tests/ui/fixtures/{1,10,60}-agents.json
```

**Module interfaces**

| Module | Interface |
|---|---|
| `h` | `h(tag, attrs, ...kids)`: strings become text nodes. Throws on `on*`, `style`, `srcdoc`, `innerHTML`, or `href` not starting with `#`. Allowed attrs: `class id role data-* aria-* type value disabled tabindex title for`. Also `list(parent, items, key, create, update)`, a keyed reconciler. |
| `store` | `get()`, `set(patch)`, `subscribe(selector, cb)`. Callbacks run batched in one rAF. |
| `api` | `get`, `post` (adds `by:"dashboard"`), `token`, `onAuthError` |
| `changes` | `start(onDirty)`, `stop()` |
| `router` | `parse()`, `go(route, params)`, `onChange(cb)` |
| `keys` | `bind(seq, fn, scope)` |
| `a11y` | `announce(msg, level)`, `trapFocus(el)` (returns a release function), `roving(list)`, `reducedMotion()` |
| `derive` | `attention`, `stuck`, `rollup`, `filter`, `byWorkspace` (pure functions) |
| views | `mount(el, store, api)`, `unmount()`. Views read from the store and write through `api`. |

**Order**
1. Day 0: E1 stubs the interfaces; E2 commits fixtures.
2. Views are built in parallel against the fixtures.
3. Server changes land with Python tests.
4. `dashboard.html` is deleted once the new UI reaches parity.

**Tests**
- `node --test`, skipped when node is missing.
- Pure modules are tested on fixtures. `h` is tested with a fake-document shim.
- View tests assert node counts and keyed identity.

**Accessibility**
- Landmarks.
- Fleet is a treegrid with roving tabindex.
- The drawer is `role=dialog`. It traps focus and returns it to the unit that opened it.
- Escalations announce through the assertive region and questions through the polite region, once per id.
- Every action works by keyboard alone.
- Reduced motion disables transitions.
- Status always has a text label, never colour alone.

**Responsive**
- Under 800 px: one column, full-screen drawer. The timeline scrolls inside its own box.
- The page never scrolls horizontally.

**Performance**
- No layout reads during render.
- The stream keeps ≤ 500 lines. The timeline draws ≤ 2000 marks and buckets anything beyond that.

## 5. Risks, non-goals, acceptance

**Risks**
- Long-poll threads (capped at 8).
- `_bump` on the hot path for text events.
- Effort is mostly `unknown`.
- The token sits in browser history until `replaceState` runs.

**Do not build**
- Money estimates
- Spawn from the UI
- Policy editing
- Frameworks, CDNs, WebSockets, service workers
- History beyond the in-memory windows

**QA**

Auto-checkable:
- No `orch/ui` file contains `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function`, `on[a-z]+=`, `javascript:`, `style=`, or `http(s)://`.
- `h()` throws on `onclick`, `javascript:` and `style`. `<img onerror>` agent text renders as text.
- The `/ui/` CSP has `script-src 'self'` and no `unsafe-inline`.
- A bad Host returns 403. A traversal path returns 404. No served file contains the token.
- `/api/changes` and `/api/timeline` return 401 without a token and 403 with an agent token.
- A 9th waiter gets 429. A fake event wakes a waiter in under 100 ms.
- The 60-agent fixture renders in ≤ 1500 nodes. A one-row change touches only that row.

Manual:
- When idle, only long-polls appear in the network log.
- Keyboard-only run: deny an escalation, stop an agent with a reason, focus returns.
- A screen reader announces each escalation once.

**Unverified**
- CLI effort flags.
- That codex/opencode `tool_end` ids match their `tool_start`.
- All timing numbers (no daemon was run).
