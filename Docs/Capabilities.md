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
| `opus4.1` | Claude Opus 4.1 | 200K | Advanced. |
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
| HTML Mockup | `` ```html-mockup `` | Interactive UI prototypes in sandboxed iframes. |
| Packet | `` ```packet `` | Bit-level protocol frame layouts. |
| TikZ | `` ```tikz `` | General LaTeX vector drawing. Rendered server-side. |
| CircuiTikZ | `` ```circuitikz `` | Electronic circuit schematics. |
| chemfig | `` ```chemfig `` | Chemical structures, reaction schemes, stereochemistry. |
| tikz-cd | `` ```tikz-cd `` | Commutative diagrams. |

Rendered diagrams include **Open** (popup with zoom/pan), **Save** (SVG download), and **Source** (view/edit definition) buttons.

### LaTeX diagrams (server-side)

The four LaTeX-family types above are compiled by a local TeX installation rather
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
sudo tlmgr install standalone dvisvgm pgf circuitikz siunitx chemfig tikz-cd
```

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

The `/goal` command lets you define a verifiable objective and have Ziya work on it autonomously until it's done. Under the hood it auto-synthesizes a Task Card with an Until block and launches it immediately.

```
/goal fix all TypeScript errors in frontend/src
/goal migrate from Pydantic v1 to v2 with all tests passing
/goal refactor the auth module to use dependency injection
```

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

#### Resuming inside a loop

A loop was the one shape resume could not help. Picking any row inside a
Repeat or Until body resumes the **whole enclosing loop**, because only
structural blocks carry per-block state — so a five-iteration campaign that
lost its last iteration to an expired credential had to re-pay all five, and
the four banked passes were discarded. That is the most expensive lost work
the task system had, since a long loop is precisely where a run is most
likely to outlive a credential.

Iteration dots on a loop row now carry the same two actions the block rows
do, applied to one iteration:

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

Two cases are refused rather than half-supported, because both would produce
a run that looks successful while feeding empty input to the work:

- **A parallel loop.** Its iterations cannot see each other, so there is no
  ordering for "resume at 3" to mean — the earlier iterations were never
  prerequisites. Retry the whole loop instead.
- **A predecessor whose full result was dropped.** Only the first 50 passing
  iterations of a loop keep their complete artifact; past that there is
  nothing to replay into `{{previous}}`.

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



