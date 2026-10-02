<p align="center"><img src="docs/assets/hero.svg" alt="Switchyard: one task routed onto agy, Claude Code, OpenCode or Codex" width="100%"></p>

Coding-agent CLIs are all scriptable, but each one is scriptable in its own way, and the obvious pattern, run it
headless and kill-and-resume to change course, throws away the command that was running and re-pays for the context.

**Switchyard is a small local daemon that keeps one live session per agent, whatever the provider, and gives you one
way to spawn, watch, steer and stop all of them.** Run bulk work on cheap or free agents, keep a strong one for review,
see everything in a dashboard, and let a policy interrupt or stop risky tool calls.

```console
$ python agentctl.py route "rotate the production auth token"      # real output, copied from a run
{ "provider": "claude", "model": "opus", "tier": "hard",
  "reasons": ["sensitive keyword -> hard tier", "tier hard -> claude:opus"], "review": true }

$ python agentctl.py spawn "add retry logic to client.py and run its tests" --cwd ./api
$ python agentctl.py list                       # every agent, its status, tokens used
$ python agentctl.py tail <id>                  # what is it doing right now?
$ python agentctl.py send <id> "also cover the timeout case" --mode interrupt   # new direction, same session
$ python agentctl.py stop <id> --reason "wrong environment"
```

<p align="center"><img src="docs/assets/dashboard.jpg" alt="The Switchyard dashboard: agents grouped by directory, a pending guard decision, and provider switches" width="860"></p>

## Contents
[Install](#install) · [Providers on and off](#turn-providers-on-and-off) · [Models are discovered](#models-are-discovered-not-hardcoded) · [Routing](#routing) · [Guard](#the-guard) · [Use it from your agent](#use-it-from-your-agent) · [What each provider can do](#what-each-provider-can-do) · [Limits](#limits) · [More docs](#more-docs)

## Install

Switchyard is pure standard-library Python (3.10+). The only prerequisite is **at least one** provider CLI. You do not
need all four, and you can switch off the ones you lack. Commands below say `python`; use `py -3.10` or `python3` if that
is how Python 3.10+ is launched on your machine.

**1. Install the provider CLIs you want** (skip any you already have; review install scripts before running them).

| Provider | Install | Log in |
|---|---|---|
| **Claude Code** | Windows: `irm https://claude.ai/install.ps1 \| iex` <br> macOS / Linux: `curl -fsSL https://claude.ai/install.sh \| bash` <br> or `npm install -g @anthropic-ai/claude-code` | `claude auth login` |
| **OpenCode** | `npm install -g opencode-ai` <br> or `curl -fsSL https://opencode.ai/install \| bash` | `opencode auth login` (some models are free and need no login) |
| **Codex CLI** | `npm install -g @openai/codex` <br> or `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` | `codex login` |
| **agy** (Antigravity CLI) | Install Antigravity from Google; `agy` is installed with it. Check with `agy --version` | Sign in on first launch |

Sources and flags for each: [Claude Code](https://code.claude.com/docs/en/setup) · [OpenCode](https://opencode.ai/docs/) ·
[Codex](https://github.com/openai/codex). I could not find an official Google page with an `agy` install command, so that
row is deliberately a pointer rather than a script.

**2. Get Switchyard and start it.**

```bash
git clone https://github.com/vassu-v/agent-supervisor-multiprovider_mcp switchyard && cd switchyard
python agentctl.py serve            # leave this running; dashboard at http://127.0.0.1:8765
```

**3. See what it found.** It checks which CLIs are installed and logged in, and asks each one for its models.

```bash
python agentctl.py providers        # ready / off / not installed / needs login, with a model count
python agentctl.py models agy       # the model ids agy reports right now
```

**4. Run something.** The directory is created if it does not exist, and the agent is confined to it. Up to 8 agents can be busy at once.

```bash
python agentctl.py spawn "write primes.py, run it, reply with the output" --cwd ./demo --provider auto
python agentctl.py list
```

## Turn providers on and off

Missing a provider, or just not wanting to use one? Switch it off. It stops being routed to, and every agent is told it
is unavailable, so none will try to use it.

```bash
python agentctl.py providers disable codex
python agentctl.py providers enable codex
```

The same switch is on the dashboard, and in `orch/config.json` (optional; copy `orch/config.example.json`, which has everything
on) or through the environment: `SWITCHYARD_DISABLE=codex,agy`. Changes apply immediately, with no restart. The dashboard strip and
`providers` show one of: **ready**, **off**, **not installed**, **needs login**, **degraded**.

How agents find out:
- The MCP server states provider availability in its `initialize` instructions, so a connecting agent knows at once.
- Every agent Switchyard starts gets the current list in its rules preamble.
- Asking for a provider that is off returns an error that names the providers that *are* usable.

## Models are discovered, not hardcoded

No model name is baked into the code. Each provider is asked what it offers, and the answer is cached for 10 minutes
(`model_cache_ttl_s`), warmed in the background at startup, and refreshed on demand (`models --refresh`). It is **not**
queried on every call, which would add seconds to each spawn.

| Provider | Where the list comes from |
|---|---|
| agy | `agy models` |
| OpenCode | `opencode models` (every provider you have configured, free ones included) |
| Codex | `codex debug models` (lists models, uses no tokens) |
| Claude Code | has no list command, so the documented aliases `opus`, `sonnet`, `haiku` (always the latest); add exact ids under `extra_models` in the config |

Because the lists are live, a newly released model is usable as soon as the CLI reports it, and asking for a model a
provider does not offer fails fast with the closest real ones. Login state is checked the same way
(`claude auth status`, `codex login status`) so an installed-but-signed-out provider shows as **needs login** instead of failing
mid-task.

## Routing

`provider: auto` picks a track from a **tier**. Tiers are ordered lists of `provider:pattern` in
[`orch/policy.json`](orch/policy.json), where the pattern is matched against the models actually discovered:

```json
"bulk": { "candidates": ["agy:*flash*medium", "opencode:opencode/*free*", "claude:haiku"] },
"hard": { "candidates": ["claude:opus", "claude:sonnet", "agy:*pro*high", "codex:*"], "review": true }
```

The first candidate whose provider is on and whose pattern matches wins, and the newest matching version is chosen. Words
like *auth, secret, payment, production, delete* bump a task to `hard`, which also marks its result as needing review.
`python agentctl.py route "<task>"` shows the decision, including what was skipped and why.

## The guard

Every tool call an agent makes is checked against the same policy file (hot-reloaded):

| Verdict | Examples | Effect |
|---|---|---|
| pass | reads, writes inside the agent's directory, tests, local git | none |
| escalate | `git push`, external POSTs, writes outside the directory, `.env`/ssh keys, global installs, cloud CLIs, recursive deletes | agent interrupted, you **allow** or **deny**; no answer in 10 minutes stops it |
| block | `format`, `rm -rf /`, `reg delete HK…`, `shutdown`, sending secrets out | agent stopped at once |

Any agent, or you, can stop another agent that is misbehaving; a reason is required and every stop is logged. Agents are
told to keep notes in one shared `AGENTS.md`; if a harness writes to its own `CLAUDE.md` or `GEMINI.md`, nothing is
blocked and the new lines are mirrored into `AGENTS.md`.

## Use it from your agent

- **MCP.** Register the stdio server and your agent gets 13 tools to spawn, watch, steer, stop, and resolve escalations,
  check `providers_list` / `models_list`, and flip `provider_set`.
  ```json
  { "mcpServers": { "switchyard": { "command": "python", "args": ["/path/to/switchyard/orch/mcp_bridge.py"] } } }
  ```
  Claude Code: `claude mcp add switchyard -- python /path/to/switchyard/orch/mcp_bridge.py`. The daemon must be running.
- **Skills.** [`skills/switchyard-use`](skills/switchyard-use/SKILL.md) teaches an agent to run the fleet,
  [`skills/switchyard-setup`](skills/switchyard-setup/SKILL.md) teaches it to install and tune it. Plain markdown: copy
  them into your agent's skills folder, and edit them freely.
- **Any shell.** `agentctl.py` talks to the same daemon, so any agent that can run commands can use it.

## What each provider can do

Measured on real CLIs (Codex excepted), in one-off runs on Windows 11 with the cheapest models, so treat the numbers as indicative.

| | Steer mid-turn | Interrupt, keep the session | Notes |
|---|---|---|---|
| **Claude Code** | yes, seen at the next tool boundary | native, ~0.2 s, no restart; the running command is killed | live process keeps the prompt cache warm (29k cached tokens on turn 2) |
| **OpenCode** | no, natively queued | native `abort`, session and context kept | free models available |
| **agy** | no, natively queued | kill and resume the same conversation | no native cancel exists |
| **Codex** | yes (`turn/steer`) | `turn/interrupt` | **built from the protocol schema and a mock; never run against a real Codex** |

## Limits

- **The guard reacts, it does not sandbox.** Agents run with permission prompts off, so a tool call has started by the time
  the guard sees it. It stops or interrupts quickly, but cannot undo. For hard isolation run agents in a container or VM.
  The only provider sandbox wired in is agy's (`spawn --sandbox`, CLI only); Codex is started with full access.
- **Local only, and treat it as powerful.** The daemon binds to `127.0.0.1`; every call needs a token, and the Host and Origin
  headers must be the loopback address (so a web page cannot drive it). But its agents have full permissions: do not expose the port,
  and do not forward untrusted text to an agent. On Windows the agents are killed if the daemon is.
- **Cheaper agents make more mistakes.** Verify their output; reserve `-low` models for trivial chores.
- **Windows only for now.** Developed and tested on Windows 11. Process-tree killing uses `taskkill`, so Linux and macOS are not yet supported.
- Open items and unverified assumptions are listed in [`docs/PLAN.md`](docs/PLAN.md#open-items).

## More docs

[`tests/`](tests/) (`test_guard.py`, `test_http_security.py` need no model; the adapter tests need the real CLIs) · [`AGENTS.md`](AGENTS.md) (for any agent working in this repo) · [`docs/PLAN.md`](docs/PLAN.md) (every decision and why) ·
[`skills/`](skills/README.md) · [`orch/base.py`](orch/base.py) (the adapter contract, if you want to add a provider).
