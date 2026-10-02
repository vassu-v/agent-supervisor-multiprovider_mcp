# Switchyard: plan, decisions and context

Everything learned and decided while building this, so anyone (human or agent) picking it up has the same context.
Facts here were **measured** unless marked otherwise. Claims that could not be verified are in [Open items](#open-items).

## Contents
1. [Goal and origin](#goal-and-origin)
2. [How it works](#how-it-works)
3. [Why persistent processes](#why-persistent-processes)
4. [Provider findings](#provider-findings)
5. [Decisions](#decisions)
6. [Routing and escalation](#routing-and-escalation)
7. [Test evidence](#test-evidence)
8. [Bugs found and fixed](#bugs-found-and-fixed)
9. [Open items](#open-items)

## Goal and origin
Started from one question: can a main agent use the `agy` (Antigravity) CLI to run sub-agents, and keep control of them?
It grew into a provider-agnostic orchestrator and switch: a main agent (for example Claude) plans and reviews, cheap or
unlimited agents do bulk work, strong agents handle the high-stakes parts, and cost is spread across providers.

The first version (a single-file agy broker) proved the idea. Then the requirement was sharpened: no kill-and-resume, no
losing a long-running command, and any provider behind the same interface.

## How it works
```
callers (you, another agent) ──CLI / HTTP / MCP──►  daemon
                                                    ├─ router    tier + keywords → provider/model
                                                    ├─ guard     pass | escalate | block   (policy.json)
                                                    ├─ registry  agents, queues, events, audit
                                                    └─ adapters  one persistent process/session each
                                                         agy · claude · opencode · codex
```
- **Adapter contract** (`orch/base.py`): `start()`, `send(text, mode)`, `interrupt()`, `kill()`, `alive`, `session_id`,
  `capabilities`. Adapters emit normalised events: `status`, `text`, `tool_start`, `tool_end`, `permission`, `usage`,
  `result`, `error`, `session`. The core never sees provider details.
- **Turn accounting:** the core marks an agent busy when it delivers a turn and idle when a `result` arrives. A
  `cancelled` result is attributed to the interrupted turn, a normal result to the oldest open turn. Queued messages are
  delivered when the agent goes idle.
- **Message modes:** `queue` (after the turn), `steer` (inject mid-turn when `capabilities.steer`, else falls back to
  queue), `interrupt` (the core calls `adapter.interrupt()` first, then delivers the text as a new turn).
- **Spawn:** route, create the working directory, create `AGENTS.md` if missing, snapshot harness notes files, start the
  adapter, prepend a short rules preamble to the task.
- **Faces:** HTTP API (`/api/*`), dashboard (`/`, polls every 2s), CLI (`agentctl.py`), MCP stdio bridge
  (`orch/mcp_bridge.py`, 13 tools). Loopback only; mutating calls need a bearer token from `orch/token.txt`.
- **Persistence:** event log per agent in `logs/<id>.jsonl`, audit trail in `logs/audit.jsonl`.

## Why persistent processes
The obvious design (headless one-shot, then kill and resume to change direction) has three problems:
1. **Cost.** A resumed conversation re-sends its history; the prompt cache may be cold. Measured: agy reported 0 cached
   tokens on a fresh start. A live Claude session read 29,006 cached tokens on its second turn.
2. **Lost work.** Killing an agent kills the shell command it was running (verified on agy, claude and opencode).
3. **No visibility.** One-shot output is invisible until exit.

So every adapter keeps one live process and uses the provider's native mid-turn control wherever it exists.

## Provider findings
| | agy | Claude Code | OpenCode | Codex |
|---|---|---|---|---|
| Persistent channel | stream-json over stdio | `claude -p` stream-json over stdio | `opencode serve` (HTTP + SSE) | `codex app-server` (JSON-RPC over stdio) |
| Mid-turn message | Queued until the turn ends (a 2nd message sat unread for the full 60s command) | Accepted; seen at the next tool boundary (`DONE STEERED`) | Natively queued; folded in at the next step boundary, does not preempt a running tool | `turn/steer` (documented; unverified) |
| Interrupt | **None.** 5 control events tried (cancel, interrupt, abort, stop, control): all "unsupported" | `control_request {subtype: interrupt}`: 0.2s, process stays alive, child dies, context kept | `POST /session/:id/abort`: 0.3s, session survives, child dies, context kept | `turn/interrupt` (documented; unverified) |
| Kills child command on kill | Yes | Yes | Yes | Assumed |
| Session resume | `--conversation <id>` | `--resume <id>` | session id persists | thread id |
| Auto-approve | `--dangerously-skip-permissions` | `--permission-mode bypassPermissions` | `OPENCODE_CONFIG_CONTENT={"permission":"allow"}` | `approvalPolicy: never` |

agy specifics worth knowing:
- Input line is `{"event":"user","message":{"role":"user","content":"..."}}`, one per line. Launch with `--print=` (with
  the equals sign: plain `--print` swallows the next flag as its prompt).
- PowerShell piping prepends a BOM that agy rejects; drive it from a real process API (Python `subprocess`).
- The `init` event carries a top-level `conversation_id`; tool steps carry `tool_name` and `tool_info.parameters`.
- Its binary contains internal cancel/interrupt code but nothing is exposed. `agy remote-control` is a phone-access
  daemon (start/status/stop only, registers the machine at boot); it has no turn-control API and was **not** started.

## Decisions
1. **Persistent processes over kill/resume**, for the reasons above.
2. **One adapter contract**, normalised events, capabilities declared per adapter and measured, never assumed.
3. **Interrupt is done by the core, then text is delivered.** Adapters must not interrupt again inside `send()` (this
   caused a double restart in agy; fixed).
4. **Permissions off, scoped by working directory.** Every agent runs with prompts disabled so it never blocks, and is
   started in its own directory. Because that means no pre-block, the guard is **detect-and-react**, not a sandbox.
5. **Guard policy is data** (`orch/policy.json`, hot-reloaded), evaluated on every `tool_start`/`permission` event.
6. **Any agent may stop any agent, with a mandatory reason**, audited. Agents get the command in their preamble.
7. **`AGENTS.md` is the single shared notes file.** The daemon creates it, tells agents to use it, and mirrors new lines
   from `CLAUDE.md`/`GEMINI.md`/etc. into it. Harness-specific files are **not** forbidden (an earlier version reverted
   edits to them; changed on request: let users edit them, but save the knowledge to `AGENTS.md`).
8. **Stdlib-only Python 3.10+.** Nothing to install.
9. **CLI is `agentctl.py`, not `orch.py`.** A module named `orch.py` shadows the `orch/` package (found the hard way).
10. **Default model tier is medium.** `-low` only for near-zero-intelligence chores; the API returns a warning.
11. **One daemon, three faces** (HTTP/dashboard, CLI, MCP) so many clients share a single fleet.
12. **Loopback only, bearer token for changes.** Agents have full permissions, so the port must not be exposed.
13. **Security hardening after an independent QA pass.** Every API call except `/api/health` needs the bearer token (compared in
    constant time); the Host header must be the loopback address and any Origin must match it (defeats DNS-rebinding and
    cross-site requests, which could otherwise read the dashboard's token and spawn permission-less agents); the dashboard has
    no inline handlers and escapes every value; agent ids are validated (`[A-Za-z0-9_-]{1,32}`); the server binds exclusively
    (a second daemon cannot share the port); bodies are size-capped; a CSP and `X-Frame-Options: DENY` are sent.
14. **The guard fails closed.** The policy is validated and its regexes compiled once; a malformed edit keeps the last good
    policy; an exception inside the guard escalates instead of passing; tool input is walked as values (not JSON-dumped) and
    capped at 4000 characters so regex time stays bounded; the guard's reaction (stop/interrupt) runs off the adapter's reader
    thread, because stopping from inside it can deadlock on the adapter's own reply. Editing `orch/policy.json`, `token.txt` or
    `config.json` from an agent is itself an escalation.
15. **Agents die with the daemon (Windows).** The daemon puts itself in a kill-on-close Job Object, so a crash, `taskkill` or a
    closed console cannot leave orphaned agent processes. Verified by hard-killing a daemon with a running agent.
16. **Honest, strict API behaviour.** Invalid `mode`/`decision`/`tier` are errors (not silently accepted); `steer` on a provider
    that cannot steer says it was queued; `interrupt` keeps already-queued messages and says how it interrupted (native vs
    restart); interrupting an idle agent is a no-op; a nonexistent `cwd` (whose parent is also missing) is refused as a typo
    guard; spawn failures leave no half-started agent; `models` output is bounded (`filter`, `limit`).
17. **Codex built blind.** The adapter follows the schema generated by `codex app-server generate-json-schema` and is
    tested against a mock. No real Codex turn has ever been run.
18. **Provider switches.** Each provider can be turned off (`orch/config.json`, `SWITCHYARD_DISABLE`, dashboard, `agentctl providers`).
    A provider that is off, not installed, or signed out is never routed to; the MCP `initialize` instructions and every spawned
    agent's preamble list what is unavailable, and requests for it fail with the list of usable providers.
19. **Models are discovered, not hardcoded.** `orch/providers.py` asks each CLI (`agy models`, `opencode models`, `codex debug models`;
    Claude Code has no list command so documented aliases are used) and caches the answer 10 minutes, warmed in the background and
    refreshable on demand, never per call (a cold discovery is ~12s across four CLIs). An explicit model that a live provider does
    not offer fails fast with real alternatives.
20. **Routing candidates are patterns, not names.** `provider:glob` resolved against the discovered list (newest version wins);
    `*` means the provider's own default. A tier skips candidates whose provider is off or whose pattern matches nothing.
21. **Login state is checked** (`claude auth status`, `codex login status`; OpenCode/agy inferred from a non-empty model list) so a
    signed-out provider is reported as `needs login` instead of failing mid-task.

## Routing and escalation
**Routing** (`routing` in the policy): tiers `trivial`, `bulk`, `standard`, `hard`, `review`, each an ordered list of
`provider:pattern` (glob over the discovered models); the first usable candidate wins. Sensitive keywords (auth, secret, payment, migration, production,
delete, deploy, credential…) bump a task to `hard`, which also flags the finished agent as `needs_review`.

**Guard** (`guard`): each tool call is matched against patterns.
| Verdict | Covers | Effect |
|---|---|---|
| pass | reads, writes inside the cwd, tests, local git | none |
| escalate | `git push`/`reset --hard`/remote edits, global installs, external POST/DELETE, cloud CLIs, `.env`/ssh/CI files, recursive deletes, `taskkill`/`Stop-Process`, paths outside the cwd | agent interrupted, escalation created; `resolve allow|deny` resumes it with that instruction; unanswered in 600s → stopped |
| block | `rm -rf /`, `format X:`, `diskpart`, `reg delete HK…`, `shutdown`, uploading secrets | agent stopped immediately, audited |

Honest framing: the guard sees a tool call as it starts. It cannot un-run something, only stop the agent quickly.

## Test evidence
All run against real CLIs on Windows 11 with the cheapest suitable models.
- **agy:** two agents in parallel in separate directories; `AGENTS.md` auto-created in each; block (stopped), escalate
  (`git push`), deny resolution; stop-by-agent requires a reason; interrupt during a 120s sleep killed the child, restarted
  once, and the follow-up answered; queue and steer-fallback delivered in order.
- **Claude Code** (haiku, tiny prompts): simple reply; multi-turn memory in one process with 29k cached tokens on turn 2;
  steer mid-tool returned `DONE STEERED`; interrupt in 0.2s with no restart, child killed, follow-up remembered the
  earlier word.
- **OpenCode** (free `big-pickle`): 5/5 adapter tests (reply, memory, tool + queued mid-turn message, abort vs a 120s
  command, two isolated parallel adapters); also driven through the daemon.
- **Codex:** 5/5 against `tests/codex/mock_app_server.py` (turn, steer, interrupt + follow-up, approval auto-accept, kill).
- **Mirror rule:** an agy agent wrote `CLAUDE.md`; the file was kept and the line appeared in `AGENTS.md`.
- **MCP bridge:** `initialize`, `tools/list`, and `tools/call` against the live daemon. An independent agent then drove all 13
  tools like a real client: it found the input-validation, interrupt-queue and documentation gaps fixed in decision 16.
- **Guard:** `tests/test_guard.py` (25 checks incl. the reported false positives, evasions and a ReDoS case); verified end to end
  with real agents (block stopped the agent; an escalation was denied and the agent adapted).
- **HTTP hardening:** `tests/test_http_security.py` (22 checks on a throwaway daemon: token, Host/Origin, traversal ids, bad
  JSON, oversize body, duplicate bind).
- **Crash cleanup:** a daemon was hard-killed with a long command running; zero agent processes survived.
- **Dashboard:** renders grouped by directory with status, usage and audit.

## Bugs found and fixed
| Bug | Cause | Fix |
|---|---|---|
| Every agent stuck "busy" | Reader thread read `init.conversation_id` from the wrong level; the exception killed the thread silently | Read the top-level field; reader loops catch and log |
| Double restart on agy interrupt | Core and adapter both interrupted | Core only |
| Late `cancelled` result attached to the new turn | Async native interrupt | Attribute by interrupted flag |
| `from orch import ...` failed | `orch.py` module shadowed the `orch/` package | Renamed the CLI to `agentctl.py` |
| `.gitignore` hid the skills | Bare `SKILL.md` pattern matched everywhere | Anchored to the repo root |
| Local takeover via a web page | Dashboard served the token to any Host; no Host/Origin check (DNS rebinding) | Host/Origin validation, token on all calls |
| Stored XSS in the dashboard | Agent id put unescaped into an inline `onclick` | Id validation, no inline handlers, full escaping |
| Guard silently off | A malformed policy or one bad regex raised inside the event handler, which swallowed it | Last-good policy, compile once, fail closed |
| Guard false positives and evasions | `shutdown` in a commit message, `rm -rf` of a subfolder, `process.env`, `git -C . push`, `curl --request POST`, dict inputs, prefix path match, URLs read as paths | Narrower, bounded patterns; value walking; `commonpath`; URL stripping (25 regression tests) |
| ReDoS in the guard | Unbounded `[^\n]*` between alternations (3.9s on 6000 `-d`) | Bounded `.{0,300}` plus a 4000-char input cap (now ~4ms) |
| Interrupt dropped queued messages | Queue drained between the cancel and the new turn | Queue held aside across the interrupt |
| Invalid inputs accepted | `mode`, `decision` not validated; "maybe" consumed an escalation | Strict validation; failed validation does not consume anything |
| Orphaned agents after a daemon crash | Cleanup ran only on a graceful exit | Kill-on-close Job Object |

## Open items
- **Codex against real Codex.** Unverified assumptions: `initialize`/`thread/start` response shapes beyond the schema;
  the turn id being available before a steer or interrupt; `turn/steer` racing on a turn-id mismatch; an interrupt really
  ending as `turn/completed` with status `interrupted`; `tokenUsage.last` being per-turn rather than cumulative; the
  permissions-approval echo shape; the `commandExecution` command/exit-code mapping; locating `codex.exe` on Windows.
- **agy install command:** no official Google page with an install command was found (only third-party sources), so the README
  points to Antigravity rather than giving a script. agy has no login-status command; its login state is inferred.
- **Claude model list:** Claude Code exposes no list command, so aliases are used; exact ids can be added via `extra_models`.
- **From the QA pass, not yet fixed:** the OpenCode server each agent runs has no password on its loopback port, so another local
  process could drive that agent; adapters' `_mark_dead` paths do not always kill their process (the Job Object covers daemon
  exit, not an adapter dying mid-run); Codex emits both a permission and a tool event for one command (deduplicated by the
  core, but the approval is written after the event is emitted); finished agents, escalations and turn history are never
  pruned; a Claude `steer` may produce an extra `result` event that ends the turn early (suspected, not reproduced); the
  `agent_result` status can lag a cancelled turn.
- **Cross-provider handoff** (one agent's result feeding another provider's agent) is not built.
- **A clean live-vs-resume cost comparison** has not been run; only the cache numbers above exist.
- **Dashboard** polls every 2s rather than using true server push; read-only API calls need no token (loopback only).
- **agy interrupt** costs a restart. If agy ever exposes its internal cancel, switch the adapter to it.
- **Windows-first.** Process-tree killing uses `taskkill`; Linux/macOS paths exist but are untested.
- **Guard is detect-and-react.** A real sandbox per provider would be stronger; only agy's `--sandbox` is wired in.
