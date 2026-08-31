# Ziya Capabilities

## Models

Ziya supports models from multiple providers. The default model is `sonnet4.6` on AWS Bedrock. Use the model picker in the toolbar to switch at any time.

### Amazon Bedrock — Claude Models

| Alias | Model | Context | Notes |
|---|---|---|---|
| `sonnet4.6` | Claude Sonnet 4.6 | 200K (1M extended) | **Default**. Adaptive thinking. |
| `sonnet4.5` | Claude Sonnet 4.5 | 200K (1M extended) | Extended context. |
| `sonnet4.0` | Claude Sonnet 4.0 | 200K (1M extended) | Extended context. |
| `sonnet3.7` | Claude Sonnet 3.7 | 200K | EU regions only. |
| `sonnet3.5-v2` | Claude 3.5 Sonnet v2 | 200K | |
| `sonnet3.5` | Claude 3.5 Sonnet | 200K | |
| `opus4.6` | Claude Opus 4.6 | 200K (1M extended) | Advanced. Adaptive thinking. |
| `opus4.5` | Claude Opus 4.5 | 200K | Advanced. |
| `opus4` | Claude Opus 4 | 200K | Advanced. |
| `opus3` | Claude Opus 3 | 200K | US regions only. |
| `haiku-4.5` | Claude Haiku 4.5 | 200K | Fast and cheap. |
| `haiku` | Claude 3 Haiku | 200K | Fast and cheap. |

### Amazon Bedrock — Nova Models

| Alias | Model | Context | Notes |
|---|---|---|---|
| `nova-premier` | Amazon Nova Premier | 1M | Multimodal. Web grounding capable. |
| `nova-pro` | Amazon Nova Pro | 300K | Multimodal. Thinking mode. |
| `nova-lite` | Amazon Nova Lite | 300K | Fast. Multimodal. |
| `nova-micro` | Amazon Nova Micro | 128K | Text only. |

### Amazon Bedrock — Other Models

| Alias | Model | Context | Notes |
|---|---|---|---|
| `deepseek-r1` | DeepSeek R1 | 128K | Reasoning model. |
| `deepseek-v3` | DeepSeek V3 | 128K | |
| `deepseek-v3.2` | DeepSeek V3.2 | 128K | |
| `qwen3-coder-480b` | Qwen3 Coder 480B | 128K | us-west-2 only. |
| `kimi-k2.5` | Kimi K2.5 | 128K | Thinking model. |
| `minimax-m2.1` | MiniMax M2.1 | 1M | |
| `glm-4.7` | GLM 4.7 | 128K | |
| `openai-gpt-120b` | OpenAI GPT OSS 120B | 128K | us-west-2 only. |
| `openai-gpt-20b` | OpenAI GPT OSS 20B | 128K | us-west-2 only. |

### Google Gemini

| Alias | Model | Context | Notes |
|---|---|---|---|
| `gemini-3.1-pro` | Gemini 3.1 Pro Preview | 1M | Thinking levels. Native function calling. Default. |
| `gemini-3.1-pro-customtools` | Gemini 3.1 Pro Preview (Custom Tools) | 1M | Optimized for agentic workflows with bash/custom tools. |
| `gemini-latest` | Gemini Pro Latest | 1M | Floating alias — auto-updates to latest Pro model. |
| `gemini-3-pro` | Gemini 3 Pro Preview | 1M | ⚠️ Deprecated March 9, 2026. Use `gemini-3.1-pro`. |
| `gemini-3-flash` | Gemini 3 Flash Preview | 1M | Thinking levels. Native function calling. |
| `gemini-2.5-pro` | Gemini 2.5 Pro | 1M | Native function calling. |
| `gemini-flash` | Gemini 2.5 Flash | 1M | Native function calling. |
| `gemini-2.0-flash` | Gemini 2.0 Flash | 1M | |
| `gemini-2.0-flash-lite` | Gemini 2.0 Flash Lite | 1M | No function calling. |
| `gemini-2.5-flash-lite` | Gemini 2.5 Flash Lite | 1M | Thinking mode. |

### OpenAI

| Alias | Model | Context | Notes |
|---|---|---|---|
| `gpt-4.1` | GPT-4.1 | 200K | Native function calling. Vision. |
| `gpt-4.1-mini` | GPT-4.1 Mini | 200K | Native function calling. Vision. |
| `gpt-4.1-nano` | GPT-4.1 Nano | 200K | Native function calling. Vision. |
| `gpt-4o` | GPT-4o | 128K | Native function calling. Vision. |
| `gpt-4o-mini` | GPT-4o Mini | 128K | Native function calling. Vision. |
| `o3` | o3 | 200K | Reasoning model. |
| `o3-mini` | o3 Mini | 200K | Reasoning model. |
| `o4-mini` | o4 Mini | 200K | Reasoning model. |

> **Note**: OpenAI models require `OPENAI_API_KEY` set in your environment and `--endpoint openai`. Enterprise deployments may restrict available endpoints via policy.

---

## Portable Model Tiers

Instead of naming a specific model, you can select a **tier** — a portable,
endpoint-agnostic cost/capability rung. Tiers let a decomposed workload run
cheap executor work on small models under a smarter supervisor, without
hardcoding a provider- or version-specific name that rots when models are
retired or you switch endpoint.

| Tier | Intent | Bedrock | Google | OpenAI | Anthropic | z.ai |
|---|---|---|---|---|---|---|
| `xsmall` | Cheapest/fastest | Nova Micro | Flash Lite | GPT-5.5 Nano | Haiku 4.5 | GLM-4.6 |
| `small` | Cheap | Nova Lite | Gemini Flash | GPT-5.5 Mini | Haiku 4.5 | GLM-4.6 |
| `medium` | **Default** (average) | Sonnet 5 | Gemini 3.1 Pro | GPT-5.5 | Sonnet 5 | GLM-5.2 |
| `large` | Most capable | Opus 4.8 | Gemini 3.1 Pro | GPT-5.5 Pro | Opus 4.8 | GLM-5.2 |
| `frontier` | Cutting edge — rare, expensive | Fable 5 | Gemini 3.1 Pro | GPT-5.5 Pro | Fable 5 | GLM-5.2 |

Five rungs, cheapest → most capable. **`medium` is the center: the default,
"average" model — the same one the top-level conversation uses (Sonnet 5 on
Bedrock).** It is also the resolution fallback target. **`frontier`** is the
rarely-warranted top: cutting-edge models that today run roughly 20× the cost
of `large` with heavy throttling, so reserve it for work that genuinely needs
it.

The resolution is **not** a separate table — each model entry carries a
`tier` tag, so the tier follows the model automatically as models are added
or retired. A rung with no exactly-tagged model **rounds up** to the nearest
defined rung at or above it (falling to the highest rung below only if nothing
at/above exists), so an unmapped rung never silently under-serves a task.
`--model <tier>` works at the top level too.

**Where tiers apply:**

- **Top-level conversation** — `--model medium` (or any tier) selects the
  resolved model for the session.
- **Task Card blocks** — the Model control (Task Advanced section, and on
  every container block / the card / the deck via the Permissions row) picks
  a tier (recommended) or a specific model. A tier set on a container, the
  card, or the project-wide deck flows down to every task beneath it; a leaf
  overrides for itself. Set a smart tier on the card and cheap tiers on
  mechanical leaf tasks to run cheap executors under a smart supervisor.
- **Delegates** — each delegate spec can carry a `model_tier` so a swarm runs
  cheap executors under a smarter orchestrator. A model that authors its own
  plan (a `delegate-tasks` or `task-card` block, or `swarm_request_delegate`)
  can set `model_tier` per unit of work directly.

A **specific model name** (e.g. `sonnet4.6`) or inference-profile ARN is
still available as an escape hatch, but is explicitly flagged non-portable in
the UI — prefer a tier unless you have a concrete reason to pin an exact model.

---

## Tools

The model has access to tools it can call autonomously when they would help answer your question.

### Builtin Tools (no setup required)

| Tool | What it does |
|---|---|
| `file_read` | Read files from your project |
| `file_write` | Write files to approved locations |
| `file_list` | List directory contents |
| `nova_web_search` | Search the web with citations (requires `bedrock:InvokeTool` IAM permission) |
| Architecture shapes | Browse and search diagram component catalogs for DrawIO, Mermaid, and Graphviz |

### MCP Tools

Connect any MCP-compatible server to give the model additional capabilities — shell access, internal APIs, databases, and more.

MCP servers are configured in `mcp_config.json`. Ziya looks for this file in three locations (all are merged, later entries win):

1. Current working directory (`./mcp_config.json`)
2. Ziya project root
3. User home (`~/.ziya/mcp_config.json`)

```json
{
  "my_server": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    "enabled": true
  }
}
```

#### Tool Enhancements (per-server)

MCP tool descriptions are set by the server and can't always be changed upstream. If a tool has ambiguous parameters or the model keeps calling it incorrectly, add a `tool_enhancements` block to inject supplemental hints into the tool's description:

```json
{
  "my_server": {
    "command": "npx",
    "args": ["-y", "some-mcp-server"],
    "tool_enhancements": {
      "search_tool": {
        "description_suffix": "\n<Rule>The 'query' parameter must be a string, not an array.</Rule>"
      },
      "file_tool": {
        "description_suffix": "\nAlways use absolute paths."
      }
    }
  }
}
```

The `description_suffix` is appended verbatim to the tool's description before it reaches the model. This is useful for correcting common model mistakes without waiting for the MCP server to update.

Enhancement sources are merged in priority order (later overrides earlier for the same tool):

1. **Enterprise plugin** — organization-wide defaults via `ToolEnhancementProvider` (see `Enterprise.md`)
2. **MCP server config** — the `tool_enhancements` block shown above
3. **User overrides** — `~/.ziya/tool_enhancements.json` (see below)

The shell command allowlist can be extended by enterprise plugins via the `ShellConfigProvider` interface — see `Enterprise.md` for details. Users can also add commands per-session with `/shell add <cmd>` or persist them with `/shell add <cmd> save`.

---

## Skills

Skills are reusable instruction bundles that shape how the model behaves in a conversation. Activate one (or several) from the Skills panel to give the model standing guidance — a particular review style, a communication style, a focus area.

Ziya ships a few example skills to illustrate the concept. You'll likely want to create your own: any repeatable instruction you find yourself typing can be a skill. Skills can include a system prompt, and in future releases will be able to declare specific tools, context presets, and model overrides as well.

Custom skills can be created and edited from the Skills panel.

---

## Code Application

When the model suggests a diff:

- **Apply** writes the change to disk immediately
- **Undo** reverses it
- The diff pipeline tries multiple strategies (`patch`, `git apply`, difflib) so it handles imperfect diffs gracefully
- Per-hunk status is shown — partial success is fine
- **File deletion** diffs (`deleted file mode` / `+++ /dev/null`) delete the target file when applied
- **New file creation** diffs (`new file mode` / `--- /dev/null`) create the file when applied

Files outside the project root can be modified if they were added via the file browser.

---

## Context & Projects

- Multiple projects can be open in separate browser tabs simultaneously
- Each project has its own conversation history, contexts, and skills
- The file tree shows token counts to help you manage context size
- Files outside the project root can be added via the browser
- Context window usage is shown in the toolbar

### Context Curation

Ziya gives you direct control over what the model sees, rather than relying on automatic summarization that may discard information you consider important:

- **Mute/unmute messages** — exclude any message from context without deleting it. Muted messages stay visible (dimmed) and can be restored anytime. Use this to shed weight from dead-end explorations while keeping the important discoveries.
- **Fork + truncate** — branch from any message to explore an alternative. Optionally truncate the fork to start with a lighter context, while the original conversation remains intact.
- **Edit or resubmit** — revise any message in the history and resubmit from that point.
- **Selective file removal** — drop individual files from context when they've served their purpose, reclaiming token budget.

This is a deliberate alternative to automatic context compaction (used by Claude Code, Cline, and others). Auto-compaction lets the machine decide what to keep — which risks losing details you know are critical. Manual curation takes a few clicks but keeps you in control.

---

## Large PDF Reference Documents

PDFs that would blow past the context window (reference manuals, specs,
textbooks) are handled through a built-in per-document RAG path:

- Below the threshold (25k tokens / 60 pages by default, tunable via
  `ZIYA_PDF_RAG_TOKEN_THRESHOLD`), PDFs are extracted in full as before.
- Above the threshold, the PDF is replaced in context with a **stub**
  containing the native bookmark tree / table of contents (or a
  heuristic figure & table list if the PDF has no bookmarks), along
  with the first and last pages verbatim.
- The model can then pull specific sections on demand using three
  built-in MCP tools:
  - `pdf_outline(path)` — re-fetch the full outline, figures, tables
  - `pdf_read_pages(path, start_page, end_page, include_images?)` —
    read a page range verbatim, optionally with rendered page images
    (helpful for scanned or diagram-heavy pages)
  - `pdf_search(path, query, top_k?, mode?)` — BM25 keyword search
    across pages and figure/table captions; set `mode="embedding"` to
    use semantic search when `sentence-transformers` is installed

Indexes are cached under `.ziya/pdf_index/`, keyed by file path + mtime
+ size, and persist across restarts.

---

## Vision / Multimodal

Drag images into the chat input, paste from clipboard, or use the image button. Supported on: Claude Sonnet/Opus 4.x, Claude 3.x, Nova Pro/Lite/Premier, Gemini.

---

## Diagram Rendering

Ziya renders inline diagrams from fenced code blocks. Supported formats:

| Format | Block syntax | Notes |
|---|---|---|
| Mermaid | `` ```mermaid `` | Flowcharts, sequence, class, state, ER, Gantt, etc. Auto-preprocessed for syntax compatibility. |
| Graphviz | `` ```graphviz `` | DOT language. Full layout engine. |
| DrawIO | `` ```drawio `` | XML-based diagrams with export and online editor support. |
| Vega-Lite | `` ```vega-lite `` | JSON data visualization specs. |
| HTML Mockup | `` ```html-mockup `` | Interactive UI prototypes in sandboxed iframes. Add the `figure` modifier (`` ```html-mockup figure ``) to drop the frame and controls for a graphic that is part of the discussion rather than a design under review. The iframe supplies a theme-matched foreground plus `--mockup-border` / `--mockup-muted`, so incidental text can be left unstyled and stay legible in both themes — deliberate colour (brand, status, palette, or a fixed light/dark design) is expected, and should carry its own background so the pairing does not depend on the surface behind it. |
| Packet | `` ```packet `` | Bit-level protocol frame layouts. |
| Music | `` ```music `` | Published-quality sheet music (VexFlow). Notes/chords/rests, beaming, tuplets, grace & cue notes, slurs/ties, dynamics/hairpins, articulations, ornaments, lyrics, chord symbols, pedal & harp-pedal lines, measures/repeats/voltas, mid-score key & meter changes (modulation), tempo & navigation marks, title block, multi-voice & grand staff with cross-staff beams/slurs, and multi-system wrapping. A short phrase can also be written inline as `` `music: C4/q, D4/q` ``. Malformed input (bad octave/accidental/duration, out-of-range tuplet count) degrades gracefully instead of hanging the render. |
| Railroad | `` ```railroad `` | Railroad (syntax) diagrams from a JSON spec: terminals, nonterminals, choice, optional, loops with separators, dashed groups — a single production or a stack of named rules. For grammars, regex structure, and config/URL/file formats. Tolerates trailing commas, comments, and stray fences in the JSON. |
| WaveDrom | `` ```wavedrom `` | Digital timing diagrams from WaveJSON: clocks, signals, buses with labeled data, groups, gaps (`|`), and annotated node/edge arrows for setup/hold and handshake timing. Accepts the canonical JSON5 style (unquoted keys, single quotes). Dark mode uses WaveDrom's own dark skin. Also renders `reg` bit-field and `assign` logic specs. |
| Flame graph | `` ```flamegraph `` | Interactive flame graphs for performance profiles (click a frame to zoom, click the root to reset). Accepts nested JSON (`{name, value, children}`) or collapsed-stack text — the `frame;frame;frame count` lines py-spy, perf, and flamegraph.pl emit — pasted directly into the fence. Frame names may contain spaces; `#` comment lines are skipped. |
| TikZ | `` ```tikz `` | General LaTeX vector drawing. Rendered server-side. |
| CircuiTikZ | `` ```circuitikz `` | Electronic circuit schematics. |
| chemfig | `` ```chemfig `` | Chemical structures, reaction schemes, stereochemistry. |
| tikz-cd | `` ```tikz-cd `` | Commutative diagrams. |
| pgfplots | `` ```pgfplots `` | Typeset function/data plots with math-notation axes and legends, continuous with KaTeX derivations. Includes the `smithchart` and `polar` libraries. |
| forest | `` ```forest `` (also `` ```syntax-tree ``) | Labelled trees in field-standard notation: constituency/syntax trees, taxonomies, decision and game trees, phylogenies, parse trees. Bracket syntax, with roofs over elided constituents, aligned tiers, and TikZ movement arrows. Prefer graphviz/mermaid for a generic hierarchy — forest is for trees whose *notation* matters. The `linguistics` and `edges` libraries are preloaded, so `roof` and `forked edges` work without declaring them. |
| bussproofs | `` ```bussproofs `` (also `` ```prooftree ``, `` ```proof-tree ``) | Proof trees read as premises-over-conclusion: natural deduction, sequent calculus, typing rules. A stack discipline — `\AxiomC` pushes a pending subproof, `\UnaryInfC`/`\BinaryInfC`/`\TrinaryInfC` consume 1/2/3 — with `\RightLabel` rule annotations and `\fCenter` turnstile alignment. |

Rendered diagrams include **Open** (popup with zoom/pan), **Save** (SVG download), and **Source** (view/edit definition) buttons.

### LaTeX diagrams (server-side)

The seven LaTeX-family types above are compiled by a local TeX installation rather
than in the browser, so they need one to be present. When TeX is missing, the
diagram is not lost: the block renders as a notice with the exact `tlmgr install`
command for the packages that type needs, and the LaTeX source stays visible.

Output is **SVG** when `dvisvgm` is installed — text stays selectable and is
recoloured for dark mode — and **PNG** otherwise.

**Dark mode.** TeX draws black on transparent, which measures 1.27:1 against the
dark diagram background — invisible. SVG output is recoloured on the client:
TeX's default black becomes a light ink, and any colour you authored is lightened
only as far as it takes to clear a 3:1 contrast floor, with its hue preserved so
`\draw[red]` still reads as red. Stroke widths are never altered, because TeX's
hairline weights are part of the engraving. PNG output cannot be recoloured
selectively, so it is inverted instead.

**Sizing.** Diagrams render at their natural size rather than being stretched to
the width of the chat column. dvisvgm reports an absolute size in points, which
becomes the diagram's width, capped at the container so it still shrinks on a
narrow viewport. This matters most for small structures: a lone benzene ring is
intrinsically 70px wide, so filling an 820px column would scale it nearly 12×.

To install a minimal working toolchain:

```bash
# macOS (BasicTeX), then the packages Ziya's profiles use
sudo tlmgr install standalone dvisvgm pgf circuitikz siunitx chemfig tikz-cd pgfplots \
                   forest bussproofs
```

**No macro definitions.** `\def`, `\newcommand`, `\renewcommand`, `\let`, `\edef`
and `\gdef` are refused in a diagram body across every LaTeX type, because they
can construct unbounded expansions. Anything a notation genuinely needs is
supplied by its profile's preamble instead — `bussproofs`, for example, presets
`\fCenter` to a turnstile, since its own default (`\relax`) renders a sequent
proof with no turnstile at all rather than failing.

**Chemistry.** The `chemfig` type draws structures on its own. Two extras are
worth installing alongside it:

```bash
sudo tlmgr install mhchem      # \ce{} chemical equations, \pu{} units
```

`mhchem` is optional and loaded only if present, so its absence disables `\ce{}`
without affecting structure rendering. Lewis dot structures (`\lewis`, `\Lewis`)
need no extra package — they come from a module chemfig already ships, which Ziya
loads for you.

**Charges and lone pairs.** `\charge` separates the angle from the symbol with
`=`, not `:` — `\charge{90=\|,180=\|}{O}` — because `:` is already taken for the
optional radial offset. Both mistakes are **repaired automatically**, so either
form renders, but the rule is worth knowing since the raw errors name the wrong
cause: chemfig uses `:` for *bond* angles (`-[:30]`), so the wrong separator is
the natural first guess, and it fails with `Argument of \charge_g has an extra
}` — pointing at brace balance and never mentioning the separator. The charge
argument is also not math mode, so `\ominus` and friends fail with `Missing $
inserted`, which likewise doesn't say which argument was at fault.

The repair promotes a stand-in `:` to `=` and wraps a math symbol in `$...$`,
reporting each correction alongside the image. It is deliberately conservative
in two ways. The math wrap fires **only** for payloads containing a TeX control
word, because wrapping is not always neutral — `\charge{90=-}` renders
*differently* in math mode (a text hyphen is not a math minus), while `\|`, `+`
and `2+` are byte-identical either way, so blanket wrapping would silently
alter diagrams that already worked. And a genuine offset survives: in
`45:2pt:\|` only the *last* colon is promoted. An undefined command such as
`\+` is left to fail, since that is a different error and guessing at intent
would trade a clear message for a wrong structure.

For plain lone pairs `\lewis{1:5:7:,O}` is usually less fiddly than stacking
`\|` marks by angle.

`\ce{}` also works in ordinary prose math (`$...$` / `$$...$$`) with no TeX
installation at all, since the browser's KaTeX renderer loads the mhchem
extension.

**Electron-pushing arrows.** Diagrams using `\chemmove` (or TikZ
`remember picture` overlays) are always rendered as PNG. These resolve
coordinates recorded during a previous compilation pass, which the DVI→SVG
driver places incorrectly — an SVG would render successfully but silently omit
the arrow. PNG is chosen instead, at the cost of selectable text and dark-mode
recolouring for those particular diagrams.

**Ring-closure lint.** chemfig ring specifications are checked before
compilation, because an under-specified ring is not a syntax error: chemfig
draws the bonds it was given, leaves the ring open, and the pipeline reports
success — so the output is a picture of a *different molecule* with nothing in
the TeX log to indicate it. The rule is that a standalone `*n(...)` ring needs
`n` bonds while a **fused** one needs `n-1` (it inherits its closing edge from
the ring it is nested in), and a ring nested inside a *branch* is pendant
rather than fused, so it still needs all `n`.

The count ignores branches, bond options and brace groups, since each can
legitimately contain a bond character that is not a ring bond — `(-OH)` is a
substituent, `-[:-30]` carries a negative angle, and `SO_{4}^{2-}` ends in a
minus sign. This is the trap in practice: `*5(-(=O)-(=O)-)` looks like five
bonds but has three, so a carbonyl-rich ring reads as complete when it is short.

Rings missing exactly one bond in an unambiguous case — an even ring whose
bonds strictly alternate, i.e. a Kekulé aromatic ring — are closed
automatically, which is positionally safe (substituents keep their relative
placement, so a para pair stays para). Everything else is reported and left
alone: the bond *order* of an added bond is genuinely ambiguous for odd rings,
and guessing would trade a visibly broken ring for a plausible-looking wrong
structure. Both corrections and warnings are returned alongside the image, so
a model iterating on a diagram sees the defect rather than only the success.

**Skip-edge rerouting**: For Mermaid diagrams with feedback/control-loop edges that span multiple nodes, a post-render rerouter automatically arcs those paths above or below intermediate nodes instead of drawing them straight through. Arcs are nested by skip distance — shorter-range arcs sit closer to the node row, longer-range arcs arc further out — so overlapping edges remain visually distinct even when several skip edges share the same side.

### Visual Diagram Feedback (Builtin Tool)

The `render_diagram` builtin tool renders a diagram specification server-side and returns the resulting image directly as a vision content block, enabling the model to *see* what was rendered and evaluate correctness. This supports an iterative refinement loop:

1. Model defines a diagram spec (mermaid, graphviz, vega-lite, drawio, packet, etc.)
2. `render_diagram` tool renders it via the headless Playwright pipeline
3. Model receives the PNG image and analyzes it with vision capabilities
4. Model identifies rendering issues (missing labels, broken layout, wrong colors)
5. Model fixes the spec or pipeline code, re-renders, and re-checks

This is particularly useful for validating the rendering pipeline itself — systematically testing each diagram type and fixing any rendering issues discovered.

The tool uses the same headless Chromium renderer as the export API, running through the full frontend D3Renderer pipeline with all plugins and post-render enhancers.

### Headless Diagram Export (API)

Diagrams can be rendered to PNG or SVG images server-side via the REST API, enabling integration with external services like Slack, CI pipelines, or documentation generators.

The headless renderer uses Playwright to drive a real Chromium instance through the same frontend rendering pipeline as the chat UI — including all post-render enhancers (edge rerouting, theme application, layout fixes). This guarantees pixel-perfect output.

**Setup** (optional dependency):
```bash
pip install playwright && playwright install chromium
```

**API**:
```bash
curl -X POST http://localhost:6969/api/render-diagram \
  -H "Content-Type: application/json" \
  -d '{
    "type": "mermaid",
    "definition": "graph LR\n  A-->B-->C",
    "theme": "dark",
    "format": "png"
  }' \
  --output diagram.png
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `type` | string | required | `mermaid`, `graphviz`, `vega-lite`, `drawio`, `packet`, etc. |
| `definition` | string | required | Diagram source text or JSON spec |
| `theme` | `dark`\|`light` | `light` | Color theme |
| `format` | `png`\|`svg` | `png` | Output format (SVG falls back to PNG for canvas renderers) |
| `width` | int | auto | Explicit width in pixels |
| `height` | int | auto | Explicit height in pixels |

### Rendered Conversation Export (API)

Conversations can be exported with server-side rendered diagram images via the REST API. This enables plugin export targets (Slack, Quip, wiki), CLI-driven exports, and API consumers to get fully rendered output without a browser.

The pipeline extracts all diagram code blocks (mermaid, graphviz, vega-lite, drawio, packet, etc.) from the conversation, renders each through the headless Playwright pipeline, and embeds the resulting images inline in the exported markdown or HTML.

**API — Server-rendered export**:
```bash
curl -X POST http://localhost:6969/api/export/rendered \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "human", "content": "Draw an architecture diagram"},
      {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B-->C\n```"}
    ],
    "format": "markdown",
    "theme": "dark"
  }'
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `messages` | array | required | Conversation messages with role and content |
| `format` | `markdown`\|`html` | `markdown` | Output format |
| `theme` | `dark`\|`light` | `light` | Color theme for diagram rendering |
| `target` | string | `public` | Export target ID (for metadata) |
| `image_format` | `svg`\|`png` | `svg` | Image format for embedded diagrams |

**API — Export to plugin target**:
```bash
curl -X POST http://localhost:6969/api/export/to-target \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [...],
    "target_id": "slack",
    "theme": "dark"
  }'
```

Plugin export targets are registered via the `ExportProvider` interface. See `app/plugins/interfaces.py` for the contract.

### Conversation PDF Export

Exporting a conversation to PDF (the **PDF** option in the export modal) is
**server-side rendered**. The client `POST`s the raw conversation to
`POST /api/export/pdf`; the server renders the whole conversation through the
real frontend `MarkdownRenderer` pipeline at a hidden `/print` route and
captures it with headless Chromium (`page.pdf()`, A4, `printBackground`). It is
not the browser's "Print to PDF" of the on-screen chat — it is a dedicated,
light-themed render built for print.

Because it drives the same rendering pipeline as the chat UI, the PDF preserves
what the old client-side print path dropped:

- **Syntax highlighting** (Prism) and **code-block backgrounds**.
- **Diff add/remove colors** and **text highlight** colors, via
  `print-color-adjust: exact` in the shared print stylesheet.
- **Rendered diagrams** (mermaid, D3, graphviz, etc.) as images, including
  `<canvas>` renderers rasterized to `<img>` so their pixels survive.
- **KaTeX math**, tables, and `<details>` blocks.
- **Dark-mode content forced to light** so diagrams and code composite onto the
  white page instead of leaving dark bands (baked dark mermaid themes are
  normalized to light before rendering).
- **Sane pagination** — oversized figures are scaled to fit one page, headings
  are kept with their content, and long code/diffs split across pages.

The export is also built as a **finished document**, not merely a correct one:

- **Flow-aware figure sizing.** A figure that technically fits a page but cannot
  fit alongside the prose that introduces it — so it would otherwise be bumped
  whole onto its own page, stranded from its context behind an empty band — is
  shrunk the *minimum* needed to keep it with its introducing text. The shrink
  is bounded: **0.75 is the floor** for flow-driven shrinking (never smaller for
  flow reasons), while a genuinely oversized figure may still scale below 0.75
  purely to fit one page. The two cases are tagged (`data-print-fit-reason` =
  `flow` vs `oversize`).
- **Live-session chrome and superseded diffs are excluded.** Content that
  belongs to the interactive session but not to a document is dropped in print
  mode: a **superseded diff** (an earlier diff the assistant corrected later in
  the same message — the app merely fades it to 0.45 opacity, which would land
  in the PDF as low-contrast noise and inflate the page count) is omitted
  entirely, keeping only the final version; and live-session **UI-chrome notes**
  (the "Auto-added N file(s) to context … Remove via the A button in the Files
  panel." banner, context-enhancement warnings, "Checking context…" spinners)
  are stripped. Both are suppressed at the shared `/print` route, so the HTML
  export inherits the same hygiene.
- **A diff header stays with its diff body.** A "Modify: `<path>`" header is
  bound to the diff it introduces (a `break-after: avoid` relationship) and diff
  tables are allowed to *flow* across a page boundary rather than being forced
  whole onto a fresh page, so a header is never stranded at a page bottom with a
  large empty band above its body.
- **Wide tables are fit-scaled.** A markdown table far wider than the printable
  content width is uniformly scaled down (mirroring the oversized-figure fit) so
  its right-hand columns are no longer clipped off the margin; narrow and
  ordinary-width tables are left untouched.
- **A navigable outline.** The PDF carries a **bookmark tree with one entry per
  message** ("You (message N)" / "Ziya (message N)"), synthesized during
  capture, so a long conversation can be navigated in any PDF viewer.
- **Live hyperlinks.** URLs in the conversation body and the footer are real
  clickable `/Link` annotations, not just blue text.
- **Sensible document metadata.** `/Title` (the conversation title), `/Author`
  ("Ziya"), `/Creator` ("Ziya PDF Exporter"), `/Subject`, and creation date are
  set rather than left at the headless-Chromium defaults.
- **Embedded fonts.** Every font is embedded (Type0 CID subsets; diagram text
  uses self-embedded Type3 fonts), so the document renders identically on a
  machine that does not have those fonts installed.
- **Vector diagrams.** mermaid/D3 diagrams reach the PDF as vector path
  operations (not rasterized bitmaps), so they stay crisp at any zoom.

Option filtering (`roundLimit` / `includeHuman` / `includeCollapsed`) happens in
the `/print` page, so the PDF and HTML exports share one implementation — which
is why the client sends raw messages instead of pre-filtering them.

**Fallback.** If the server has no Playwright/Chromium installed,
`/api/export/pdf` returns HTTP `501` and the modal falls back to the
**client-side** renderer (`frontend/src/utils/pdfExport.ts`), which rasterizes
the live DOM and opens the browser print dialog. This is lower fidelity (subject
to the user's print settings) but ensures a PDF is always available.

**Troubleshooting:**

| Symptom | Cause / Fix |
|---|---|
| "Server PDF renderer unavailable; used the browser print dialog" | Playwright/Chromium not installed server-side (`501`). Install with `pip install playwright && playwright install chromium`, then restart Ziya, to get the high-fidelity path. |
| PDF export errors with a non-501 status | A render error or a missing conversation. `404` = conversation id not found; `400` = no message source; `500` = a render failure (check the server log for the `/print` console/pageerror diagnostics the renderer captures). |
| Export hangs / times out | The `/print` render never reached `data-render-status="complete"` (a very large conversation, or a diagram that never settled). The session has a bounded safety timeout; retry, and check the server log for stuck renders. |
| Colors or diagrams missing after a frontend change | The `/print` route lives in the built bundle. After editing the print path or `frontend/src/styles/print.css`, rebuild: `cd frontend && npx craco build`. |
| A very wide table has its right columns cut off | Fixed: tables far wider than the printable width are now uniformly fit-scaled so their right columns are no longer clipped (narrow tables are untouched). Long unbroken code lines also wrap instead of clipping. If a table still looks cramped, it was scaled to fit the page width. |

The fidelity of this path is verified by the shared export-fidelity harness
under `tests/export_fidelity/` (see `Docs/CONTRIBUTING.md` and the harness
`README`-style module docstrings): a fast wiring tier runs on every test
invocation, and an `integration`-marked tier drives a real headless render.

### Conversation HTML Export

Exporting a conversation to HTML (the **HTML** option in the export modal,
reachable via **Download** — saves a `.html` file — and **Paste** — GitHub Gist
and plugin targets) is **dual-mode**. Clipboard copy always forces Markdown and
is out of scope here.

- **High-fidelity mode (route-driven).** When Playwright/Chromium is available,
  HTML is generated by driving the same shared `/print` route the PDF path uses
  and extracting self-contained HTML from the rendered DOM. This gives
  real-renderer fidelity (Prism syntax highlighting, `react-diff-view` per-line
  diff coloring, KaTeX math, tables, diagrams as images) because it is the exact
  chat rendering pipeline.
- **Fallback mode (Python).** When no browser is installed, HTML is produced by
  the pure-Python exporter (`app/utils/conversation_exporter.py`,
  `_export_as_html` → `_markdown_to_html_basic`). HTML export **never
  hard-fails** merely because a browser is absent. The fallback has a lower
  fidelity ceiling than the real renderer but is itself faithful: it now
  delivers **syntax highlighting** (Pygments, inline-styled token spans),
  **per-line diff add/remove backgrounds**, **KaTeX math** (rendered to
  self-contained MathML via a Node subprocess), **GFM tables** as real
  `<table>` grids, and a **light-pinned** document that stays light even when
  opened on a dark-mode machine.

**What each mode preserves**

| Aspect | Route-driven (browser) | Python fallback |
|---|---|---|
| Syntax highlighting | Prism (chat theme) | Pygments inline spans |
| Diff add/remove color | `react-diff-view` per line | per-line background spans |
| Math | KaTeX HTML+fonts | KaTeX MathML (no external fonts) |
| Tables | full renderer | real `<table>` grids |
| Diagrams | embedded images | embedded images (via `/api/export/rendered`) |
| Dark-mode independence | light-pinned wrapper | light-pinned wrapper |
| Self-containment | inlined CSS + data-URI assets | inlined CSS + data-URI assets |

Both modes produce a **self-contained** document: all CSS is inlined and any
diagram images are embedded as data URIs / inline SVG — opening the `.html` with
the network disconnected loses nothing. Both modes **neutralize XSS**: prose is
HTML-escaped before any tag is generated and `javascript:` / `vbscript:` /
`data:` link schemes are rejected, so untrusted conversation content renders as
inert text in either mode (a hard gate, verified by the fidelity harness).

**How to tell which mode produced the output.** The route-driven mode requires a
live Ziya server whose built bundle includes `/print`. When Playwright/Chromium
is unavailable the exporter transparently uses the Python fallback; the returned
export metadata records the format, and the fallback's markup carries the
Pygments/MathML/`<table>` structures described above rather than the chat
renderer's Prism/`react-diff-view`/`.katex`-HTML classes.

**Troubleshooting:**

| Symptom | Cause / Fix |
|---|---|
| Downloaded `.html` opens dark on a dark-mode machine | Should not happen — the document is pinned to `color-scheme: light` and carries no `prefers-color-scheme` dark block. If you see this, you are on a stale build; regenerate the export. |
| Code blocks show no colors in the fallback | Pygments not importable server-side. Reinstall (`pip install pygments`); the exporter degrades to an uncolored block rather than failing. |
| Math shows as literal `$$...$$` in the fallback | `node` not on `PATH` or `frontend/node_modules/katex` missing. Install the frontend deps and ensure `node` is available; math degrades to escaped LaTeX otherwise (still valid HTML). |
| A markdown table shows as literal `\| --- \|` pipe text | Should not happen — GFM tables render as real `<table>` grids in both modes. If you see this you are on a stale build. |
| Diagrams missing from the exported HTML | Diagram images are embedded only when exporting through `POST /api/export/rendered` (server-side diagram render); a plain paste without captured diagrams keeps diagram source as a code block. |

The HTML fidelity of both modes is verified by the same
`tests/export_fidelity/` harness (18 checks per fixture variant across light and
forced-dark), including `self_containment`, `dark_mode_independence`,
`diff_coloring`, `syntax_highlighting`, `math_rendering`, `table_rendering`,
`structural_validity`, and `xss_neutralized`.

---

### Conversation Markdown Export

Exporting a conversation to Markdown is the **most-used export path**: the
**Copy to clipboard** action in the export modal **always** produces Markdown
regardless of the format selector, **Download** saves a `.md` file, and
**Paste** targets GitHub Gist plus plugin targets. All three run the same pure
Python exporter (`app/utils/conversation_exporter.py`, `_export_as_markdown`);
it never requires a browser and never goes through the `/print` route.

**What the Markdown export preserves (losslessly):**

- Full conversation content — every user and assistant turn, in order, under
  `## 👤 User` / `## 🤖 AI Assistant` headings separated by horizontal rules.
- Code blocks in **language-tagged fences** (so the consumer applies its own
  syntax highlighting), and diffs in **` ```diff ` fences** (so Gist colors
  them with its native diff highlighter).
- **Math** verbatim (`$…$` inline and `$$…$$` block), **GFM tables**, and
  collapsible `<details>` sections.
- **Diagrams**: rendered to embedded images when the frontend captured them,
  otherwise the diagram **source fence is preserved** (Gist renders mermaid
  natively; other viewers show the code) — never dropped.
- Fences are **balanced per message** and **tool-output wrappers are widened**
  past any interior backtick run, so no fence can "run away" and swallow the
  rest of the document or leak tool text as prose.

**What it deliberately excludes (export hygiene):**

- **Superseded diffs.** When the assistant re-diffs a file it already diffed
  earlier in the same message, the UI greys the earlier one out (opacity 0.45).
  Markdown has no opacity, so a retained stale diff would be indistinguishable
  from the live one. The exporter ports the frontend supersession algorithm and
  **drops superseded diffs**, keeping only the final one.
- **Live-session UI chrome.** The "Auto-added N file(s) to context … Remove via
  the A button in the Files panel." banner and the "Checking context…" spinner
  are live-session affordances with no meaning in an exported document; they are
  **stripped**. The real answer next to them is preserved.

**What it does not attempt:**

- **Color is the consumer's job.** Markdown has no native color, and the export
  does **not** inject raw HTML `<span style>` / `<mark>` to force diff or
  highlight colors. Doing so would break the plain-text clipboard path and be
  stripped by Gist's sanitizer anyway (see the export-fidelity notes). Diff
  fences and language tags let the *consumer* colorize; that is by design, not a
  gap.
- **System and empty messages are skipped.** `role == "system"` messages and
  messages whose content is empty/whitespace are omitted — the **same policy the
  HTML export applies** — so an export of a system-only or empty conversation
  yields just the header and footer (it never crashes). Whether to surface
  system-prompt content in exports is an open product decision, applied
  consistently across formats rather than diverging per-format.

Markdown fidelity and hygiene are verified by the `tests/export_fidelity/`
harness (`md_fence_integrity`, `md_tool_block_fence_integrity`,
`md_diagram_embedding`, `md_math_preservation`, `md_table_integrity`,
`md_structural_sanity`, `md_roundtrip_legible`, plus the format-neutral
`no_superseded_diffs` and `no_ui_chrome` hygiene checks).

---

## Local Voice Input

The web composer supports microphone input using local `faster-whisper`
transcription. Recorded audio is sent only to the local Ziya server and is
never submitted to a browser speech service or AI model provider.

No separate installation command is required. On first use, clicking the
microphone installs `faster-whisper` through the exact Python interpreter
running Ziya, so virtual environments and pipx installations are handled
correctly. Recording begins immediately after installation completes. The first
transcription also downloads the selected Whisper model into
`~/.ziya/models/whisper/`; subsequent recordings reuse it. The default `base`
model runs on CPU with `int8` computation.

| Variable | Default | Description |
|---|---|---|
| `ZIYA_WHISPER_MODEL` | `base` | faster-whisper model name or local model path |
| `ZIYA_WHISPER_DEVICE` | `cpu` | CTranslate2 device: `cpu`, `cuda`, or `auto` |
| `ZIYA_WHISPER_COMPUTE_TYPE` | `int8` | Compute type such as `int8`, `float16`, or `default` |

Microphone capture requires a secure browser context. `http://localhost:6969`
qualifies, but a plain-HTTP LAN address generally does not.

## Thinking Mode

Some models support extended reasoning before responding:

- **Adaptive thinking** — Sonnet 4.6, Opus 4.6: controllable effort (`low` through `max`), enabled via model settings panel
- **Extended thinking** — Sonnet 3.7, Sonnet/Opus 4.0–4.5, Nova Pro/Premier: enable via model settings panel
- **Gemini thinking levels** — Gemini 3 Pro/Flash: `low`, `medium`, `high`, set in model settings

## Multi-Region Routing (Bedrock)

Models available in multiple AWS regions benefit from automatic region failover on throttle. When a request is rate-limited in the primary region, Ziya transparently retries in an alternate region before surfacing the error.

**How it works:**
- Models with cross-region inference profiles (e.g. `us.`, `eu.`, `global.` prefixes) are eligible
- Each region is weighted; the user's configured region gets a preference bonus
- When a throttle or overloaded error occurs, the request is retried once in the highest-weighted alternate region
- Throttled regions have their weight temporarily reduced, shifting subsequent requests toward healthier regions
- Weights recover automatically after a cooldown period (default: 2 minutes)

**Eligible models** (those with multi-region model IDs):
- Sonnet 4.0, 4.5, 4.6
- Sonnet 3.5, 3.5-v2
- Opus 4.6

**Environment variables:**
| Variable | Default | Description |
|---|---|---|
| `BEDROCK_REGION_COOLDOWN_SECS` | `120` | Seconds before a throttled region recovers full weight |

---

## CLI Mode

Ziya provides a full terminal interface alongside the web UI. All commands use the same model, credentials, and MCP tools as the server.

### Commands

| Command | Description |
|---|---|
| `ziya chat [FILES...]` | Interactive terminal chat with optional file context |
| `ziya ask "question" [FILES...]` | One-shot question — prints the answer and exits |
| `ziya review [--staged\|--diff] [FILES...]` | Code review with optional custom prompt |
| `ziya explain [FILES...] [--prompt "..."]` | Explain code from files or stdin |

### In-Session Commands

Inside `ziya chat`, the following slash commands are available:

| Command | Description |
|---|---|
| `/add <file\|dir>` | Add files or directories to conversation context |
| `/rm <file\|pattern>` | Remove files from context |
| `/files` | List files currently in context |
| `/shell <subcommand>` | Manage shell command allowlist (`add`, `rm`, `reset`, `yolo`, `git`, `timeout`) |
| `/goal <text>` | Set an autonomous goal (synthesizes + launches a task card) |
| `/tune <key> <val>` | Adjust session settings (e.g. `/tune iterations 50`) |
| `/model [name]` | Switch model or open interactive model picker |
| `/clear` | Clear conversation history |
| `/reset` | Clear history, context files, and all session state |
| `/suspend` | Save session and exit |
| `/resume` | Restore a previous session |
| `/join [id\|title]` | Attach to a live GUI conversation for this project (shared, synced) |
| `/help` | Show command reference |

### /goal — Autonomous Goals

The `/goal` command lets you define a verifiable objective and have Ziya work on it autonomously until it's done. Under the hood it auto-synthesizes a Task Card with an Until block and **stages** it — the inline tile shows a `staged` badge with **Run** and **Discard**, so you can review the synthesized instructions and grant permissions before the agent starts working, rather than discovering both mid-run.

```
/goal fix all TypeScript errors in frontend/src
/goal migrate from Pydantic v1 to v2 with all tests passing
/goal refactor the auth module to use dependency injection
```

If any task in the synthesized card requests shell commands or writes outside
the default safe set (`.ziya/`, `/tmp/`), the staged tile says it needs signing
and lists how many blocks are affected. **Run** still works — those blocks are
clamped to the default floor rather than the run being refused — but signing
first (via the `ziya-approve` command shown in Task Cards) is what makes the
extra permissions actually take effect.

The agent iterates (up to 15 times by default), re-evaluating whether the goal is met after each pass. Progress is visible via the inline task tile.

**Subcommands:**

| Command | Description |
|---|---|
| `/goal status` | Show the active goal's progress |
| `/goal pause` | Pause the running goal |
| `/goal resume` | Resume a paused goal |
| `/goal clear` | Cancel and remove the goal |

> **`/goal pause` vs. the task-run tile Pause button.** These are different
> mechanisms. `/goal pause` stops the goal's run (it goes to `cancelled`) and
> `/goal resume` **relaunches the card from scratch** as a fresh run — right
> for a goal's Until-loop, which re-evaluates repo state each pass. The
> **Pause button** on a task-run tile is a true in-place hold: the same run
> pauses at the next boundary (between Repeat iterations, sequence siblings,
> or `until` loops — the same boundaries Cancel uses), keeping loop progress
> and in-memory context, and **Resume** continues it. An in-flight Task/LLM
> step always finishes before the hold takes effect. A held run shows a
> distinct non-terminal `paused` status.

#### Step-debugging a run

Alongside **Pause** and **Resume**, a live run tile has a labelled **Step**
button: it advances
the run by exactly one block and holds again, which is how you walk a complex
card while building it rather than launching it and watching it die. Clicking
repeatedly queues more steps (the tile shows `held +N` for unspent ones).

Stepping works on a run that is already going, not just a paused one — the step
takes control, so the run advances to its next boundary and stops there.

Granularity is a whole block, because Step reuses the same three hold points as
Pause and Cancel (sequence siblings, Repeat iterations, `until` loops) and adds
none. Stepping past a Task runs that entire Task, including all of its LLM
iterations and tool calls; there is no mid-Task stop. One step buys one unit of
real work at any nesting depth — descending into a group or starting a new loop
iteration is free.

A held run keeps its `held` chip when the tile is collapsed to its one-line
receipt, so a run waiting on you is distinguishable from a finished one — but
the receipt carries no buttons, so expand the tile to reach Step and Resume.

#### When a finished tile folds itself away

A tile that finishes while you are not touching it collapses to its receipt
after 8 seconds. Interacting with it — clicking, typing, selecting trace text —
pushes that out by a quiet period, so reading is never interrupted mid-sentence.

Expanding a tile **by hand** does more: it pins the tile open, and only
collapsing it yourself closes it again. A run held on an infrastructure fault
never auto-collapses at all, since the receipt offers no way to resume it.

#### When infrastructure breaks under a fan-out

A held run is one that stopped because the *environment* broke — expired
credentials, a lost endpoint, throttling that outlasted its retries — rather
than because the work failed. The distinction matters because the two ask for
opposite responses: a failed run needs the card or the code fixed, a held run
needs only the infrastructure back before it continues from where it stopped.

Inside a wide fan-out this is not a single event but a collapse. When one
subagent's credential dies, its siblings are usually about to hit the same wall,
so the hold reports its **breadth**, not just the first fault: how many
subagents faulted out of how many ran, which kinds, and the call path from the
outermost card down to the subagent that raised it. A `fleet-wide` marker
separates "the credential died and took all 20 auditors" from "one auditor got
throttled" — both are infrastructure faults, and only one of them means you
should stop and go fix something.

Whether the remaining subagents are cancelled depends on the kind. An
authentication fault is session-level: one means every sibling is already dead,
so the fan-out is cut short immediately rather than burning the rest against a
dependency known to be gone. Throttling and transient service errors are
per-request and have already survived several retries with backoff, so a single
one never aborts a healthy fan-out — the run holds, but the siblings that can
still finish do. A proportion of the fan-out failing that way does gate it;
`ZIYA_TASK_INFRA_GATE_RATIO` (default `0.34`) sets that fraction, so the threshold
scales with the width of the fan-out rather than being a fixed count.

The conversation list carries a **gear per run status**, so what a chat's tasks
are doing is legible without opening it. The gear is colour-coded to match the
run tile — blue spinning for running, violet static for paused or held, green
for done, red for failed, amber for partial or cancelled — and animates only
while something is genuinely progressing, since a spinning indicator is how you
decide to keep waiting rather than intervene.

Where a conversation holds more than one task, each status gets its own gear
with a count beside it: "2 done, 1 held" is a different situation from either
"3 done" or "1 held", and collapsing them to a single winner would hide
whichever one you were looking for. Needs-attention states are ordered first so
a problem cannot be pushed off the end of a narrow row by successes. Counts
appear from two upward — a "1" beside a lone gear is noise. Retry attempts count
once, not once per attempt, so a card retried twice reports one gear rather than
three.

This matters most for terminal states. The gear previously meant only "a task is
running", so every stopped state — done, failed, cancelled, partial, held —
rendered as nothing at all, and a conversation whose overnight study died on an
expired credential looked identical to one that had never run a task.

The indicators cover **every** conversation, not just the one you have open.
A run that finishes, fails, or holds in a conversation you have not visited
still updates that row, which is the whole point of a background indicator: the
work worth being told about is the work you are not currently watching. This is
polled from a small server-side projection rather than the run records
themselves — those carry block states, iteration summaries and artifacts and
are encrypted at rest, so reading them all to learn a few status strings would
cost work proportional to your entire run history on every tick. Only the
project you have open is polled, at a 40-second interval, and only while
something can still change on its own; polling pauses while the window is in
the background and refreshes on return. On a project with two hundred runs of
history an idle check costs about four hundredths of a millisecond, so having
many projects and a long task history does not accumulate cost.

Because a hold propagates up through nested cards, the run map marks every
row with its position relative to the fault, so you do not have to open each
subagent to find out which one broke. The block that raised it reads
**HELD HERE**; the containers above it read **holding**, since they cannot
finish while a step below them is stopped; and anything that never got to run
reads **blocked**. Hovering any of them explains the fault and its breadth.

A called card can also answer the question from its own side. A Call runs
inline in the caller's run, so a six-card study produces one run record owned
by the outermost card — which meant opening one of the inner cards directly
showed nothing, even while that card was the one holding the study. Opening it
now resolves its own portion of the blocking tree: which of *its* blocks is
held, which of its stages are blocked behind it, and the same breadth and
remedy the caller shows. A hold in a sibling card is reported as context only
and never marked on this card's blocks, since pointing at a card that is fine
is worse than showing nothing.

Within a fan-out, the iteration dots separate the subagents that actually
faulted from the ones the gate cancelled — a cancelled sibling was killed
deliberately because a peer hit dead infrastructure, so it is not counted as
a failure of the work.

Resuming a wide parallel fan-out does **not** re-run every iteration. Picking
a single iteration is refused — parallel iterations do not depend on each
other, so there is no ordering for "resume at 3" to mean — but the block-level
retry that remains banks every iteration that already produced a result and
executes only the ones that never finished. For a 20-agent audit that lost one
subagent to an expired credential, that is one subagent re-run, not twenty.
Stages *before* the loop replay from record as usual. Clicking an iteration of
a parallel loop therefore explains the refusal and points at the block-level
retry, since taking it costs nothing.

#### Resuming a finished run from a block

A run that died partway through used to be unrecoverable: the only option was
relaunching the card, discarding every block that had already succeeded.

A stopped run now leads with a **recovery banner** naming the block it stopped
at, with **↻ Retry \<block\>** and **▶ Continue past it**. Both start a *new*
run that replays the earlier blocks' recorded results instead of re-running
them; they differ only in whether the named block itself runs again — continue
is what you want after fixing the cause by hand.

This is separate from **Restart** in the tile header, which relaunches the card
from the beginning and keeps none of the run's progress. The banner says so,
because Restart is the more prominent control and is usually the wrong one for
a run that got partway.

For a deliberate choice other than the stopping point, every row of the block
map also carries **↻ from here** and **▶ past here** on hover — useful for
re-running from *earlier* than the failure.

What this preserves, and why:

- Earlier blocks are marked `skipped` but keep their original summaries, so
  `{{sibling("id")}}` and `{{previous_sibling}}` still resolve.
- A replayed block's failure flag is cleared — otherwise an `on_failure="stop"`
  sequence would halt before ever reaching your target.
- `state` blocks are genuinely re-run (they only write authored literals), which
  is how `{{var.NAME}}` is rebuilt.
- The original run's launch-time variable overrides are carried forward.

The source run is kept as an immutable record, so the resumed run appears as a
second tile next to it rather than replacing it. Picking a block inside a loop
body resumes from the **whole enclosing loop**, because only structural blocks
carry per-block state — so you can click any row and let the server decide.
Runs created before run snapshotting existed cannot be resumed, and show no
button.

Retrying a loop this way does **not** restart it at iteration zero. The retry
banks the iterations the loop already completed and restarts at the first one
that did not, so a run held 22 iterations into a serial campaign re-runs from
22 — you do not have to find and click the right iteration dot to get that.

The banner names that iteration rather than only the loop: the button reads
**↻ Resume \<loop\> at #22** and the note says how many iterations will be
replayed. Without it, the control that preserves 22 iterations described
itself identically to one that would re-run them. The number is a prediction
of a server-side decision, so it is worded as where execution resumes rather
than as a promise — in the two cases it can be wrong (a chained resume whose
carried iterations are not visible to the browser, or a record that disagrees
with what is on disk) the run starts *earlier or later* than named, and the
resumed run's dot strip shows what actually happened.

The rule differs by loop shape, because the shapes mean different things:

- A **serial** loop banks a *prefix*. Its iterations are dependent —
  `{{previous}}` binds the one before — so the prefix ends at the first
  iteration that failed, was never recorded, or whose full result was dropped
  past the 50-pass retention cap. Everything before that point still replays.
- A **parallel** loop banks an *index set*, since its iterations are
  independent and a gap in the middle is simply filled.

A `▶ Continue past it` on a loop is unaffected: it resumes *after* the loop,
which then replays whole as a single block.

#### Resuming inside a loop

The recovery banner already restarts a serial loop at the first iteration
that did not complete, so you do not normally need this section. It exists for
the *deliberate* choice: resuming from an iteration other than the automatic
one — earlier than the failure, or one past a result you have fixed by hand.

Iteration dots on a loop row carry the same two actions the block rows do,
applied to one iteration:

Click an iteration dot to focus it; the detail panel below then offers:

- **↻ re-run #N** re-runs that iteration.
- **▶ continue from #N+1** accepts its recorded result and runs the next one.

The buttons live in the detail panel rather than on the dot itself because
they need a sentence explaining that earlier iterations are replayed — a
user who doesn't know that will assume the loop restarts from zero.

Earlier iterations are **replayed from record** rather than re-executed, so
the first iteration that actually runs receives the same `{{previous}}` and
`{{all}}` bindings it saw originally. Blocks before the loop replay through
the existing block-level mechanism, exactly as any other resume.

The resumed run's dot strip shows the replayed iterations as **dimmed dots
preceding the ones it executed**, so the preserved work is visible as
preserved. They keep their original colour — a preserved failure still reads
red — and stay clickable, because the carried artifacts are copied onto the
resumed run. Without this the strip restarted at one circle, which was
indistinguishable from a fresh short run and read as though the banked
iterations had been thrown away.

Replayed iterations are excluded from the run's own progress figures
("N iterations passed", the partial-run classification, and failure
clustering), so an attempt is never credited with a prior attempt's results.

Two cases are refused rather than half-supported, because both would produce
a run that looks successful while feeding empty input to the work:

- **A parallel loop.** Its iterations cannot see each other, so there is no
  ordering for "resume at 3" to mean — the earlier iterations were never
  prerequisites. Retry the whole loop instead.
- **A predecessor whose full result was dropped.** Only the first 50 passing
  iterations of a loop keep their complete artifact; past that there is
  nothing to replay into `{{previous}}`. Picking a specific iteration past
  that point is refused; the block-level retry instead banks everything up to
  the cap and re-runs from there, which is a shorter prefix rather than a
  refusal.

While stepping, the status tag briefly reads `running`, because the executor
genuinely is running the block your step bought. The `held` chip beside it is
the thing to watch: it stays lit for as long as the run is under your control.

### /join — Continue a GUI Conversation from the Terminal

`/join` attaches your CLI session to a conversation that already exists in the
Ziya GUI for the same project directory, so both surfaces operate on the same
underlying chat. Run it with no argument for an interactive picker, or pass a
conversation id (or title) to attach directly:

```
/join
/join "Auth refactor"
```

You can also attach at launch:

```bash
ziya chat --join            # interactive picker
ziya chat --join "Auth refactor"
```

While attached:

- The GUI chat's `id` becomes the session's conversation id, so **beads,
  task-card results, and the GUI sidebar all track the shared conversation**.
- Each completed turn is **written back** into the GUI chat; message ids for the
  unchanged prefix are preserved so the sidebar doesn't churn.
- Turns added elsewhere — from the GUI, or another attached CLI — are **pulled
  in and previewed at the next prompt**. Your input buffer is never touched, so
  you can keep typing while sync happens.
- A `[⇄<id>]` badge on the prompt marks the attached state.

To split off a private local branch, use the existing fork mechanic: **`/save`
forks the current history into a local session and detaches** — the GUI
conversation is left untouched and your CLI continues privately from that point.
`/clear` and `/reset` also detach first (clearing local history while attached
would otherwise truncate the shared GUI chat).

> The GUI has no "join from GUI" affordance yet; attachment is CLI-initiated.
> Concurrent edits are last-writer-wins in this version.

`/join` requires the directory to have been opened in the GUI at least once (so
a project record exists); otherwise there are no conversations to join.

### Piping

Any command that accepts content also reads from stdin, so standard Unix piping works:

```bash
git diff | ziya review                      # Review uncommitted changes
git diff --cached | ziya review             # Same as: ziya review --staged
cat error.log | ziya ask "what's wrong?"    # Diagnose a log file
cat utils.py | ziya explain                 # Explain a file via pipe
```

When both a question argument and piped input are provided, they are combined:

```bash
cat handler.py | ziya ask "find the bug"    # "find the bug" + file contents
```

### Common flags

All subcommands accept the same global flags:

```bash
ziya ask "..." --model haiku-4.5            # Use a specific model
ziya review --staged --profile prod         # Use a specific AWS profile
ziya ask "..." --endpoint google            # Use Google Gemini
ziya chat --no-stream                       # Disable streaming output
ziya chat --debug                           # Enable debug logging
```

Flags can appear before or after the subcommand:

```bash
ziya --profile dev ask "explain this"       # Equivalent to:
ziya ask "explain this" --profile dev
```

### Sessions

Interactive `chat` sessions are auto-saved to `~/.ziya/sessions/`. Resume a previous session with:

```bash
ziya chat --resume                          # Interactive session picker
ziya chat --ephemeral                       # Don't save this session
```



