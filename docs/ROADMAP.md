# Roadmap: shared context, announce board, swarm-safe editing

Synthesis of five independent Opus design reviews (announce board, locking and edit history, workspaces, red team and QA,
README). Nothing here is built yet. Where the reviewers disagreed, the choice and the reason are stated.

## Status (update)
- **Phase 0 done:** per-agent tokens and scopes, one mirror snapshot per directory, configurable agent cap (default 20), README restructure and docs split.
- **Phase 1 done:** workspaces, sessions, hierarchy (parent, goal, paths), the briefing, the two-lane board with digest delivery, auto events, wakes,
  stale-question surfacing, dashboard tabs/tree/board, CLI and MCP tools. Details and the decisions behind them: `docs/PLAN.md` (22-31).
- **QA built first, as planned:** scripted fake provider, harness, swarm/chaos/e2e suites, `tests/qa.py`. They found and fixed real bugs (see PLAN).
- **Not started:** Phase 2 (SQLite notes API, advisory claims/leases, edit ledger) and Phase 3 (hook enforcement, worktrees).

## The one finding that changes the order
Agents cannot be told apart today. They are told to run `agentctl.py`, which reads the admin token from `orch/token.txt`, and
the caller name in each request (`by`) is whatever the caller says. So any agent can approve its own escalation, spawn more
agents, or post as "dashboard". A board, claims or edit history built on that would have no trustworthy sender.
**Identity comes first.**

## Decisions
| Topic | Decision | Why |
|---|---|---|
| Identity | Per-agent token minted at spawn, passed in the agent's environment. The server maps token to agent id and ignores `by`. Agent tokens may announce, read, declare, claim, list and stop; they may not resolve escalations, switch providers, or spawn past a quota. | Without it nothing downstream can be trusted. |
| Clients | Each MCP bridge registers a session (`clientInfo.name`, optional `SWITCHYARD_WORKSPACE`, heartbeat). | Lets the briefing say who is attached. |
| Unit of scope | **Workspace** = git root (the directory itself if no repo). Agents in subfolders share the root's board and notes. | One board and one `AGENTS.md` per project, not per folder. |
| Default view | A client sees its own workspace. `all` widens it. Acting on another workspace's agent needs a reason and is audited. | Quiet by default, never hidden. |
| Storage | SQLite (`logs/switchyard.db`, WAL, one writer thread). Live agent state stays in memory. | Locking, history and boards need transactions. |
| Board | Structured kinds (`started`, `done`, `changed`, `blocked`, `info`), short text, delivered as a **digest at the agent's next turn boundary**. Only a child's `done`, `stopped` or `error` wakes an idle parent. | Avoids token storms and ping-pong. |
| Auto-announcements | The daemon posts spawn, finish, stop, error, escalation and conflict events itself. | Works even if agents never cooperate. |
| Board safety | Posts are untrusted text: fenced as data, length-capped, control characters stripped, guard run over the body, verified sender, no cascades, rate-limited. | It is a prompt-injection channel. |
| `AGENTS.md` | The database is the source of truth. Agents add with `agentctl note`, and may remove only their own entries. The daemon renders the file atomically under one lock. Edits made directly to the file are ingested; deleted lines are restored. | We cannot stop agents writing the file, so we make loss impossible instead. |
| Edit history | Ledger from tool events (exact), plus a debounced scanner for shell writes (credited as `window`, `ambiguous` or `external`). Content-addressed blobs for diffs. Never auto-revert source files. | Per-line attribution under concurrent shell writes is not possible without admin tracing. |
| Swarm in one directory | No directory per agent. Layer 1: advisory claims with leases (expire, released on death), conflict warnings from real tool events, escalate repo-wide git commands (`stash`, `checkout .`, `clean`, `reset`). Layer 2: enforce claims through provider hooks. Layer 3: opt-in worktrees. | Matches your swarm requirement; each layer is shippable alone. |
| Concurrency cap | Move `MAX_CONCURRENT` to config, default 20. | 8 blocks a swarm. |
| README | About 110 lines. Install, providers, policy, security and MCP detail move to `docs/`. Add a "many agents, one repo" section with one table. | Readers dropped off after the first screen. |

## Phases
**Phase 0, small fixes that need no design (do first):**
- per-agent tokens and agent permissions;
- one `CLAUDE.md`/`GEMINI.md` snapshot per directory, not per agent (today one change is mirrored once per agent);
- configurable agent cap;
- README restructure and docs split.

**Phase 1, shared context:**
- workspace and session identity;
- persisted owner, parent, goal, declared paths;
- running agents in the MCP briefing and each spawned agent's preamble;
- dashboard owner, goal and agent tree;
- the announce board with digest delivery, auto-announcements and parent notification.

**Phase 2, safe shared editing:**
- SQLite;
- `agentctl note` and the rendered `AGENTS.md`;
- advisory claims with leases;
- the tool-event edit ledger and the scanner;
- repo-wide git escalation patterns.

**Phase 3, enforcement and polish:**
- Claude `PreToolUse` hook and OpenCode plugin to block claimed paths, Codex per-path decline;
- opt-in worktrees with a daemon merge step;
- MCP notifications and urgent steer.

**QA is built before Phase 2**, not after: see below.

## QA plan
- A **fake provider adapter** (scripted, no model cost) that can edit files, post, claim and crash, and can mimic agy's limits.
- `test_swarm.py`: 20 fake agents in one directory. Asserts no overlapping leases, one correct author per write, every post delivered
  once in order, `AGENTS.md` only grows, turns stay under budget.
- `test_claims_props.py`: random operation sequences against a reference model (grant-all-or-none, expiry, release on death).
- `test_chaos.py`: daemon killed mid-swarm, agent killed mid-write, corrupt policy, 1 MB post, malicious posts, locked `AGENTS.md`.
- `test_board_storm.py`, `test_mirror_shared_cwd.py` (the duplicate-mirror bug), and an opt-in real-provider smoke test on free models.
- `tests/qa.py` runs everything plus the existing guard and HTTP tests, writes a report, and a non-zero exit is the gate. A QA agent
  reruns it with new seeds.

## Biggest risks
1. **Prompt injection through the board.** Mitigated, not removed. Free text never wakes anyone and is always fenced.
2. **Declared goals and paths are guesses.** The briefing must say they are advisory. Conflicts are detected from files actually touched.
3. **Shell commands and git can wipe shared work** and bypass every hook. Escalation patterns are the real defence.
4. **agy cannot receive a message mid-turn**, so a "don't touch X" post arrives late. Show per-provider delivery latency; prefer
   claimed-disjoint work for agy in shared directories.
5. **Scope.** Six features for an alpha whose Codex adapter has never run against real Codex. Hence the phasing.

## Must verify before relying on it
- Provider hooks fire under permission bypass (Claude `PreToolUse`) and the OpenCode plugin API behaves as described.
- What working directory and `clientInfo` Antigravity's MCP launch actually provides.
- Whether Windows holds `AGENTS.md` open often enough to need the retry logic in practice.
- Real Codex behaviour (the eight assumptions in `PLAN.md`).

## Explicitly not building
Kernel or ETW tracing for writer process ids, a virtual filesystem, CRDT merging of source files, write-through MCP edit tools,
per-call shadow-repo commits, a directory per agent as the default, automatic reverts of anything except `AGENTS.md`, or a database
inside an agent's working directory.
