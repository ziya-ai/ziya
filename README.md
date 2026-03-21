<p align="center">
  <img src="docs/images/logo.svg" alt="Ziya" width="280">
</p>

<h3 align="center">AI Technical Workbench for Code, Architecture, and Operations</h3>

<p align="center">
  Self-hosted. Runs alongside your editor, not instead of it.
</p>

<p align="center">
  <a href="https://pypi.org/project/ziya/"><img alt="PyPI" src="https://img.shields.io/pypi/v/ziya?style=flat-square&color=b32ca8"></a>
  <a href="https://pypi.org/project/ziya/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/ziya-ai/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/ziya-ai/ziya?style=flat-square"></a>
</p>

<!-- Uncomment when hero.gif or hero-screenshot.png is captured:
<p align="center">
  <img src="docs/images/hero.gif" alt="Ziya in action" width="800">
</p>
-->

---

## What is Ziya?

Ziya is a self-hosted AI technical workbench — a browser-based environment where you work with AI on code, architecture, and operational analysis in a single conversation. It was originally developed by engineers at a major technology company as an internal tool for real development and operations workflows, and has been used in production across hundreds of engineers.

It is **not** an IDE, not a plugin, and not a terminal-only CLI. You keep your editor. Ziya is the surface where you think about your systems — where code context, operational data, and visual analysis come together.

```bash
pip install ziya
ziya
```

Then open [localhost:6969](http://localhost:6969). That's it.

---

## What Makes This Different

### 🔧 Rendered Diffs with Apply/Undo

Code changes appear as structured diffs with per-hunk Apply and Undo buttons. A multi-strategy patch pipeline (`patch` → `git apply` → difflib → LLM resolver) handles imperfect model output gracefully. No more copy-paste from a chat window.

<!-- Uncomment: ![Diff apply](docs/images/diff-apply.gif) -->

### 📐 Architecture & Operations Analysis

Paste a thread dump and get a Graphviz deadlock diagram. Ask about your system architecture and get a DrawIO diagram generated from the actual code. Drag and drop existing architecture diagrams, operational plots, or monitoring screenshots directly into the conversation for integrated visual analysis alongside your codebase. This is the gap no coding assistant fills — Ziya works with operational data, not just source files.

<!-- Uncomment: ![Ops analysis](docs/images/ops-analysis.png) -->

### 📊 Six Visualization Renderers

All rendered inline, all with a normalization layer that handles imperfect LLM output:

| Renderer | Use Cases |
|---|---|
| **Graphviz** | Dependency graphs, call flows, lock cycles, network topologies |
| **Mermaid** | Sequence diagrams, flowcharts, ER diagrams, state machines |
| **Vega-Lite** | Latency distributions, throughput charts, statistical plots |
| **DrawIO** | System architecture, exportable `.drawio` files |
| **KaTeX** | Inline and display math |
| **Packet Diagrams** | Bit-level protocol frame layouts with rulers and annotations |

Plus **HTML mockups** — interactive UI previews rendered in isolated iframes.

### 🤖 Parallel AI Agents (Swarm)

Decompose complex tasks into parallel delegates that run simultaneously, each with independent context. Completed delegates produce "crystals" — compacted memory summaries that downstream agents can query. Recursive sub-swarms supported. Live progress tracking in the sidebar.

### 🔌 MCP Tool Integration with Security Controls

Connect any MCP server (stdio or remote HTTPS). Built-in protections:
- **Tool poisoning detection** — descriptions scanned for prompt injection
- **Tool shadowing prevention** — external tools can't override built-ins
- **Rug-pull detection** — tool definitions fingerprinted; changes on reconnect trigger warnings

Browse and install MCP servers from the built-in registry.

### 🧠 Skills System

Activate reusable instruction bundles that steer model behavior — documentation standards, code review checklists, operational runbooks. Create custom skills from the UI. Skills compose: stack multiple for a single conversation.

### 📁 Project-Scoped Everything

Multiple simultaneous projects, each with its own conversations, context, and file tree. Conversation forking, per-message editing, export/import. Token budget visible per file.

### 💻 Web UI + CLI

Full browser UI at `localhost:6969`. Also a rich terminal mode (`ziya chat`) with `prompt_toolkit` autocomplete, multiline paste detection, and streaming. One-shot mode (`ziya ask "question"`), code review (`ziya review --staged`), and pipe support (`git diff | ziya review`).

---

## How People Use It

**Development** — Ask about code, get diffs with one-click apply. Generate architecture diagrams from the actual codebase. Fork conversations to explore alternatives. Run parallel agents on complex refactors.

**Operations** — Paste thread dumps, log snippets, or error traces. Get visual analysis: deadlock diagrams, latency charts, packet breakdowns. Drag and drop existing monitoring dashboards, Grafana screenshots, or CloudWatch plots for AI-assisted interpretation alongside the code that produced the data.

**Architecture** — Point it at a codebase and get living architecture documentation — DrawIO and Mermaid diagrams generated from what the code actually does, not from stale wiki pages. Ask "what happens if this service goes down?" and get failure mode diagrams with affected paths highlighted.

---

## Supported Models

| Provider | Models | What You Need |
|---|---|---|
| **AWS Bedrock** | Claude 4.6/4.5/4.0/3.7/3.5 (Sonnet, Opus, Haiku), Nova Premier/Pro/Lite/Micro, DeepSeek R1/V3, Qwen3, and more | AWS credentials with Bedrock access |
| **Google** | Gemini 3.1 Pro, 3 Pro/Flash, 2.5 Pro/Flash, 2.0 Flash | `GOOGLE_API_KEY` |
| **OpenAI** | GPT-4.1/Mini/Nano, GPT-4o, o3, o4-mini | `OPENAI_API_KEY` |

Switch models mid-conversation. Configure temperature, top-k, top-p, max tokens from the UI. Prompt caching reduces cost and latency on follow-up messages.

---

## Quick Start

```bash
# Install
pip install ziya

# For AWS Bedrock (most common)
export AWS_ACCESS_KEY_ID=<your-key>
export AWS_SECRET_ACCESS_KEY=<your-secret>

# For Google Gemini
export GOOGLE_API_KEY=<your-key>

# Run
ziya
```

Open [localhost:6969](http://localhost:6969). Ziya reads your codebase from the current directory and loads it as context.

### Common Options

```bash
# Use a specific model
ziya --endpoint=bedrock --model=sonnet4.0

# Exclude build artifacts
ziya --exclude='node_modules,dist,*.pyc'

# Focus on specific directories
ziya --include-only='src,lib'

# CLI chat mode (terminal)
ziya chat

# One-shot question
ziya ask "explain the authentication flow"

# Code review
git diff | ziya review
```

See `ziya --help` for all options, or configure everything interactively in the web UI.

---

## Comparison

| | IDE Forks | CLI Tools | Extensions | **Ziya** |
|---|---|---|---|---|
| Keep your editor | ❌ | ✅ | ✅ | ✅ |
| Rich visual UI | ✅ | ❌ | Partial | ✅ |
| Hunk-level diff apply | Partial | ❌ | ❌ | ✅ |
| Inline diagrams (6 types) | ❌ | ❌ | ❌ | ✅ |
| Operational data → visual analysis | ❌ | ❌ | ❌ | ✅ |
| Drag-and-drop image/document analysis | Partial | ❌ | ❌ | ✅ |
| Self-hosted / fully private | ❌ | ✅ | ❌ | ✅ |
| Parallel agents (swarm) | ❌ | ❌ | ❌ | ✅ |
| Web + Terminal modes | ❌ | Terminal only | ❌ | ✅ |
| Multi-model switching | Partial | ✅ | Partial | ✅ |

---

## Enterprise

Ziya includes a plugin architecture for enterprise deployment — pluggable auth, endpoint restriction, encryption-at-rest, data retention policies, shared Bedrock accounts, and custom MCP formatting. Currently deployed at scale internally at a major technology company.

See [Docs/Enterprise.md](Docs/Enterprise.md) for the full plugin system.

---

## Documentation

- [Feature Inventory](Docs/FeatureInventory.md) — comprehensive list of every capability
- [Architecture Overview](Docs/ArchitectureOverview.md) — system design and component map
- [User Configuration](Docs/UserConfigurationFiles.md) — `~/.ziya/` config files reference
- [MCP Security Controls](Docs/MCPSecurityControls.md) — tool poisoning, shadowing, rug-pull detection
- [Skills](Docs/Skills.md) — the skills system and built-in skills
- [Enterprise](Docs/Enterprise.md) — plugin interfaces and internal deployment
- [Brand Guide](Docs/BrandGuide.md) — logo and color specifications

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug fixes, visualization improvements, model support, and documentation are all welcome.

## Security

See [SECURITY.md](SECURITY.md). Do not open public issues for security vulnerabilities.

## License

[MIT](LICENSE)
