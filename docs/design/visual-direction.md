# Visual direction: the Yard

Files: [tokens.css](tokens.css), [mockup-yard.html](mockup-yard.html) (`?theme=light|dark&density=compact`).

## 1. Concept: a signal-box track diagram, drawn as a gutter

Each workspace is a **yard** and each agent is **one row**. A rail gutter at the left draws the tree as track: the parent's line runs down and each child leaves on a turnout. The row's platform shows state the way a signalling panel does ([Swindon panel](https://reference.swindonpanel.org.uk/index.php/The_Panel_Itself), [track indications](https://www.railwaysignallingconcepts.in/tag/track-indications/feed)):
- **occupied** (busy): solid amber block
- **route set** (idle): thin line, empty node
- **hollow siding + buffer stop**: finished
- **red signal head**: held, waiting on you

Amber stands in for the panel's red "occupied" because amber already means "task" in the brand.

**Honest evaluation.** A *scenic* yard (curves, moving trains) collapses at 60 agents: labels collide and nothing scans. Rules:
- Schematic only: fixed-height rows, orthogonal lines, 8px turnouts. The gutter is at most 102px (depth limit 3).
- Text carries identity. Rails carry only structure and state, so rows stay a sortable table.
- Messages are not permanent edges. Hovering a post lights its sender and target. Only a *wake* (child done → parent) travels the rails, once.
- Each yard has its own board beside it.

**Rejected:**
1. *Force-directed graph* ([Grafana](https://grafana.com/docs/grafana/v9.0/visualizations/node-graph)): the layout shifts every refresh and leaves no room for text.
2. *Flight-strip kanban by status* ([SKYbrary](https://skybrary.aero/articles/flight-progress-strips)): it loses the tree. Its fixed-field anatomy became the row.
3. *Swimlane Gantt* ([Langfuse](https://langfuse.com/changelog/2026-08-28-responsive-timeline), [Temporal](https://temporal.io/blog/the-dark-magic-of-workflow-exploration)): it shows history, not "now". It survives as a per-row timeline.

## 2. Visual language

- **Type:** `system-ui` / `Segoe UI`; `ui-monospace` / `Cascadia Mono` for ids, paths and models. Scale 11 · 12 · 13 · 15 · 18 · 24, with tabular numerals.
- **Space:** 4px base. Rows are 48px (compact 28px). Page gutter is 16px.
- **Lines and corners:** 1px dividers, 2px rails, 6px occupied block. Radii are 3 / 6 / 10.
- **Icons:** inline SVG `<symbol>`s on a 12px grid, `currentColor`.

Colour has four jobs, and each job also has a non-colour cue:

| Job | Where | Non-colour cue |
|---|---|---|
| Provider | provider chip only, never on the rails | shape (agy ● claude ■ opencode ▲ codex ◆) + name |
| Status | platform + status word | form + glyph + word |
| Board lane | 3px rule + tint | lane title, swatch shape |
| Severity | escalation, error | signal glyph, "ESCALATE", `error · exit 1` |

**Contrast (computed).** Text needs ≥4.5 and marks need ≥3 ([WCAG 1.4.11](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast)).

| Token | Dark on #0b1020 | Light on #f5f7fb |
|---|---|---|
| text / muted / faint | 16.03 / 8.92 / 6.82 | 16.65 / 6.89 / 4.94 |
| rail | 3.70 | 3.84 |
| busy | 8.82 | 3.97 mark; busy-text >5 |
| danger | 6.84 | 6.03 (white on it 6.47) |
| agy / claude / opencode / codex | 10.86 / 8.37 / 7.45 / 7.17 | 5.05 / 5.05 / 6.25 / 6.51 |
| lane green / pink | 10.86 / 7.15 | 4.68 / 5.63 |
| text on lane tints | 12.94 / 14.42 | 16.08 / 15.43 |

The light theme darkens the brand pastels, because they fail on white. **Known clash:** busy amber against claude orange is 1.05:1. It is handled structurally (provider colour never appears on the rails), and if needed claude can shift toward coral. I did not run a colour-vision simulation ([Carbon](https://carbondesignsystem.com/data-visualization/color-palettes/) uses Color Oracle). Shapes and words are the safety net ([GOV.UK](https://brand.design-system.service.gov.uk/colour)).

**Data-ink** ([Tufte](https://faculty.cc.gatech.edu/~stasko/4460/Notes/tufte.pdf)): no row borders, axis-free sparklines, one key.

## 3. Motion

Durations are 80 / 160 / 280 / 640ms. Easings: standard `(0.2,0,0,1)`; M3 emphasized-decelerate `(0.05,0.7,0.1,1)` for entries; `(0.45,0,0.2,1)` for travel.
- **Wake:** an amber dot runs child → parent lane → parent (640ms). The parent row then tints once.
- **Status change:** 160ms crossfade.
- **Escalation:** slides in 8px (280ms). The lamp blinks twice, then stays solid.
- **Nothing loops.** Busy is a static block, and elapsed time updates on the 2s poll.
- **Reduced motion:** no travel. Sender and receiver get a static highlight, and fades are ≤120ms.

## 4. Components

```
│ ●▬▬▬▬ lead  ● busy 4m              ■ claude            ▬ ▬▬ ▬▬|  ▁▃▂▅▇█ 118k  14
│        Users API: split the work…  opus ▮▮▮▯ high       30 min    per turn     turns
├╮
│╰○───◆ api-tests ○ idle ? asked 6m   ● agy gemini-3-flash ▮▯▯▯ low
╰─▐════ db ✕ done 3m ago ↑ woke lead  (hollow siding, struck id)

═ shop  D:\code\shop  ⎇ main   5 agents · 3 busy · 1 waiting on you · 229k   [claude-code]

┃ ✓ done db ● 3m              ┃ ? question api-tests 6m [stale · passed to api]
┃ app_users migration written ┃ Which port does the mock server use?
┃ [db/001.sql] ↑ woke lead    ┃ [answer as dashboard…] [Answer]

▐ (o) ESCALATE · GIT PUSH  api wants to run  held 1m12s      [ Deny ][ Allow once ]
▐  |  git push origin feat/users-api                         ▬▬▬▬▬▬▬░ auto-stop 8:48
```
- **Spark bars:** last 12 turns, 3px wide. The latest bar is in text colour. Total is printed in mono.
- **Model/effort chip:** four rising pips plus the word. Models without effort show `· free`.
- **Provider chip:** shape, name, model count and a switch. Off means 62% opacity and a struck-through name.
- **Timeline:** busy is a 6px block, idle a 2px rail, held/error a red tick, "now" a hairline.
- **Answered question:** collapses to a dashed, untinted line.
- **Empty / loading / error:**
  - Empty: one route-set line plus a `spawn` hint.
  - Loading: grey rails with no platforms, and no shimmer.
  - Unreachable: the meta turns danger and the last data stays dimmed instead of blanking.
- **Density:** comfortable (48px, goal + paths) or compact (28px, one line).
  - Below 1100px the board moves under its yard.
  - Below 760px the timeline drops.
  - At 390px the rail pitch is 12px and rows wrap.

## Verified / not verified

**Verified:** dark, light, compact and 390px render with no horizontal scroll, no console errors and no external requests. Travel follows the rails.

**Not verified:** 60 real agents, screen readers, Firefox and Safari, CVD simulation.
