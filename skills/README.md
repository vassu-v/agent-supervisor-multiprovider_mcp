# Skills

Two plain-markdown skills, written so any agent can read, follow and **edit** them.

| Skill | Teaches an agent to | Read it when |
|---|---|---|
| [`switchyard-use/`](switchyard-use/SKILL.md) | Run the fleet: route a task, spawn agents, watch, steer, interrupt, stop, handle escalations, verify results, announce, ask, answer and read the shared board | The daemon is already running and you need to delegate work |
| [`switchyard-setup/`](switchyard-setup/SKILL.md) | Install and configure it: start the daemon, connect providers, add the MCP bridge, tune the policy, add a provider | Nothing is set up yet, or something is not working |

## Installing a skill
Copy the folder into your agent's skills directory, for example `~/.claude/skills/` for Claude Code. The `SKILL.md`
front-matter (`name`, `description`) is what the agent uses to decide when to load it.

## Editing a skill
They are just markdown, so an agent (or you) can change them in place:
- Add a provider's quirks to the table in `switchyard-use`.
- Record what you learned under that skill's **Local notes** section so the next agent starts ahead.
- Keep the rules in step with `orch/policy.json` if you change the guard.

If you improve a skill in a way others would want, send it back as a pull request.
