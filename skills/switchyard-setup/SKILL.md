---
name: switchyard-setup
description: Install, configure and troubleshoot the Switchyard agent orchestrator: start the daemon, check which provider CLIs (agy, claude, opencode, codex) are usable, add the MCP bridge to a client, tune routing and guard policy, add a new provider adapter. Use when Switchyard is not running, a provider is missing, or you want to change how it routes or guards.
---

# Setting up Switchyard

Requirements: Python 3.10+. No packages to install. At least one provider CLI installed **and logged in**. Commands say `python`;
use `py -3.10` or `python3` if that is how Python starts on this machine.

**Install a missing provider CLI.** The per-OS commands, login steps and the agy note are in `docs/install.md`. Review install
scripts before running them; the user must agree to installs.

The user may not want or have every provider; offer `providers disable <name>` instead of installing.

## 1. Start the daemon
```bash
cd <repo> && python agentctl.py serve
```
Run it in its own terminal and leave it. Default port 8765 (`ORCH_PORT=...` to change). Health:
`curl http://127.0.0.1:8765/api/health` returns `{"ok": true, "providers": [...]}`. Dashboard: http://127.0.0.1:8765/

Note: an agent platform may kill background commands after a time limit. If the daemon keeps dying, run it from your own
terminal, or as a Windows scheduled task / systemd service, which needs the owner's approval to create.

## 2. Check and choose providers
```bash
python agentctl.py providers        # ready | off | not installed | needs login | degraded, with a model count
python agentctl.py models <name>    # the model ids that provider reports right now
```
Each state means: **not installed** (CLI not found), **needs login** (installed, signed out), **degraded** (installed but model
discovery failed), **off** (switched off), **ready**. To install a missing CLI, see `docs/install.md`; log in with
`claude auth login`, `opencode auth login`, `codex login`, or sign in to agy on first launch.

Switch providers off that the user lacks or does not want, so they are never routed to and agents are told they are unavailable:
```bash
python agentctl.py providers disable codex      # or: SWITCHYARD_DISABLE=codex,agy   or edit orch/config.json   or the dashboard
```
Models are discovered from the CLIs (`agy models`, `opencode models`, `codex debug models`; Claude Code uses aliases
`opus`/`sonnet`/`haiku`, plus any `extra_models` in the config) and cached 10 minutes. Force a re-query with
`models --refresh`. If agy is not on PATH, set `AGY_BIN`.

## 3. Smoke test (cheap)
```bash
python agentctl.py models opencode        # pick a free or cheap model id from the live list
python agentctl.py spawn "Reply with the single word OK" --cwd ./smoke --provider opencode --model <id from above> --id smoke
python agentctl.py tail smoke      # then: python agentctl.py stop smoke --reason "smoke test done"
```
Use the cheapest model per provider for tests (OpenCode lists free models, ids ending in `-free`; Claude Code: the `haiku` alias).

## 4. Connect an agent
- **MCP:** register the stdio server `python <repo>/orch/mcp_bridge.py` in the client. Claude Code:
  `claude mcp add switchyard -- python <repo>/orch/mcp_bridge.py`. The daemon must be running.
- **Skills:** copy `skills/switchyard-use` into the agent's skills directory.
- Tool list: `docs/mcp.md`. The MCP `initialize` instructions are a workspace briefing (peers, open questions).

## 5. Tune policy (`orch/policy.json`, hot-reloaded, no restart)
- `routing.tiers.<tier>.candidates`: ordered `provider:pattern` list. The pattern is a glob matched against the **discovered** model
  list (newest version wins) or an alias like `sonnet`; `*` means the provider's default. First usable candidate wins, so put the
  cheapest suitable first. Never hardcode a model id that a CLI may rename.
- `routing.bump_to_hard_keywords`: words that push a task to the `hard` tier.
- `guard.block_patterns` / `escalate_patterns`: regexes tested against every tool call. Block stops the agent; escalate
  interrupts it and waits for `resolve`.
- `escalation.pending_timeout_s` and `timeout_action`: what happens to unanswered escalations.
- `mirror_files`: harness notes files whose new lines are copied into `AGENTS.md`.
Full reference: `docs/policy.md`, `docs/providers.md`, `docs/security.md`.
The guard is detect-and-react, not a sandbox. For hard limits use the provider's own sandbox.

## 6. Add a provider
1. Create `orch/adapters/<name>.py` with a class subclassing `Adapter` from `orch/base.py`. Read the contract in that file's docstring.
2. Emit only the normalised events; one `result` per turn; kill must kill child processes (`kill_tree`).
3. Register it in `PROVIDERS` in `orch/core.py`, and add it to a tier in `policy.json`.
4. Measure, do not assume: does a mid-turn message queue or inject? Does interrupt keep the session? Does it kill the
   running command? Set `capabilities` from the answers and write a test in `tests/<name>/`.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| `agentctl.py` cannot connect | Daemon not running or wrong `ORCH_URL`/port |
| Provider shows `not installed` / `needs login` | CLI not on PATH (agy: set `AGY_BIN`), or signed out: log in, then `models --refresh` |
| `model ... is not offered by ...` | The id is not in the live list: run `models <provider>` and use one of those |
| Agent stuck `busy` | Slow model or long command: `tail <id>`. Free models can take a minute |
| `401 bad or missing token` | `ORCH_TOKEN` does not match `orch/token.txt` |
| `403` from an agent | Agent tokens cannot resolve escalations, switch providers, send or interrupt; ask the user |
| Need an isolated daemon for tests | `SWITCHYARD_HOME=<dir>` keeps logs, db, token and config there; `SWITCHYARD_FAKE=1` adds a model-free `fake` provider |
| agy interrupt shows `restarts` | Expected: agy has no native cancel, so interrupt is kill + resume |

## Local notes
(edit freely)
