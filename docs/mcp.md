# MCP

The MCP bridge is a stdio server. The daemon must be running.

```bash
claude mcp add switchyard -- python /path/to/switchyard/orch/mcp_bridge.py
```

Other clients:

```json
{ "mcpServers": { "switchyard": { "command": "python", "args": ["/path/to/switchyard/orch/mcp_bridge.py"] } } }
```

## Briefing at connect

The server's `initialize` instructions tell your agent which providers are available.
They are the workspace briefing:
- workspace and attached clients
- up to 10 live agents as `id provider status goal paths`
- open questions (the first 3 are shown)
- the last 5 `done`, `blocked`, `handoff` or `changed` posts
- providers and cost notes

## Core tools

| Tool | Does |
|---|---|
| `agent_spawn` | Start an agent: task, cwd, provider, model, tier, id |
| `agent_send` | Message an agent: queue, steer or interrupt |
| `agent_list` | List agents with status and usage |
| `agent_tail` | Last N events of an agent |
| `agent_result` | Every turn of an agent |
| `agent_interrupt` | Cancel the current turn |
| `agent_stop` | Stop an agent. Reason required |
| `escalations` | List pending guard decisions |
| `escalation_resolve` | Allow or deny an escalation |
| `route_preview` | Show a routing decision |
| `providers_list` | Provider states |
| `provider_set` | Switch a provider on or off |
| `models_list` | Live model ids, with `filter`, `limit`, `refresh` |

## Board and workspace tools

| Tool | Does |
|---|---|
| `board_read` | Read board posts |
| `board_post` | Announce to the board |
| `board_ask` | Ask an open question |
| `board_answer` | Answer a question by id |
| `agent_declare` | Set your goal and paths |
| `workspace_info` | Resolve a path to its workspace |
| `sessions_list` | List attached clients |

Also:
- `agent_spawn` takes `goal` (200 characters) and `paths` (10 entries).
- `agent_list` takes `scope` (`workspace` or `all`), `ws` and `tree`. It defaults to the client's workspace.
- Every successful tool result appends `board: N new since your last call` when N is above 0. Your own posts do not count.

## CLI equivalents

| CLI | MCP tool |
|---|---|
| `spawn`, `send`, `interrupt`, `stop` | `agent_spawn`, `agent_send`, `agent_interrupt`, `agent_stop` |
| `list`, `tail`, `result` | `agent_list`, `agent_tail`, `agent_result` |
| `escalations`, `resolve` | `escalations`, `escalation_resolve` |
| `route`, `providers`, `models` | `route_preview`, `providers_list`, `models_list` |
| `announce`, `ask`, `answer`, `board` | `board_post`, `board_ask`, `board_answer`, `board_read` |
| `declare`, `ws`, `sessions` | `agent_declare`, `workspace_info`, `sessions_list` |

`who` (the briefing) has no tool. It arrives in the `initialize` instructions.

An agent's own token cannot call `agent_send`, `agent_interrupt`, `escalations`, `escalation_resolve` or `provider_set` (403).

Never forward untrusted text to `agent_send`. Agents act on any text they receive.

Next: [collaboration.md](collaboration.md).
