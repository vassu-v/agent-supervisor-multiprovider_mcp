# Policy

One file drives routing and the guard: [`orch/policy.json`](../orch/policy.json). The daemon reloads it on every decision.
A malformed edit keeps the last good policy. An error inside the guard escalates instead of passing.

## Routing tiers

`provider: auto` picks a tier, then a provider and model.

| Tier | Use for | Candidates, in order |
|---|---|---|
| trivial | Near-zero-intelligence chores | `agy:*flash*low` |
| bulk | Boilerplate, scans, drafts | `agy:*flash*medium`, `opencode:opencode/*free*`, `claude:haiku` |
| standard (default) | Normal coding | `agy:*flash*high`, `agy:*pro*low`, `claude:sonnet`, `opencode:*large*` |
| hard | Auth, money, prod, deletes | `claude:opus`, `claude:sonnet`, `agy:*pro*high`, `codex:*` |
| review | Checking another agent | `claude:sonnet`, `agy:*pro*high`, `codex:*` |

## Patterns

A candidate is `provider:pattern`. The pattern is a glob matched against the models the provider reports now.
The newest matching version wins. `*` means the provider's own default.
A candidate is skipped when its provider is off or nothing matches. The first usable one wins, so put the cheapest first.
Never hardcode an id a CLI may rename.

`bump_to_hard_keywords` pushes a task to `hard`: auth, secret, token, payment, billing, migration, production, delete, deploy, credential and more.
A `hard` tier sets `review: true`, so the agent shows `needs_review` until you verify it.

```bash
python agentctl.py route "rotate the production auth token"   # shows the decision and what was skipped
```

## Guard

Every tool call is matched against regexes when it starts.

| Verdict | Covers | Effect |
|---|---|---|
| pass | Reads, writes in the cwd, tests, local git | None |
| escalate | `git push`, `reset --hard`, global installs, external POST, cloud CLIs, `.env`, ssh keys, CI files, recursive deletes, `taskkill`, `sudo`, paths outside the cwd | Agent is interrupted; you resolve |
| block | `rm -rf /`, `format X:`, `diskpart`, `reg delete HK`, `shutdown`, uploading secrets | Agent stops at once |

Lists live under `guard.block_patterns` and `guard.escalate_patterns`. `guard.outside_cwd` is `escalate`.
Tool input is capped at 4000 characters so matching stays fast. Any tool input that names `orch/policy.json`, `orch/token.txt` or `orch/config.json` escalates, reads included.

## Escalations

```bash
python agentctl.py escalations
python agentctl.py resolve <eid> allow|deny --note "..."
```

`allow` tells the agent to continue. `deny` tells it to choose a safe alternative.
`escalation.pending_timeout_s` is 600 and `timeout_action` is `stop`. No answer in 10 minutes stops the agent.

## Stopping agents

`stop_authority` lets any agent stop any agent in its workspace. A reason is required and every stop is audited with who and why.

## AGENTS.md mirroring

`AGENTS.md` is the shared notes file. The daemon creates it in each agent's directory.
If a harness writes `CLAUDE.md`, `GEMINI.md`, `QWEN.md`, `.cursorrules` or `copilot-instructions.md` (`mirror_files`), nothing is blocked.
New lines are copied into `AGENTS.md`.
One snapshot is kept per directory. A change is mirrored once, credited to the agent when no other agent is busy there, else `unknown`.
Notes kept in SQLite and rendered per workspace are not built yet. See [ROADMAP.md](ROADMAP.md).

Next: [security.md](security.md).
