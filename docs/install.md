# Install

Switchyard is stdlib-only Python 3.10+. You need at least one provider CLI, installed and logged in.
Review any install script before you run it.

## 1. Python launcher

Commands in the docs say `python`. Use the launcher that starts Python 3.10+ on your machine.

| OS | Launcher |
|---|---|
| Windows | `py -3.10` |
| macOS, Linux | `python3` |

## 2. Provider CLIs

<details>
<summary>Claude Code</summary>

| OS | Install |
|---|---|
| Windows | `irm https://claude.ai/install.ps1 \| iex` |
| macOS, Linux | `curl -fsSL https://claude.ai/install.sh \| bash` |
| Any | `npm install -g @anthropic-ai/claude-code` |

Log in: `claude auth login`. Docs: https://code.claude.com/docs/en/setup
</details>

<details>
<summary>OpenCode</summary>

| OS | Install |
|---|---|
| Any | `npm install -g opencode-ai` |
| macOS, Linux | `curl -fsSL https://opencode.ai/install \| bash` |

Log in: `opencode auth login`. Some models are free and need no login. Docs: https://opencode.ai/docs/
</details>

<details>
<summary>Codex CLI</summary>

| OS | Install |
|---|---|
| Any | `npm install -g @openai/codex` |
| macOS, Linux | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` |

Log in: `codex login`. Docs: https://github.com/openai/codex
</details>

<details>
<summary>agy (Antigravity CLI)</summary>

Install Antigravity from Google. `agy` comes with it. Check with `agy --version`. Sign in on first launch.

An honest note: no official Google page with an `agy` install command was found. This page points to the product and gives no script.
If `agy` is not on PATH, set `AGY_BIN` to its full path. agy has no login-status command, so Switchyard infers login from a non-empty model list.
</details>

## 3. Get Switchyard

```bash
git clone https://github.com/vassu-v/agent-supervisor-multiprovider_mcp switchyard && cd switchyard
python agentctl.py serve            # leave running; dashboard at http://127.0.0.1:8765
```

Change the port with `ORCH_PORT`.

## 4. Check what it found

```bash
python agentctl.py providers        # ready / off / not installed / needs login / degraded
python agentctl.py models agy       # model ids agy reports now
python agentctl.py spawn "write primes.py, run it, reply with the output" --cwd ../switchyard-demo --provider auto
```

The directory is created if its parent exists. The agent is confined to it by the guard, not a sandbox.
Missing a provider? Run `providers disable <name>`. See [providers.md](providers.md).

Next: [providers.md](providers.md).
