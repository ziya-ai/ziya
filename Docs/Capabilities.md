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
| `gemini-3-pro` | Gemini 3 Pro Preview | 1M | Thinking levels. Native function calling. |
| `gemini-3-flash` | Gemini 3 Flash Preview | 1M | Thinking levels. Native function calling. |
| `gemini-2.5-pro` | Gemini 2.5 Pro | 1M | Native function calling. |
| `gemini-flash` | Gemini 2.5 Flash | 1M | Native function calling. |
| `gemini-2.0-flash` | Gemini 2.0 Flash | 1M | |
| `gemini-2.0-flash-lite` | Gemini 2.0 Flash Lite | 1M | No function calling. |
| `gemini-2.5-flash-lite` | Gemini 2.5 Flash Lite | 1M | Thinking mode. |

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

Connect any MCP-compatible server to give the model additional capabilities — shell access, internal APIs, databases, and more. See your server's documentation for setup.

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

Files outside the project root can be modified if they were added via the file browser.

---

## Context & Projects

- Multiple projects can be open in separate browser tabs simultaneously
- Each project has its own conversation history, contexts, and skills
- The file tree shows token counts to help you manage context size
- Files outside the project root can be added via the browser
- Context window usage is shown in the toolbar

---

## Vision / Multimodal

Drag images into the chat input, paste from clipboard, or use the image button. Supported on: Claude Sonnet/Opus 4.x, Claude 3.x, Nova Pro/Lite/Premier, Gemini.

---

## Thinking Mode

Some models support extended reasoning before responding:

- **Adaptive thinking** — Sonnet 4.6, Opus 4.6: controllable effort (`low` through `max`), enabled via model settings panel
- **Extended thinking** — Sonnet 3.7, Sonnet/Opus 4.0–4.5, Nova Pro/Premier: enable via model settings panel
- **Gemini thinking levels** — Gemini 3 Pro/Flash: `low`, `medium`, `high`, set in model settings
