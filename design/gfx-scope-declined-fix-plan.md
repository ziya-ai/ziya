# Plan: recover the 36 scope-declined GFX fixes

**Status:** proposal, not started
**Source of truth:** `.ziya/gfx-sweep/backlog.json` (291 defects, field `blocked_by_scope`)
**Derived:** 2026-08-28

---

## 1. What the artifact history actually contains

I scanned all 186 task-run records for the project (via the running server's
`/api/v1/projects/{id}/task-runs`, since the on-disk run records are encrypted at
rest) plus the plaintext campaign state in `.ziya/`.

Scope-decline language appears in 32 runs, but almost all of it is **not** what
was asked for. Two classes must be separated:

| Class | Count | Is it a declined-but-clear fix? |
|---|---|---|
| Deploy/write-path infrastructure failures (music-notation campaign runs 3/4/5) | ~20 snippets | **No.** The fix was *authored* but could not be *verified*, because the served bundle lived in a different checkout (`ZiyaInternal-ws`, then site-packages `app/templates/`) and `npm`/`cp` were policy-blocked. This is a harness defect, already superseded. |
| Research-boundary "out of scope" in the competitive-landscape cards (CL1–CL6) | ~50 snippets | **No.** "Out of scope for tool X" is a capability-grid verdict, not a declined fix. |
| **Root-caused defects whose confirmed fix site was outside the granted writable paths** | **36** | **Yes. This is the answer.** |

The 36 live in `backlog.json` under a dedicated field, `blocked_by_scope`, with
`status: "open"`. Every one of them records: the confirmed fix site (file +
function + line), the specific change required, and an explicit statement that a
workaround at an in-scope site was *considered and rejected* as a non-root patch.
They are the highest-quality backlog in the project — diagnosis is already paid
for; only the write permission was missing.

Two further items (`D-015`, `D-032`) are `blocked_by_scope` **and**
`fix-applied` — partial in-scope fixes with a documented scope-blocked residue.
They belong in Batch 3/6 below, not in the 36.

Separately, 22 defects are `status: "deferred"` by the card's **severity gate**
(all `severity: low`). Those were deferred by *policy*, not by scope, and the
gate worked as designed. They are Batch 8 — lowest priority.

---

## 2. Systemic root cause of all 36 declines

This is one mistake, not 36. The GFX Stage 2 card granted write access to
`frontend/src/plugins/d3` — but that directory holds **thin delegating shims**,
while the engine logic lives in `frontend/src/utils/d3Plugins/`:

```
frontend/src/plugins/d3/musicPlugin.ts        121 lines   ← GRANTED
frontend/src/utils/d3Plugins/musicPlugin.ts  6053 lines   ← where every defect is
```

`plugins/d3/musicPlugin.ts:9` imports `isMusicSpec, resolveMusicSpec,
renderMusicSpec` from the core. Same split for `packetPlugin` (655 vs 971 lines).
So the card was granted the *dispatcher* and denied the *engine*. Every agent
correctly refused to reimplement engine logic in the shim, and correctly refused
to write a test that could only certify unpatched behaviour.

The card description already half-noticed this ("widen the fix/residue task
scopes to cover them ... in the editor before signing") but the widening covered
`frontend/src/styles` and `frontend/src/components` only — not
`frontend/src/utils`, which is where 28 of the 36 live.

**The required scope union is exactly 8 files:**

```
frontend/src/utils/d3Plugins/musicPlugin.ts
frontend/src/utils/d3Plugins/packetPlugin.ts
frontend/src/utils/d3SpecParser.ts
frontend/src/utils/colorUtils.ts
frontend/src/utils/domSanitize.ts
frontend/src/components/MarkdownRenderer.tsx
frontend/src/styles/mermaid-theme.css
frontend/src/index.css
```

Plus, for the `D-015`/`D-032` residues: `frontend/src/components/D3Renderer.tsx`,
`frontend/src/utils/mermaidThemeNormalize.ts`,
`frontend/src/components/PrintRenderPage.tsx`.

---

## 3. Blockers to clear before any batch runs

1. **A GFX Stage 2 run is live.** Run `040e827e` is `status: running`, last
   activity 5 minutes ago, and rewrote `backlog.json` at 22:52. It is currently
   reading `frontend/src/plugins/d3/mermaidEnhancer.ts`. Do **not** start any
   batch until it reaches a terminal state — concurrent writes to
   `backlog.json` will lose defect status, and concurrent edits to the same
   engine files will collide.
2. **`frontend/src/utils/d3Plugins/musicPlugin.ts` is dirty** (uncommitted `M`),
   as are 10 of its test files. There is also a stray untracked
   `musicPlugin.ts.new`. Commit or stash before Batch 1, and delete the `.new`
   file — the `G-24_incident` note in `backlog.json` records an earlier run
   losing uncommitted work by overwriting a working-tree file with HEAD.
3. **Rebuild is mandatory to verify anything.** Fixes are invisible to
   `render_diagram` until `npm run build` copies into `templates/`. Batches must
   carry the `npm` shell grant with a ≥1200s timeout, or they will repeat the
   music-campaign failure of authoring unverifiable fixes.

---

## 4. Batches

Ordered by (leverage ÷ risk). Each batch is one card; each is independently
verifiable; none shares a fix site with another, so they can be signed
separately and, after Batch 0, run in parallel where noted.

### Batch 0 — do first (2 defects, 2 high)

> **CORRECTED 2026-08-28 after empirical verification.** The `backlog.json`
> hypothesis for D-025 is mechanically wrong and its proposed remedy is a
> no-op for the commonest diagram type. Superseding analysis below.

#### D-033 — `frontend/src/index.css:1234` (confirmed, ready)

Darken `body:not(.dark) .token.keyword` from `#d73a49` to `#b31d28`.

The catalogued figure (4.30:1 on `#f6f8fa`) understates it: these light
`.token.*` rules also apply to diff code, so the real surface set is
white / `#f6f8fa` / `#e6ffec` / `#ccffd8` / `#ffebe9` / `#ffdce0`, and the
**worst case is 3.61:1 on a diff delete line**. Measured (WCAG):

| candidate | white | #f6f8fa | #e6ffec | #ccffd8 | #ffebe9 | #ffdce0 | worst |
|---|---|---|---|---|---|---|---|
| `#d73a49` current | 4.57 | 4.30 | 4.33 | 4.11 | 3.99 | **3.61** | FAIL |
| `#c02d3c` | 5.70 | 5.36 | 5.40 | 5.12 | 4.97 | **4.50** | borderline |
| **`#b31d28`** | 6.72 | 6.32 | 6.37 | 6.04 | 5.86 | **5.30** | **PASS** |

`#c02d3c` sits exactly on the boundary, so `#b31d28` is the safe choice.

#### D-025 — root cause is NOT what the backlog says

The backlog claims the fix is adding `!important` to
`.dark .mermaid-container .node .label text { fill:#000000 }` so it beats
mermaid's white label. Three findings disprove this:

1. **`flowchart.htmlLabels: true`** (`mermaidPlugin.ts:501`). Flowchart node
   labels are HTML `<span>`s inside `foreignObject`, not SVG `<text>`. The
   named selector requires a `text` descendant, so it **cannot match flowchart
   labels at all** — adding `!important` is a no-op for the commonest type.
2. **The label colour is `#eceff4`, not `#ffffff`** (`primaryTextColor`,
   `mermaidPlugin.ts:457`), measuring **3.50:1** on `#5e81ac` — worse than the
   catalogued 4.03:1.
3. **No single fill satisfies both label colours.** `#eceff4` needs a fill at
   or darker than `#4a6893` (4.93:1); `#000000` needs `#5e81ac` or lighter
   (5.21:1). At `#4c6f9c` both fail (4.49 / 4.06). "Darken the fill" and
   "force a black label" are therefore mutually exclusive, and the backlog's
   framing of them as interchangeable options is unsound.

**Actual root cause — a seam defect.** The contrast pass
(`enhanceSVGVisibility`, called on the inline path at `mermaidPlugin.ts:676`)
reads the node background via `bg.style.getPropertyValue('fill') ||
bg.getAttribute('fill')`. Neither reflects a **stylesheet** rule, and the
`fill:#5e81ac !important` in `mermaid-theme.css` (~line 63) is exactly that.
So the pass cannot observe the paint it is supposed to contrast against, falls
back to the theme default `#eceff4`, and leaves the label at 3.50:1.

Proven by render probe: in one dark flowchart, nodes whose fill came from an
author `classDef` (emitted as an inline style, therefore *readable*) got
correct labels — dark text on pale `#fdf6b2`, light text on near-black
`#101820` — while the overlay-painted `#5e81ac` node kept a light label. The
machinery works; it is blind to CSS-applied fills.

**Recommended fix:** remove the `fill: #5e81ac !important` declaration from
the dark node-styling block in `mermaid-theme.css`, leaving `stroke`. The fill
then falls back to mermaid's own `mainBkg: #3b4252`, against which the
`#eceff4` fallback label measures **8.73:1**. This removes the
disagreement at its source rather than tuning one side of it, and eliminates
the whole class of "contrast pass blind to CSS paint" bugs for mermaid nodes.

Sibling defect: **D-134** is the same failure mode one layer down
(`getOptimalTextColor` failing open to white on an unparseable paint). Batch 3
should be sequenced with this in mind.

**Verification gap:** a CSS change cannot be confirmed through
`render_diagram` until the frontend bundle is rebuilt and deployed, and `npm`
is policy-blocked. Do not mark D-025 resolved on source inspection alone —
this is precisely the trap the music-notation campaign fell into for eight
iterations.

### Batch 1 — music engine core (18 defects, 7 high)
`frontend/src/utils/d3Plugins/musicPlugin.ts` only.

Highest-value first; the first three are one-line coercions:
- **D-180** `keys:'c/4'` scalar string hangs the render (30s timeout, total data
  loss). Fix: `typeof keys === 'string' ? [keys] : keys` at `buildNoteString`
  (:1715, confirmed `for (const k of n.keys)` at :1888 with no coercion).
- **D-179** `notes:[[...],[...]]` nesting-off-by-one degrades to rests via the
  no-keys→rest path (:1738 `if (n.rest || !n.keys?.length)`). Fix: one flatten pass.
- **D-178** no field-alias layer, so `{pitch,dur}` renders four **quarter rests**
  where four notes were written — a legible, structurally valid, 100% wrong score
  with no warning. Fix: alias normalisation in/before `resolveMusicSpec` (:2251).
- **D-182/D-184/D-185** layout: flat `notes[]` never wraps; explicit `width`
  disables wrapping; author dimensions clamp without reflow and delete content.
- **D-183/D-186/D-187** draw-loop height misallocation, voice overprint, beam geometry.
- **D-188..D-194** ornament/overlay/text-band/grace geometry.
- **D-196** light-theme stave line `#999999` = 2.85:1 on white, deliberately
  omitted from `DARK_COLOR_REMAP` (:2376-2387) so never remapped.

Split this into two cards (recovery/coercion D-178..D-181, then geometry) — 18
defects in one card is the unbounded-workload failure the Stage 2 card was
designed to avoid.

### Batch 2 — packet engine core (7 defects, 2 high)
`frontend/src/utils/d3Plugins/packetPlugin.ts` only. Independent of Batch 1;
can run in parallel.
- **D-218** one-line: `normalizeSection` (:713-715) reads `sec?.rows` then
  `sec?.fields` but never `sec.cells`, though `normalizeSectionRows` *does* read
  `row.cells`. Verified still absent. A section keyed `cells` silently loses
  every field to a placeholder row and still renders a plausible diagram — the
  most dangerous failure shape in the sweep. Fix: `|| sec.cells`.
- **D-217** row nesting off-by-one passes `row.every(Array.isArray)` and kills
  the render.
- **D-221/D-222/D-225** explicit width/height not honoured as a scale target;
  section label unbudgeted past `LABEL_W=180`; multi-line label overflows
  `computeDimensions` height math (`totalRows*ROW_H`, no label-line term).
- **D-223** `PACKET_MAX_BRACKET_DEPTH=6` caps the gutter but not the labels; 34 of
  40 brackets lose attribution.
- **D-228** `AUTO_PALETTE_LIGHT[2]` vs `[5]` = 1.02 contrast; `AUTO_PALETTE_DARK[0]`
  vs `[7]` = 1.04. Recycled sections indistinguishable.

### Batch 3 — shared colour pipeline (2 defects, 1 high) — highest cross-engine leverage
`frontend/src/utils/colorUtils.ts`. Verified: `hexToRgb` (:18) has **no `hsl()`
branch** at all.
- **D-157** mermaid's internal `hsl()` fills (lightness `NaN`) fail to parse, so
  `enhanceSVGVisibility` silently declines to remediate five default-palette
  surfaces measuring 1.02–1.15:1 on white. `backlog.json` calls the hsl parse
  "the single highest-leverage change in the engine".
- **D-134** `getOptimalTextColor` (:94) **fails open to white** for an
  unparseable paint, overriding compliant authored `#000000` labels. Fix: fail to
  *no-change*, and never override an authored colour that already clears the floor.

Run this before Batch 5 — the failure mode is shared with the graphviz/mermaid
theme passes, and a fix here may close defects other batches would otherwise
chase. Also fold in the **D-015** residue (`D3Renderer.tsx` generic
no-plugin-match → silent 30s timeout with no diagnostic).

### Batch 4 — shared spec parser (1 defect, 1 high)
`frontend/src/utils/d3SpecParser.ts` + the `resolveMusicSpec` entry point.
- **D-014** `parseD3Spec` strips only outer parens and comments; verified no
  `json5`, no fence strip, no smart-quote normalise. Fenced input, U+201C/D smart
  quotes, Python literals and `const x = {...};` wrappers all hang. `json5` is
  already in `package.json`. Also route `resolveMusicSpec` (bare `JSON.parse`,
  bails when `trimStart()[0] !== '{'`) through `parseD3Spec`.

Affects basic-chart, d3 **and** music. Sequence after Batch 1 (both touch
`musicPlugin.ts`) — or merge into Batch 1's first card.

### Batch 5 — chat-message markdown surface (4 defects, 0 high)
`frontend/src/components/MarkdownRenderer.tsx` + `frontend/src/utils/domSanitize.ts`.
- **D-034** `errorColor: '#cc0000'` (verified at :6081, drifted from the
  triage's :6041) = 2.57:1 on the dark `#262626` message surface. The one
  surface whose job is to report broken math is the least readable thing in dark.
- **D-029** `breaks:false` default (:5851/:6380) collapses single-newline hard
  breaks for assistant messages only, so the same document renders differently by
  role. CSS half is in `index.css`.
- **D-030** footnotes, definition lists, `\(..\)`/`\[..\]` delimiters, and
  several raw-HTML tags leak source.
- **D-031** inline `<span style>` style lands on an empty box, text emitted as an
  unstyled sibling.

Note the line-number drift: this file is dirty (`M`). Re-locate every site
before editing; do not trust the recorded line numbers.

### Batch 6 — chat-message CSS (2 remaining defects, 1 high)
`frontend/src/index.css` (D-033 already taken in Batch 0).
- **D-027** content wider than the message box is hard-clipped with no scroll,
  fade or wrap on four surfaces (`pre`, long prose token, inline `code`, 40-col
  table). Fix is `overflow-x:auto`/`pre-wrap`/`overflow-wrap:break-word` + a
  scrollable table wrapper. The one in-scope suspect
  (`app/utils/chat_screenshot.py`) was correctly ruled out as a non-root site.
- **D-028** blockquotes have no styling in either theme — no left rule, tint,
  italic or muted colour — so the 3:1 boundary floor is unmeetable by
  construction.

Plus the **D-032** residue (author-hardcoded colours not theme-normalised on the
inline-HTML path; `mermaidThemeNormalize.ts` exists but is wired only to `/print`).

### Batch 7 — cross-campaign ledger correction (not a code fix, but blocking)
`.ziya/graphics-stress/ledger-v2.md` records circuitikz as *"unsupported, no
rendering backend, do not re-attempt"*. The CircuiTikZ hardening run proved this
**stale and wrong** — circuitikz renders server-side via the LaTeX profile, and
that campaign fixed 5 real defects and rendered 21 specs cleanly. The run
declined the edit because the ledger was outside its writable scope. Left
uncorrected, this entry will cause future sweeps to skip circuitikz entirely.
One-line correction, `.ziya/` is already writable.

### Batch 8 — severity-gate deferrals (22 defects, all low)
Deferred by policy, not scope; the gate worked. 15 of the 22 name an **in-scope**
suspect file (`plugins/d3/*`), so most need no new grant at all. Pick up
opportunistically when an adjacent batch is already in the same file — e.g.
D-195 (ledger weight) alongside Batch 1, D-224/D-226/D-229 alongside Batch 2.
Do not schedule as its own campaign.

---

## 5. Verification contract (per batch, non-negotiable)

The music campaign burned five runs authoring correct fixes it could never see.
Every batch must:

1. Prove the served bundle is fresh **before** judging: `npm run build`, then
   confirm `templates/index.html` references the new hash **and** grep the served
   bundle for a symbol the fix introduces. No judging on an unproven bundle.
2. Re-render each defect's recorded spec (`.ziya/gfx-sweep/specs/`) in **both**
   themes, plus that engine's regression set from `backlog.json.regression_sets`.
3. Add a test that **fails without the fix**. Several declines correctly note a
   test at the shim would "only certify unpatched core behaviour" — with the core
   now writable, that excuse is gone.
4. Write `status`, `resolution_note` and `test_added` back to `backlog.json`,
   and clear `blocked_by_scope`.
5. Embed `"type":"music"` inside the definition JSON for every music render —
   `isMusicSpec` requires it, and omitting it yields a misleading
   "No plugin found" 30s timeout. This cost three separate runs their first
   renders.

## 6. Expected outcome

36 defects (13 high) closed across 7 engines, of which **5 are one-line changes**
(D-025, D-033, D-180, D-218, and D-179's flatten pass) and 2 of those alone
account for six wave-4 engine failures. The dominant cost is Batch 1's music
geometry work; everything else is small and independently shippable.
