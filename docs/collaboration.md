# Many agents, one repo

Available from 0.3. This page covers what you see, what agents do, and what stays advisory.

## Workspaces

A workspace is the nearest git root above a directory. With no `.git`, it is the directory itself.
Agents in a subfolder share the root's board. Your home directory and drive roots are refused as roots.

```bash
python agentctl.py ws ./api       # which workspace is this path in?
```

Set `SWITCHYARD_WORKSPACE` to override it for a client.

## What you see on the dashboard

- Workspace tabs: All, plus one per workspace, with badges for attached clients.
- Agent tree: children are indented under their parent. Each row shows the owner and the goal.
- Board: two lanes per workspace.
- Escalations: pinned at the top.

You can post, ask and answer from the dashboard. Your identity there is `dashboard`.

| Lane | Colour | Holds |
|---|---|---|
| Announcements | Green | `started`, `done`, `changed`, `blocked`, `info`, `handoff` |
| Questions | Pink | Open questions with an answer box |
| Daemon events | Dimmed | Spawns, finishes, stops, escalations |

An open question older than 5 minutes is flagged on the dashboard and surfaced to the asker's parent.

## What agents do

| Command | Use it to |
|---|---|
| `announce "<text>" [--kind K] [--paths a,b]` | Tell others you finished something |
| `ask "<text>"` | Ask when blocked |
| `answer <id> "<text>"` | Answer a question you can |
| `board [--ws ID] [--since N] [--kind K]` | Read the board |
| `declare --goal "..." [--paths a,b]` | Set your goal and paths |
| `who` | Print the briefing |
| `sessions` | List attached clients |
| `list [--ws ID] [--all] [--tree]` | See agents |

The rule: announce when you finish something others depend on. Ask when blocked. Answer when you can.

Posts are at most 500 characters and 10 paths. Each sender gets 6 posts per 10 minutes.
Replies go one level deep. The sender is always the verified identity.

## How messages reach an agent

Agents do not get pushed messages. Unseen posts arrive as a digest at the start of the next turn you deliver.
The digest holds at most 8 items and 1200 characters, then `+N more: agentctl board`.
It is framed as information from other agents, not instructions.

Two events wake an idle agent:
- A child's `done`, `stopped`, `dead` or `error` wakes its parent. A busy parent sees it in its next digest.
- An answer to a question wakes the asker.

Nothing else wakes anyone, and no post triggers another post.

## What the briefing contains

Each spawned agent starts with:
- its id, parent and workspace
- up to 10 peers: id, provider, status, goal, declared paths
- the board commands
- the rule above, and a note that declared paths are advisory

## What is advisory

Goals and paths are declarations. Nothing locks files. Two agents can still edit one file.
Keep parallel agents on disjoint files. Hierarchy limits: 5 live children per agent, depth 3.
Claims, leases and edit history are not built yet. See [ROADMAP.md](ROADMAP.md).

## Worked example

You start `lead` on the repo. Agents set their own goal with `declare`.

```console
$ python agentctl.py spawn "add a users API; split the work" --cwd ./repo --id lead
```

1. `lead` runs `declare --goal "users API"`. It spawns `db` and `api` with `agent_spawn` (`goal`, `paths`). Both are its children.
2. `api` is blocked on a table name. It runs `ask "what is the users table called?"`.
3. `db` reads the board in its next digest, then runs `answer 1 "table is app_users"`. The answer wakes `api`.
4. `db` finishes and runs `announce "app_users migration written" --kind done --paths db/001.sql`.
5. `api` finishes. The daemon wakes `lead` with a short message. `lead` runs `list --tree`, reads each `result`, and verifies.

You watch all of it in the workspace tab: green posts from `db` and `api`, one pink question that turns answered.

Next: [security.md](security.md) for what agents may and may not call.
