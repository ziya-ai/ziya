# Visual refresh — "Paper & Ink"

Decision record for the 2026-10 design pass. Mocks and discussion live in
the "Design Evolution / its time to consider the general design aesthetic"
conversation; this file holds only what was decided and why, so the
implementation stages can be checked against it.

## Decisions

### Palette and surfaces

Light mode becomes a warm-neutral *paper* with an explicit surface ladder,
mirroring the ladder dark mode always had.  Four paper tones are selectable;
**linen** is the default because it keeps the paper feel without yellowing
the teal/magenta brand colours or shifting diff reds and greens.

| token | linen (default) | warm | cool | white |
|---|---|---|---|---|
| canvas (chat) | `#f7f6f3` | `#f6f3ee` | `#f4f5f4` | `#ffffff` |
| sidebar | `#f1f0ec` | `#f1ede6` | `#eef1ef` | `#f6f6f6` |
| surface (cards, composer, header) | `#fcfbf9` | `#fbf9f5` | `#fafbfa` | `#ffffff` |
| inset / selected | `#efeeea` | `#efebe3` | `#eaeeec` | `#f0f0f0` |
| human turn | `#efeeea` | `#efebe3` | `#eaeeec` | `#f0f0f0` |
| hairline border | `#e2e0da` | `#e3ddd2` | `#dfe3e0` | `#e5e5e5` |
| control border | `#d8d6d0` | `#d9d3c8` | `#d3d7d4` | `#d9d9d9` |

Dark mode lifts off AntD's `#141414` void onto a slightly cool ladder with
borders two steps up.  The sidebar is *darker* than the chat canvas, the
inverse of light mode: in the dark, the reading surface is the lit one.

| token | dark |
|---|---|
| canvas | `#1c1f24` |
| sidebar | `#171a1e` |
| surface | `#252930` |
| inset / selected | `#2d323a` |
| human turn | `#2a2f37` |
| hairline / control border | `#434a55` / `#4a515c` |
| text / muted / faint | `#e8eaed` / `#9aa0a8` / `#7d848e` |
| header gradient | `#241a28 → #132a28` |

Accent is **teal only** (`#0d9488` light, `#2dd4bf` dark) and replaces AntD
blue as `colorPrimary`.  Magenta appears in the brand mark only.  The token
bar's segments are a cool analogous set (option E, 2026-10-20): files teal
(dark: `#1fb3a0`, a step below the neon accent so the bar recedes), chat sky
(`#2f7fc1` / `#5aa9e6`), tools lavender (`#7b6fb3` / `#9b8fd1`), ast grey.
Teal + magenta vibrated on the dark surface and magenta sat too close to the
slate tools slice.  Orange stays reserved for the budget-zone outline
(`--z-tok-warning`, `#f97316` / `#ff9a2e`) and errors; red for danger;
fuchsia (`#a21caf` / `#e879f9`) for remote/shared indicators.  Text on teal buttons is
dark (`#0b2a26`); white fails contrast on `#2dd4bf`.

### Type

AI response prose is set in a serif: `Charter, 'Iowan Old Style', Georgia,
serif` (Charter was designed for low-resolution screens, is narrower than
Georgia, and ships on every Mac; Windows falls back to Georgia).  UI text
moves from Arial to the system sans.  Tree, code and diff use `ui-monospace`.
Font is a user preference (Charter / Georgia / Palatino / system sans).

### Turn separation

Human turns: full-width band, 3px teal left rule, faint tint.  AI turns: open
on the canvas, no box.  Sender and time collapse to one 11px line.  No gutter
column, no indent beyond the rule; both turns' text starts at the same x.
Differentiation is by *shape*, not by a one-step grey difference.

### Header

Bench / Shell / Tasks / Memory gain text labels (collapse to icons under
~1100px).  The theme bulb becomes an **Appearance** popover holding the six
global visual preferences (theme, light paper, response font, density, prose
width cap, UI scale).  Ephemeral chat folds into **New ▾**.  The header's
empty middle shows a breadcrumb `project / conversation title` — a label,
not a control; the sidebar project switcher is the only place to change
project.  Nothing else leaves the header.

Rejected: a global Settings drawer absorbing model, project, shell and MCP.
Those are context-scoped navigation, not preferences, and moving them would
make them harder to reach.  Also rejected: the two-tone dark-sidebar light
theme ("Studio").

### Sidebar / Chats tab

- Folders stay above chats at every depth (pinned first, then active, then
  by activity — not alphabetical: 127 folders alphabetical buries the live
  ones) — this encourages filing.  Root-level chats sit under an
  **"N unfiled"** divider.  Trade accepted: a just-touched unfiled chat sits
  below idle folders; it used to interleave by activity.
- Every chat row is **one line** in both densities.  Status lives in the
  trailing slot in a fixed order: state glyph · beads · relative time.
  Run-status gears render as `⚙N` in `runStatusVocabulary` colours; the two
  chips shorten in Compact (`? Waiting`, `🔧 Held`) with full text in the
  tooltip.
- Leading edge keeps today's layout: the chat-bubble glyph (20px Comfortable,
  16px Compact) at the same x as a folder icon at the same depth, the green
  "finished while you were away" check inline before the title, the
  flag-colour dot in the trailing slot.  **Rejected:** a reserved leading
  gutter for the check.  Three variants were mocked (title at the folder-name
  x, at icon+7, at the icon x); all made root chats read as children of
  nothing or collided flag dot with check in a 13px slot.
- Collapsed folders bubble up a check / running gear when a child has one.
- Folder badge format `(N · M folders)` / `(empty)` is unchanged.
- Density: Comfortable = 36px rows; Compact = 26px rows.  Row height is the
  only structural difference.

### Collapse handle

The floating header `.panel-toggle` is replaced by a tab on the panel
divider: click collapses, drag resizes (it is the existing `PanelResizer`
edge).  Collapsed, the tab hugs the left screen edge in accent colour.  With
the panel collapsed a 28px strip above the composer carries project, model,
active skills (with `+N` overflow) and the token bar.

### Titles (finding, not styling)

`deriveTitleFromContent(content, dynamicTitleLength)` persists a slice sized
to the sidebar width at creation time (`MUIChatHistory` ResizeObserver,
`width/6` clamped 30–80), which is why stored titles carry `...` mid-screen.
Fix: store the full first line; truncate at render only.

## Stages

1. **Done.** Token system (`styles/theme-tokens.css`), appearance preferences
   (`utils/appearance.ts`), Appearance popover, header labels, New ▾,
   breadcrumb, teal `colorPrimary`, Charter prose.
2. **Done.** Chats tab rows: trailing-slot order, `⚙N` gears, compact chips,
   relative time, density row heights, teal selected-row rule, folders-first
   partition with the "N unfiled" divider.  Folder badge bubbling added
   2026-10-22 (`rollUpFolderStatus` in `utils/chatTreeFolderBadge.ts`,
   rendered on COLLAPSED folder rows only: ⟳ processing count, ✓ unread
   count, then the descendants' summed run-status counts through the same
   `RunStatusGears` the chat rows use, so "Waiting" / "Held" chips bubble
   automatically; expanded folders show it on the children).  Computed at
   render time from the visible rows, not stored on the tree, so neither
   tree-build path nor its cache hashes change.  Not done: a light-mode
   column in `runStatusVocabulary` — the dark-tuned colours are used as-is
   on paper for now (mocked 2026-10-22; same hue, one darker step each).
3. **Done.** Divider tab (18px, layered above the resizer so the whole tab
   collapses); collapsed strip as option A — 28px above the composer in a
   sticky dock, fed by `utils/panelStatus.ts` (FolderTree publishes model,
   TokenCountDisplay publishes the token breakdown) and the
   `useActiveSkillRows` hook shared with ActiveContextBar.  Option B (header
   middle) was rejected: with the breadcrumb in place the title fell to ~20
   chars and skills vanished.  Also done this stage: header left-justified;
   the order toggle demoted from a labelled primary button at the left edge
   to an icon beside Appearance.  Token bar: the shared `TokenBreakdownBar`
   (files · chat · tools · ast in `--z-tok-*` colours, `--z-border-strong`
   track, red past danger) renders in both the strip and the sidebar
   footer, which moved to the panel bottom under the model row and dropped
   the AntD Progress and "Token Estimate" heading.
4. **Done** for new conversations.  `dynamicTitleLength` and its
   ResizeObserver in `MUIChatHistory` are now dead plumbing to remove.

### Selection vs pointer (Chats tab)

The open conversation: `--z-accent-soft` fill + 3px **solid** teal rule +
600 weight.  The target folder (where New chat / New folder land):
`--z-inset` fill + 3px **dashed** teal rule.  One device, two strokes.  The
first cut gave the chat the inset fill and the folder the teal wash, which
on linen inverted the hierarchy (inset on sidebar is a 1-step difference).

## Resolved

- AST token-bar slice moved from bead amber to `--z-text-muted`; beads keep
  amber to match the BeadTree chip.
- Footer budget block: legend figures in the text colour (not green per
  number), spenders spread edge to edge (files · tools · chat · ast), total
  row `n / m` left and `n%` right, and the budget zone drawn as a 1.5px
  outline round the whole bar (`--z-tok-warning` from 70%, `--z-tok-danger`
  from 90%) with the total row in the same colour.  The ActiveContextBar's
  own "N files · N tokens" gauge (named contexts only, hard-coded 200k) was
  removed; the footer is the one budget surface.
- `dynamicTitleLength` and its ResizeObserver removed from ChatContext,
  ActiveChatContext, MUIChatHistory and PrintFeasibilitySpike.
- The suites that appeared to fail with `document is not defined` / jest-dom
  matchers were a runner artefact: bare `npx jest` skips `src/setupTests.ts`.
  Use `CI=true npx craco test --watchAll=false`.  `@jest-environment jsdom`
  docblocks were added to the 13 DOM suites that lacked them regardless.

## Open

- Flag colour as a left rule instead of a gutter dot (collides with the
  current-row rule).
- Bundled serif (Source Serif 4 / Literata) if Charter's Windows fallback
  proves unsatisfying; ~100 KB font file.
