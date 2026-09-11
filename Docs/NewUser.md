# Getting Started with Ziya

## Prerequisites

- Python 3.10 – 3.14
- AWS credentials with Bedrock access (or a Google API key for Gemini)

---

## Installation

```bash
pip install ziya
```

Or with pipx (recommended):

```bash
pipx install ziya
```

### Optional rendering dependencies

Everything the browser renders inline (Mermaid, Graphviz, Vega-Lite, Plotly,
DrawIO, packet and timing diagrams, KaTeX, …) works with no extra install.
Two things are too big for pip to install, and one command adds both:

```bash
ziya-install-extras            # prints the plan, asks, then installs
ziya-install-extras --dry-run  # just show the plan
```

| Capability | What it needs | Installer target |
|---|---|---|
| The model **looking at** its own rendered diagram (`render_diagram`), PDF export, and frozen diagram artifacts in task runs | A Chromium build for Playwright (~150 MB, no sudo) | `--browser` |
| Server-side LaTeX diagrams — circuit schematics (circuitikz), chemical structures (chemfig), pgfplots, TikZ, proof and syntax trees | A TeX distribution plus a handful of TeX Live packages (sudo for `tlmgr`) | `--latex` |

Nothing is installed silently: the script prints every package before it runs
and waits for a yes. On macOS it uses Homebrew for BasicTeX and will tell you
if Homebrew itself is missing rather than install it for you. Restart Ziya
afterwards. The startup banner lists whichever of these is still missing.

Without the Chromium build the `render_diagram` tool is simply not offered to
the model; nothing else is affected. PCAP analysis needs nothing extra —
scapy ships with Ziya and reads capture files without libpcap.
Without TeX, a LaTeX-family diagram reports exactly which packages are
missing and the `tlmgr` command that installs them.

---

## Quick Start

Navigate to your project directory and run:

```bash
cd /path/to/your/project
ziya
```

Open `http://localhost:6969` in your browser.

### Credentials on first run

Ziya defaults to AWS Bedrock but works with several providers. On first run it
detects which credentials you already have and, if AWS isn't set up but exactly
one other provider is, **auto-selects that provider and tells you it did so**
(once). Set the environment variable for whichever provider you want:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or: ziya --endpoint anthropic
export OPENAI_API_KEY=sk-...          # or: ziya --endpoint openai
export GOOGLE_API_KEY=...             # or: ziya --endpoint google
aws configure                         # AWS Bedrock (the default)
ziya --profile my-profile             # use an existing named AWS profile
```

**No cloud account at all?** Run a model locally with Ollama, LM Studio or
llama.cpp and point Ziya at it:

```bash
ollama pull qwen2.5-coder:7b        # or: ./ds4-server --ctx 100000 (DwarfStar)
ziya --endpoint local
```

That is the whole setup. With nothing else configured Ziya scans the
well-known loopback ports — Ollama `:11434`, DwarfStar `:8000`, LM Studio
`:1234`, llama-server `:8080` — and **every server that answers becomes its
own endpoint** in the model picker: *Local · DwarfStar (:8000)*, *Local ·
Ollama (:11434)*, each listing exactly the models that server reports. Pick
a model and you have picked its server; nothing guesses. `--endpoint local`
is shorthand for "the local server" — with one running it is that one, with
several it takes the first and names the others in the log (pass one as
`--endpoint local-dwarfstar`, or switch in the picker). Ziya then asks the
server what each model supports and budgets against its **full context
window** — DwarfStar reports the `--ctx` it was launched with; on Ollama the
length is also sent as `num_ctx` on every request, so you are not stuck at
Ollama's 4k default. Small Ollama models sometimes write a tool call as
plain JSON text instead of a real call (qwen2.5-coder:7b does); for
Ollama-served models Ziya recognises a response that *is* such an object
and runs the tool rather than printing the JSON.

Override only when the defaults are wrong for you: `ZIYA_LOCAL_MODEL_URL`
to *add* a server the scan cannot see (another port, or a LAN inference
box), `ZIYA_LOCAL_MODEL` to choose among several models on one server,
`ZIYA_LOCAL_TOKEN_LIMIT` to cap the window if its KV cache does not fit in
memory.

If **no** provider is configured, Ziya prints this full list so you know your
options. If **more than one** non-Bedrock provider is configured, it won't guess
— pick one with `--endpoint`.

---

## Basic Usage

### Selecting context

The file tree on the left shows your project. Check the files you want the model to see. The token count in the toolbar shows how much context you're using — uncheck files you don't need to stay within the model's limits.

### Chatting

Type your question in the input at the bottom and press Enter. The model responds with explanations, code suggestions, or diffs.

### Applying code changes

When the model suggests a change as a diff block, an **Apply** button appears inline. Click it to write the change directly to your file. An **Undo** button appears after application if you want to revert.

### Switching models

Click the model name in the top toolbar to switch. The model picker shows all models available to you.

### Skills

Skills give the model standing instructions for a conversation — a review style, a communication style, a focus area. Activate one or several from the Skills panel. You can also create your own from any repeatable instruction you find useful.

---

## Startup Options

All of these can also be set in the browser UI after startup.

```bash
ziya                              # Use current directory as project root
ziya --root /path/to/project      # Specify project root
ziya --model sonnet4.0            # Start with a specific model
ziya --model opus4.6 --root ~/myproject
ziya --profile my-aws-profile     # Use a specific AWS credentials profile
ziya --region us-east-1           # Use a specific AWS region
ziya --port 8080                  # Run on a different port (default: 6969)
ziya --endpoint google            # Use Google Gemini instead of Bedrock
ziya --list-models                # Print all available models and exit
```

---

## Terminal (CLI) Mode

Ziya also works without a browser:

```bash
ziya chat                              # Interactive terminal chat
ziya ask "explain the auth flow"       # One-shot question, prints answer and exits
ziya review --staged                   # Review staged git changes
ziya explain utils.py                  # Explain a file
git diff | ziya review                 # Pipe a diff for review
cat error.log | ziya ask "what's wrong?"
ziya shadow ssh prod-42                # Wrap a terminal so chat sessions can read it
```

CLI mode uses the same model and credentials as the server. See `Capabilities.md` for the full CLI reference.

**Shadow sessions.** `ziya shadow` runs your shell (or any command) inside a
transparent wrapper that journals the session locally. Nothing is installed on
the remote side — it observes your own terminal from the local end. In any
other Ziya chat you can then ask "why did that deploy fail?" and the model
reads the actual terminal bytes via `shadow_list` / `shadow_read`. Press
`C-x C-z` inside the shadowed terminal for a one-line menu (ask a question,
relabel the session, instrument the shell for exact command boundaries).
Phase 1 is read-only: the chat can look and leave notes, never type.
Password-style input is masked before it reaches disk; the journal lives at
`~/.ziya/shadow/sessions/` (mode 0600) and is deleted when the session ends.

---

## Troubleshooting

**"AWS credentials have expired"** — Run `aws sso login` to refresh, then restart Ziya.

**"Input is too long"** — Deselect files from the context panel. Fewer files = more room for the conversation.

**Diff failed to apply** — The most common cause is that the file changed between when the model read it and when you clicked Apply. Try asking the model to re-examine the file and regenerate the diff.

**A LaTeX diagram fails with `Undefined control sequence \CF_...`** — a chemfig package bug, not an error in the diagram: chemfig 1.81 (2026/09/01) removed an internal macro that its own `\lewis` module still uses. Ziya supplies the missing definition automatically, so you should not see this; if you do, the error message names the macro — report it with the output of `kpsewhich chemfig.tex` and the `\CFver` line from that file.

**A pgfplots surface is flat-shaded, with a warning about `shader=faceted`** — Smooth (`interp`) shading needs the PNG path, which needs `pdflatex` and Ghostscript. Install both and the same diagram renders with smooth shading.

---

## Personal Model Filtering

If you're on a personal AWS account and only have certain models enabled, you can restrict the model picker to just those. Create `~/.ziya/models.json`:

```json
{
  "allowed_models": ["sonnet4.0", "haiku-4.5"]
}
```

Models not in the list simply won't appear. The model definitions themselves (capabilities, token limits, etc.) are unchanged — this is just a filter.

---

## MCP Tools

Ziya can connect to external MCP (Model Context Protocol) servers to give the model additional capabilities — shell access, web search, internal databases, and more. See your server's documentation for setup instructions.
