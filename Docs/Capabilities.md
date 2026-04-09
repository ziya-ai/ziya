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

Rendered diagrams include **Open** (popup with zoom/pan), **Save** (SVG download), and **Source** (view/edit definition) buttons.

**Skip-edge rerouting**: For Mermaid diagrams with feedback/control-loop edges that span multiple nodes, a post-render rerouter automatically arcs those paths above or below intermediate nodes instead of drawing them straight through. Arcs are nested by skip distance — shorter-range arcs sit closer to the node row, longer-range arcs arc further out — so overlapping edges remain visually distinct even when several skip edges share the same side.

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

---

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
| `/tune <key> <val>` | Adjust session settings (e.g. `/tune iterations 50`) |
| `/model [name]` | Switch model or open interactive model picker |
| `/clear` | Clear conversation history |
| `/reset` | Clear history, context files, and all session state |
| `/suspend` | Save session and exit |
| `/resume` | Restore a previous session |
| `/help` | Show command reference |

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



