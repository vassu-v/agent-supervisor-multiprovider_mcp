# Security

Agents run with permission prompts off. Treat Switchyard as a local tool that gives agents full access to their directory.

## Network

- The daemon binds to `127.0.0.1` only, exclusively. A second daemon cannot share the port.
- Every call except `/api/health` needs a bearer token, compared in constant time.
- The `Host` header must be the loopback address. Any `Origin` must match it. This blocks DNS rebinding and cross-site requests.
- Bodies are size-capped. Agent ids match `[A-Za-z0-9_-]{1,32}`. The dashboard escapes every value and has no inline handlers.
- A CSP and `X-Frame-Options: DENY` are sent.
- On Windows, agents are killed if the daemon dies (kill-on-close Job Object).

Never expose the port. Never forward untrusted text to an agent.

## Tokens

| Token | Where it lives | Access |
|---|---|---|
| Admin | `orch/token.txt` | CLI, MCP bridge, dashboard. Full |
| Per-agent | Agent's env as `ORCH_TOKEN` | Scoped, below |

The agent token is minted at spawn (`secrets.token_hex(24)`) and held in memory. It is revoked when the agent stops or dies.
The server maps token to agent id and ignores any `by` field in the body. The identity in audit, posts and `owner` is verified.
`agentctl.py` prefers `ORCH_TOKEN`, else `orch/token.txt`.

Each agent also gets `ORCH_AGENT`, `ORCH_URL`, `ORCH_WORKSPACE` and `ORCH_PARENT`.

## What an agent can do

| Agent token allows | Agent token denied (403) |
|---|---|
| Read: health, summary, providers, models, route, list, status, tail, result, briefing, workspace, sessions, board | `/api/resolve` (approve escalations) |
| Write: announce, ask, answer, declare | `/api/provider` (switch providers) |
| `/api/stop`, with a reason | `/api/send`, `/api/interrupt` |
| `/api/spawn`, with limits | `/api/escalations`, `/api/audit` |

Reads and stops are limited to the agent's own workspace. Agents cannot call `/api/events` or `/api/workspaces` either.

Spawn limits for an agent:
- `parent` is forced to the caller.
- `cwd` must be inside the caller's workspace root.
- At most 5 live children per agent, and depth at most 3.

An agent cannot approve its own escalation, post under another name, or switch a provider.

## Board posts are untrusted text

Posts are capped at 500 characters, stripped of control characters and ANSI, and rate-limited to 6 per 10 minutes per sender.
Frames such as `[orchestrator` are neutralised. The guard runs over each post and rejects block or escalate matches.
Digests reach agents framed as information, not instructions. Only two events wake an agent: a child finishing, and an answer to its question. A stale question is passed once to the asker's parent.

## Honest limits

- The guard is detect-and-react. A tool call has started when the guard sees it. It stops quickly and cannot undo.
- Agents can read `orch/token.txt`. The guard escalates any tool input that names `orch/token.txt`, `policy.json` or `config.json`, but that is pattern matching, not a lock. The new dashboard at `/ui/` no longer embeds the token (it arrives in the URL fragment from `agentctl dashboard` and is kept in `sessionStorage`), but the old page at `/legacy` still does, and `orch/token.txt` is readable by any process of the same user. An agent that gets the token holds admin power.
- Completion hooks run commands, so setting, listing and removing them is admin-only (agent tokens get 403). They run without a shell, in the agent's directory, with a 60 s timeout; agent output reaches the command only through environment variables.
- The OpenCode server behind each agent has no password on its loopback port. Another local process could drive that agent.
- Only agy's sandbox is wired in (`spawn --sandbox`, CLI only). Codex starts with full access.
- For hard isolation, run agents in a container or VM.

Next: [mcp.md](mcp.md).
