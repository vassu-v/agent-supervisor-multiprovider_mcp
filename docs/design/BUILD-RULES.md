# Dashboard build rules (shared by every agent building the new UI)

The two design docs are the spec: [visual-direction.md](visual-direction.md) (what it looks like) and [ui-architecture.md](ui-architecture.md)
(structure, data, modules). This file only settles conflicts and sets the working rules.

## Reconciliations (these override the design docs)
1. **Effort is real now.** Agent rows already carry `effort` (requested), `effort_applied` and `effort_warning` (see `AgentRec.info()` in
   `orch/core.py`), and `/api/models` entries carry `efforts`. Do NOT parse effort from model ids. Show `effort_applied` as the pips and the word;
   when `effort_warning` is set show a small marker with the text in the title. For agy, effort lives in the model id (`...-flash-high`): that is
   already reflected in `effort_applied`.
2. **The visual reference is `docs/design/mockup-yard.html`** (+ `tokens.css`). Port its markup patterns and CSS faithfully: rail gutter, row,
   platform states, escalation bar, board lanes, chips. `orch/ui/theme.css` = `tokens.css` copied verbatim (the mockup inlines it; the real UI links it).
3. **Rows are real data**, so every dynamic value goes through `h()` text nodes. The mockup's inline JS is a demo only; do not copy its patterns that
   use `innerHTML`.
4. **Existing behaviour must not regress**: tabs per workspace, agent tree, both board lanes with answer/post forms, escalations allow/deny, provider
   toggles, interrupt/stop. The old `orch/dashboard.html` stays in place and served at `/legacy` until the new UI reaches parity; it is deleted last.

## Ownership (one owner per path; never edit a path you do not own)
| Owner | Paths |
|---|---|
| E1 core | `orch/ui/index.html`, `orch/ui/styles.css`, `orch/ui/theme.css`, `orch/ui/js/app.js`, `orch/ui/js/core/*`, `tests/ui/test_core.mjs` |
| E2 server | `orch/ui/js/net/*` (api.js, changes.js), `orch/server.py`, `orch/core.py` (additions only), `agentctl.py` (`dashboard` command), `tests/ui/fixtures/*`, `tests/test_ui_server.py` |
| E3 fleet | `orch/ui/js/views/fleet.js`, `orch/ui/js/views/escalations.js`, `orch/ui/js/components/fleet-*.js`, `orch/ui/js/components/esc-*.js`, `tests/ui/test_fleet.mjs` |
| E4 drawer+timeline | `orch/ui/js/views/drawer.js`, `orch/ui/js/views/timeline.js`, `orch/ui/js/components/drawer-*.js`, `orch/ui/js/components/timeline-*.js`, `tests/ui/test_drawer_timeline.mjs` |
| E5 board/models/audit | `orch/ui/js/views/board.js`, `models.js`, `audit.js`, `orch/ui/js/components/{board,models,audit}-*.js`, `tests/ui/test_board_models.mjs` |
| lead | everything else (docs, git, final integration) |

## Rules
- Plain ES modules, no build step, no dependencies, no external requests, no frameworks, no network except the daemon's own `/api/*`.
- **Security bans** (a test greps `orch/ui/**` for them): `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function`,
  `on[a-z]+=` attributes, `javascript:`, `style=` attributes, `http://` / `https://` strings. Create elements only with `h()`; set layout with
  `el.style.setProperty(...)` (CSSOM). Agent-authored text (goals, posts, last_text, stream lines) is ALWAYS a text node.
- Accessibility is a requirement, not polish: landmarks, roving tabindex in the fleet treegrid, `role=dialog` drawer that traps and restores focus,
  `aria-live` for escalations (assertive) and new questions (polite), every action keyboard-operable, status never colour-only, `prefers-reduced-motion`.
- Keep it fast and light: no layout reads during render, keyed reconciliation (never rebuild a list). There are no hard size or timing targets.
- Pure logic goes in pure functions with `node --test` tests (`node` may be missing: tests must `skip` cleanly then). No DOM needed for logic tests;
  where DOM is needed use the tiny fake-document shim E1 provides in `tests/ui/shim.mjs`.
- Python 3.10 stdlib only on the server side. Do not run model turns. Do not start a daemon on port 8765 (a real one may run); for live checks use
  `tests/harness.py` (`start_daemon`, isolated, fake provider) and the Browser tools.
- **No git commands that change state** (add/commit/checkout/stash/reset). The lead commits.
- When you finish: run your tests, then report in <200 words: what you built, test results, deviations from the design (with the reason), and anything you
  could not verify. Do not silently deviate from an interface in ui-architecture.md section 4; if one is wrong, say so in the report.
