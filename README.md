<p align="center">
  <img src="Docs/social-preview.jpg" alt="Ziya AI Technical Workbench">
</p>

<p align="center">
Self-hosted AI workbench for code, architecture, and operations.<br>
Runs alongside your editor — not instead of it.
</p>

<p align="center">
  <a href="https://pypi.org/project/ziya/"><img alt="PyPI" src="https://img.shields.io/pypi/v/ziya?style=flat-square&color=2dd4bf"></a>
  <a href="https://pypi.org/project/ziya/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/ziya-ai/ziya?style=flat-square"></a>
  <a href="https://github.com/ziya-ai/ziya/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/ziya-ai/ziya?style=flat-square&color=f1c40f"></a>
</p>

<!-- Uncomment when hero.gif is captured:
<p align="center">
  <img src="docs/images/hero.gif" alt="Ziya demo" width="800">
</p>
-->

---

## What is Ziya?

Ziya is a self-hosted AI technical workbench. It's not an IDE, not a plugin, not a terminal-only CLI — it's the surface where code, architecture analysis, and operational diagnostics converge in a single conversation with rich visual output.

It was originally developed by engineers at a major technology company as an internal tool for real development and operations workflows, and has been used in production across hundreds of engineers. The community edition is open source under the MIT license.

**Key idea:** You keep your editor, your terminal, your monitoring tools. Ziya is where you *think* about your systems — ask questions, get visual answers, apply changes, and coordinate parallel work.

## What Makes This Different

### 🔧 Rendered Diffs with Apply/Undo
<!-- Uncomment: <img src="docs/images/diff-apply.gif" alt="Diff application" width="700"> -->
Code changes rendered as structured diffs with per-hunk Apply/Undo buttons and individual status tracking. The 4-stage patch pipeline handles imperfect model output gracefully — no more copy-pasting from ChatGPT.

### 🧭 User-Controlled Context Curation
Most AI tools auto-compact your conversation when context fills up — the machine decides what to keep and what to summarize away. Ziya takes a different approach: **you decide what matters.**

- **Mute any message** — exclude it from model context without deleting it (unmute anytime)
- **Fork from any point** — branch off to explore a tangent, optionally truncate to shed context weight
- **Edit or resubmit** — revise any message in the history
- **Selective file removal** — drop files from context when they've served their purpose

This keeps you in control of what the model retains. In 18+ months of daily use with very large contexts, deliberate curation has proven more reliable than automatic summarization, which risks discarding details that the user knows are important but the model doesn't recognize.

### 📊 Architecture & Operations Analysis
<!-- Uncomment: <img src="docs/images/ops-analysis.png" alt="Operations analysis" width="700"> -->
Paste a thread dump → get a Graphviz deadlock diagram. Ask about data flow → get a DrawIO architecture diagram built from the actual code. Drop in latency data → get a Vega-Lite trend chart. Drag and drop existing architecture diagrams, operational plots, or monitoring screenshots directly into the conversation for integrated visual analysis alongside your codebase.

This is the gap no other AI coding tool fills. Cursor, Aider, Claude Code optimize for *writing* code. Ziya also helps you *understand and diagnose* the systems running it.

### 🎨 Seven Visualization Renderers
Graphviz · Mermaid · Vega-Lite · DrawIO · KaTeX · HTML mockups · Packet frame diagrams

All renderers include a normalization layer that handles imperfect LLM output. Diagrams render inline in the conversation, not in a separate window.

### 🤖 Parallel Agent Swarms
Decompose complex tasks into parallel delegates that run simultaneously. Each delegate has its own context, 9 coordination tools, and produces a crystal (compressed memory summary) when complete. Delegates can spawn sub-swarms. Progressive checkpointing survives crashes.

### 🔌 MCP Tool Integration
Connect any MCP server (local or remote). Built-in security: tool poisoning detection, shadowing prevention, rug-pull detection, cryptographic result signing. Shell commands are allowlisted — configurable per-session or persistently.

### 🎯 Projects, Contexts, and Skills
Organize work by project with scoped conversations, file contexts, and reusable skill bundles. Each project maintains its own history and context selections. Switch between projects without losing state.

### 🖥️ Web + CLI, Same Codebase
Full web UI at `localhost:6969` with rich rendering. Full CLI with `ziya chat`, `ziya ask`, `ziya review`, `ziya explain`. Same features, same codebase, your choice.

---

## How People Use It

**Development** — Ask about code, get diffs with Apply buttons, see architecture diagrams generated from your actual code, run parallel agents for large refactors. Drag and drop screenshots of UI bugs for visual context alongside the source.

**Operations** — Paste thread dumps, log extracts, or error traces and get visual root cause analysis correlated with your codebase. Drag and drop existing monitoring dashboards, Grafana screenshots, or CloudWatch plots for AI-assisted interpretation alongside the code that produced the data.

**Architecture** — Point it at a codebase and get living architecture documentation built from what the code actually does — not from stale diagrams someone drew six months ago.

---

## Quick Start

```bash
pip install ziya
```

**For AWS Bedrock** (default):
```bash
export AWS_ACCESS_KEY_ID=<your-key>
export AWS_SECRET_ACCESS_KEY=<your-secret>
ziya
```

**For Google Gemini:**
```bash
export GOOGLE_API_KEY=<your-key>
ziya --endpoint=google
```

**For OpenAI:**
```bash
export OPENAI_API_KEY=<your-key>
ziya --endpoint=openai
```

Then open [http://localhost:6969](http://localhost:6969).

**CLI mode** (no browser):
```bash
ziya chat                          # Interactive chat
ziya ask "what does this do?"      # One-shot question
ziya review --staged               # Review git staged changes
git diff | ziya ask "review this"  # Pipe anything in
```

---

## Supported Models

| Provider | Models | What You Need |
|---|---|---|
| **AWS Bedrock** | Claude Sonnet 4.6/4.5/4.0/3.7, Opus 4.6/4.5/4.1/4.0, Haiku 4.5/3, Nova Premier/Pro/Lite/Micro, DeepSeek R1/V3, Qwen3, Kimi K2.5, and more | AWS credentials with Bedrock access |
| **Google** | Gemini 3.1 Pro, 3 Pro/Flash, 2.5 Pro/Flash, 2.0 Flash | Google API key |
| **OpenAI** | GPT-4.1/Mini/Nano, GPT-4o, o3, o3-mini, o4-mini | OpenAI API key |
| **Anthropic** | Claude (direct API) | Anthropic API key |

Switch models mid-conversation. Configure temperature, top-k, max tokens, and thinking mode from the UI.

---

## How It Compares

| | IDE Forks (Cursor, Windsurf) | CLI Tools (Aider, Claude Code) | Extensions (Cline, Copilot) | **Ziya** |
|---|---|---|---|---|
| Keep your editor | ❌ | ✅ | ✅ | ✅ |
| Rich visual UI | ✅ | ❌ | Partial | ✅ |
| Diff apply with per-hunk status | Partial | ❌ | ❌ | ✅ |
| Inline diagrams (6+ types) | ❌ | ❌ | ❌ | ✅ |
| Operational data → visual analysis | ❌ | ❌ | ❌ | ✅ |
| User-controlled context curation | ❌ | ❌ | ❌ | ✅ (mute/fork/truncate/prune) |
| Self-hosted / data stays local | ❌ | ✅ | ❌ | ✅ |
| Project & context management | ❌ | ❌ | ❌ | ✅ |
| Parallel agent swarms | ❌ | ❌ | ❌ | ✅ |
| Web + CLI modes | ❌ | Terminal only | ❌ | ✅ |
| Drag-and-drop images for analysis | ✅ | ❌ | Partial | ✅ |
| MCP with security controls | Partial | Partial | Partial | ✅ |

---

## Enterprise

Ziya includes a plugin system for enterprise deployment — pluggable auth providers, endpoint restrictions, data retention policies, encryption at rest, and custom tool configuration. Currently deployed at scale internally at a major technology company. See [Docs/Enterprise.md](Docs/Enterprise.md) for details.

---

## Documentation

- [Feature Inventory](Docs/FeatureInventory.md) — complete capability reference
- [Architecture Overview](Docs/ArchitectureOverview.md) — system design
- [MCP Security](Docs/MCPSecurityControls.md) — tool security model
- [Skills](Docs/Skills.md) — reusable instruction bundles
- [User Configuration](Docs/UserConfigurationFiles.md) — `~/.ziya/` config files
- [Enterprise](Docs/Enterprise.md) — plugin system and deployment

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Security

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## License

MIT — see [LICENSE](LICENSE).
