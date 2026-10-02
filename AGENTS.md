# AGENTS.md

Single shared instructions file for every agent and harness working in or with this repo. Keep durable learnings under
"Agent notes" at the bottom. If your harness writes to `CLAUDE.md` / `GEMINI.md` instead, that is fine: the daemon mirrors
new lines here.

## What this repo is
Switchyard: a local daemon that runs many coding agents (agy, claude, opencode, codex) as persistent sessions you can
spawn, watch, steer, interrupt and stop. Stdlib-only Python 3.10+. Start with [`README.md`](README.md); every design
decision is in [`docs/PLAN.md`](docs/PLAN.md).

## Connect an agent
(Commands say `python`; use `py -3.10` or `python3` if that is how Python 3.10+ starts on your machine.)
1. Start the daemon (own terminal, keep it running): `python agentctl.py serve` (port 8765, override with `ORCH_PORT`).
2. Pick one:
   - **MCP** (best for agents): add the stdio server `python <repo>/orch/mcp_bridge.py` to your client (13 tools; the server's
     `initialize` instructions list which providers are available).
   - **CLI**: `python agentctl.py <cmd>` (below), JSON output.
   - **Skills**: copy `skills/switchyard-use` (and `switchyard-setup`) into your agent's skills folder.
3. Dashboard for humans: http://127.0.0.1:8765/

## Commands (`python agentctl.py ...`)
| cmd | |
|---|---|
| `spawn "<task>" --cwd DIR [--provider agy\|claude\|opencode\|codex\|auto] [--model M] [--tier T] [--id ID] [--sandbox]` | start an agent; `auto` routes by tier and keywords; `--sandbox` is agy-only and CLI-only |
| `list`, `status ID`, `tail ID [N]`, `events ID [SINCE]`, `result ID` | observe |
| `send ID "<msg>" [--mode queue\|steer\|interrupt]` | queue = after the turn; steer = inject mid-turn if supported, else queued; interrupt = cancel the turn, keep the session |
| `interrupt ID`, `stop ID --reason "<why>"` | a reason is required to stop; any agent may stop any agent; audited |
| `escalations`, `resolve EID allow\|deny [--note]` | decisions the guard is waiting for |
| `route "<task>"`, `audit` | preview routing, see who did what |
| `providers`, `providers disable\|enable NAME` | which providers are ready / off / not installed / need login; switch one off or on |
| `models [PROVIDER] [--refresh] [--full]` | models each provider reports right now (discovered live, cached 10 min; never hardcoded) |

Env: `ORCH_URL` (default `http://127.0.0.1:8765`), `ORCH_TOKEN` (default: `orch/token.txt`), `ORCH_AGENT` (your id, for audit).

## Providers and models
Check `providers` before routing: a provider can be switched off by the user (`orch/config.json`, `SWITCHYARD_DISABLE`, or the
dashboard), missing, or signed out. The daemon tells every agent which providers are unavailable; do not route work to them.
Model ids come from the providers themselves (`agy models`, `opencode models`, `codex debug models`; Claude Code uses the
aliases opus/sonnet/haiku). Use `models` to get exact ids, and `--provider auto` to let routing pick.

## Provider facts (measured)
| provider | steer mid-turn | interrupt | notes |
|---|---|---|---|
| claude | yes (next tool boundary) | native control request, ~0.2s, no restart | live process keeps the prompt cache warm |
| opencode | no, natively queued | native abort, session and context kept | `opencode/big-pickle` is a free model |
| agy | no, natively queued | kill + resume same conversation | use `-medium`/`-high`; `-low` only for trivial chores |
| codex | yes (`turn/steer`) | `turn/interrupt` | UNVERIFIED: schema + mock only |

## Rules for agents spawned by the daemon
- Work only in your working directory. Dangerous actions (git push, deleting trees, secrets, external POSTs, global
  installs) are escalated for a decision; destructive ones are blocked and the agent is stopped.
- If you see another agent doing something harmful: `python agentctl.py stop <id> --reason "..."`.
- Verify bulk output (run tests, read diffs). Use the `hard` tier for auth, money, migrations, prod and deletes.

## Editing this repo
- Adapter contract: `orch/base.py`. New provider = one file in `orch/adapters/` + an entry in `PROVIDERS` in `orch/core.py`.
- Routing and guard rules: `orch/policy.json` (hot-reloaded, no restart).
- Do not name the CLI `orch.py`: it would shadow the `orch/` package.
- Tests: `python tests/test_guard.py` and `python tests/test_http_security.py` need no model or login. The OpenCode and Claude
  adapter tests need the real CLI; Codex uses `tests/codex/mock_app_server.py`.

## Agent notes
(append below)
