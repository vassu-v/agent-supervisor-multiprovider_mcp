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
From 0.3 they are the workspace briefing, at most about 15 lines:
- workspace and attached sessions
- up to 10 live agents as `id provider status goal paths`
- open questions
- the last 5 non-chatter posts
- providers

## Tools in 0.2

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

## Tools added in 0.3

Available from 0.3.

| Tool | Does |
|---|---|
| `board_read` | Read board posts |
| `board_post` | Announce to the board |
| `board_ask` | Ask an open question |
| `board_answer` | Answer a question by id |
| `agent_declare` | Set your goal and paths |
| `workspace_info` | Resolve a path to its workspace |
| `sessions_list` | List attached clients |

Changed tools:
- `agent_spawn` gains `goal` and `paths`.
- `agent_list` gains `scope` (`workspace` or `all`) and defaults to the client's workspace.
- Every tool result appends `board: N new since your last call` when N is above 0.

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

Never forward untrusted text to `agent_send`. Agents act on any text they receive.

Next: [collaboration.md](collaboration.md).
