# Providers

Switchyard drives four provider CLIs: agy, Claude Code, OpenCode and Codex. Install steps: [install.md](install.md).

## States

`python agentctl.py providers` and the dashboard strip show one state per provider. The CLI prints `disabled`, `not_installed` and `needs_login` for off, not installed and needs login.

| State | Meaning |
|---|---|
| ready | Installed, logged in, models found |
| off | You switched it off |
| not installed | CLI not found on PATH |
| needs login | Installed, signed out |
| degraded | Installed, but model discovery failed |

A provider that is not ready is never routed to. Asking for it returns an error that names the usable providers.

## Switch providers on and off

```bash
python agentctl.py providers disable codex
python agentctl.py providers enable codex
```

Other ways: the dashboard, `SWITCHYARD_DISABLE=codex,agy`, or `orch/config.json`. Changes apply at once.
Copy `orch/config.example.json` to start:

```json
{ "providers": { "agy": {"enabled": true}, "claude": {"enabled": true, "extra_models": []},
                 "opencode": {"enabled": true}, "codex": {"enabled": true} },
  "model_cache_ttl_s": 600 }
```

`max_concurrent` in the same file sets how many agents run at once (default 20).

Agents learn availability three ways:
- The MCP `initialize` instructions list it.
- Every spawned agent gets the list in its preamble.
- Requests for an unavailable provider fail with the usable ones.

## Model discovery

No model name is hardcoded. Each CLI is asked, and the answer is cached 10 minutes (`model_cache_ttl_s`).
The cache is warmed at startup and refreshed with `models --refresh`.

| Provider | Source |
|---|---|
| agy | `agy models` |
| OpenCode | `opencode models` |
| Codex | `codex debug models` |
| Claude Code | Aliases `opus`, `sonnet`, `haiku` |

Claude Code has no list command. Add exact ids under `extra_models`. A model the provider does not offer fails fast and lists the closest real ones.
Login state comes from `claude auth status` and `codex login status`. OpenCode and agy are inferred from a non-empty model list.

## Measured capabilities

Measured on real CLIs in one-off runs on Windows 11 with the cheapest models. Treat the numbers as indicative.

| | Steer mid-turn | Interrupt, keep session | Notes |
|---|---|---|---|
| Claude Code | Yes, at next tool boundary | Native, 0.2 s, no restart | 29k cached tokens on turn 2 |
| OpenCode | No, queued | Native `abort` | Free models exist |
| agy | No, queued | Kill and resume | No native cancel |
| Codex | Yes (`turn/steer`) | `turn/interrupt` | Never run against real Codex |

Killing an agent kills the command it was running, on all three measured CLIs.
Use agy `-medium` or `-high`. Keep `-low` for trivial chores.

Next: [policy.md](policy.md).
