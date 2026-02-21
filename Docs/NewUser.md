# Getting Started with Ziya

## Prerequisites

- Python 3.11+
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

---

## Quick Start

Navigate to your project directory and run:

```bash
cd /path/to/your/project
ziya
```

Open `http://localhost:6969` in your browser.

If your AWS credentials aren't configured yet:

```bash
aws configure
```

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
ziya chat                         # Interactive terminal chat
ziya ask "what does this do?"     # One-shot question, prints answer and exits
```

CLI mode uses the same model and credentials as the server.

---

## Troubleshooting

**"AWS credentials have expired"** — Run `aws sso login` to refresh, then restart Ziya.

**"Input is too long"** — Deselect files from the context panel. Fewer files = more room for the conversation.

**Diff failed to apply** — The most common cause is that the file changed between when the model read it and when you clicked Apply. Try asking the model to re-examine the file and regenerate the diff.

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

---

## Internal (Amazon) Users

Use the `AtoZiya` internal package. Run `mwinit -o` before starting Ziya to refresh credentials. Everything else is automatic.
