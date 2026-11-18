# User Configuration Files

All user-level configuration lives under `~/.ziya/`. These files are optional.

---

## `~/.ziya/mcp_config.json`

Defines MCP servers available to all projects.

Created automatically on first run as an empty starter file, so you can find it
without having to know it exists. Ziya reads only the `mcpServers` key; keys
beginning with `_` are documentation and are ignored — the seeded file carries a
`_README` and an `_example_mcpServers` block you can copy entries out of.

The examples are deliberately kept outside `mcpServers`: an entry in there with
a `command` is treated as a real server and Ziya would try to launch it.

```json
{
  "mcpServers": {
    "my-stdio-server": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"],
      "env": { "SOME_SETTING": "value" },
      "enabled": true
    },
    "my-remote-server": {
      "url": "https://mcp.example.com/sse",
      "auth": { "type": "bearer", "token_env": "MY_MCP_TOKEN" },
      "enabled": true
    }
  }
}
```

- `command` is a **string** and `args` an **array**. If the launcher is installed
  through nvm/asdf/pyenv, give an absolute path — Ziya's `PATH` may not include
  the shims.
- Use `url` instead of `command` for a remote server.
- Prefer `token_env` (names an environment variable) over an inline `token`,
  which sits in plaintext and is not covered by at-rest encryption.
- JSON has no comments. A `//` or `/* */` anywhere makes the file unparseable
  and Ziya reports a config syntax error in the MCP status panel.

### When a server doesn't appear

Open the MCP status panel. Every configured server is listed with the furthest
startup stage it reached, so you can tell whose problem it is:

| Stage | Meaning |
|---|---|
| `config` | A client exists but connection was never attempted — either nothing has started it yet, or its configuration could not be prepared (see below). |
| `preflight` | The command or script does not exist on this machine. No process was started, which is why the Logs tab has nothing to show. |
| `spawn` | The process was launched but died before the MCP handshake. The Logs tab has its stderr and exit code. |
| `handshake` | The server answered `initialize` but stalled while listing its tools, resources, or prompts. |
| `ready` | Connected; tools are listed. |

An entry that is **missing from the list entirely** was rejected while the
config was read — it has no server to report on. The findings panel at the top
of the modal explains why, naming the offending key and its line number.

A `preflight` failure shows a card naming what was searched and how to install
the missing launcher. Because nothing was spawned, an empty Logs tab is the
expected result rather than a sign that log capture is broken.

A `spawn` failure whose launcher could not find the runtime it needs is named
directly: a wrapper script that runs `node`, `python3`, `uvx` and so on exits
immediately with code 127, and the card reports the missing runtime and how to
install it. This is common with launchers that are themselves present — the
command exists, so preflight passes; the interpreter it goes on to run does not.

A malformed entry — a null or non-object `env`, a bad `auth` block — is
contained to itself. It appears at stage `config` with the error that stopped
it, and every other server still starts. Two nulls are tolerated rather than
fatal: a null `env` is ignored with a warning, and a null `args` means no
arguments (any other non-array value is still coerced to a single string
argument, which is rarely what was intended).

A registry install that cannot start is reported as a failure and its config
entry is removed, so a server you were told was installed always has a reason
if it isn't working. Install the dependency named in the error and install it
again; downloaded files are kept, so the retry is fast.

After installing a missing command, or editing this file, click **Reload
Config** to re-read it and reconnect every server — cached per-server details
are discarded so you see the fresh result. Reload Config is in the **MCP
servers** panel, reached from the Bench (ℤ) by the *MCP servers · status, logs,
reload config* button under the board, or from a server card's drawer. The
Bench refreshes itself after the reload, so a newly configured server gets a
card without reopening it.

The panel's heading, **MCP tokens in context**, counts only what the open
conversation sends on each request: tool definitions of servers **On hand**,
tools this conversation has loaded from servers **Within reach**, the
one-line-per-server list of within-reach servers in the system prompt, and
the instruction estimate. Below it, a *Not sent* line gives the weight of
within-reach tools that are not loaded, shelved servers, and disabled tools.
On a model with native tool search (the Anthropic API, or Bedrock through the
mantle gateway, with Claude 4.5 or later; on Bedrock's Invoke API, a model
whose config sets `supports_tool_search`, currently `fable5.2`),
within-reach definitions are sent
deferred: the provider holds them out of the model's context, so they appear
on their own row and are not part of the total. Tools the model has since
found through that search are not known to Ziya and are not counted. With no
conversation open the heading describes the project's placements. Each
server's row uses the same split: an on-hand server shows its tokens, a
within-reach server shows *within reach · N if loaded* (or what it has
loaded, or *sent deferred*), and a shelved server shows *shelved · N not
sent*.

Search order — the **first** file found wins, and this one is last:

1. `./mcp_config.json` (current working directory)
2. `<project root>/mcp_config.json`
3. `~/.ziya/mcp_config.json`

## `<project>/.ziya/hooks.yaml`

Shell commands the project runs at fixed points in a turn (lifecycle hooks). Three events:

| event | fires | placeholders |
|---|---|---|
| `post_apply` | after the diff applier or the `file_write` tool lands a change on disk | `{file}`, `{file_rel}`, `{source}` (`diff` / `file_write`) |
| `post_tool` | after any tool call returns | `{tool}`, `{ok}` |
| `run_finished` | when a Task Card run reaches `done` / `partial` / `failed` / `cancelled` / `held` | `{run_id}`, `{status}`, `{card}`, `{error}` |
| `pre_tool` | before a tool call runs, ahead of the consent gate; can veto it | `{tool}`; the arguments arrive in the stdin JSON (`tools:` filter applies) |
| `session_start` | once per conversation in the project, as the first turn's system prompt is built | `{session}` |

```yaml
hooks:
  post_apply:
    - run: "ruff check {file}"
      match: "*.py"          # fnmatch on the project-relative path
      await: true            # exit status + output are appended to the tool result the model sees
      timeout: 60            # seconds, default 60, max 900
  post_tool:
    - run: "echo {tool} >> .ziya/tool-audit.log"
      tools: [run_shell_command, file_write]
  run_finished:
    - run: 'curl -s -X POST "$ZIYA_NOTIFY_URL" -H content-type:application/json -d @-'
      status: [failed, held]
```

`pre_tool` and `session_start` are always awaited. A `pre_tool` hook that exits 0 lets the call proceed; exit 2 refuses it and the hook's output is the reason the model is given. Any other exit is a hook *error*: the call proceeds under the default `on_error: allow`, or is refused under `on_error: deny`; both are logged. Hooks cannot rewrite arguments. A `session_start` hook's stdout (up to 4,000 characters) is added to the model's Session Context as *Session notes*, marked as data rather than instructions, so `git status --short` or an on-call note reaches the model without being pasted; a failing hook adds nothing. It fires once per conversation (keyed on the conversation's start time), not once per turn.

```yaml
hooks:
  pre_tool:
    - run: 'test "$ZIYA_HOOK_TOOL" != run_shell_command || { echo "shell is off in this repo"; exit 2; }'
  session_start:
    - run: "git status --short | head -20"
```

Each hook gets the event as JSON on stdin and as `ZIYA_HOOK_*` environment variables; `{placeholders}` are shell-quoted before substitution. Hooks are fire-and-forget by default; `await: true` (honoured for `post_apply` and `post_tool`) runs the hook before the tool result is returned and appends `[hook <name>] … exited N` plus its output, which is how "lint after every edit and tell the model" works. `run_finished` is never awaited: it is the notification point for scheduled and unattended runs.

A hook command is checked by the same shell policy as `run_shell_command` — allowlist, interpreters, signed escalations, session grants — so a `hooks.yaml` in a cloned repository has exactly the reach the model's shell tool has in that project. A refused command is logged, not run. The child environment is credential-stripped like every other subprocess (`ZIYA_MCP_ENV_PASSTHROUGH` forces named variables through). `ZIYA_HOOKS=0` disables hooks. Every run, refusal and config error is appended as a JSON line to `<project>/.ziya/hooks.log`.

## `~/.ziya/models.json`

Restricts or extends the models available in the model picker.

## `~/.ziya/tool_enhancements.json`

## `~/.ziya/templates.json`

Project templates and the default-template preference for new projects. See
[Project Templates](ProjectTemplates.md).

## `~/.ziya/pricing.json`

Your own model prices and rate plan for cost accounting — internal transfer
prices, negotiated rates, prices for models newer than the built-in table.
Entries here win over the shipped catalog. See
[Cost Accounting](CostAccounting.md).

## `~/.ziya/usage.db`

Not configuration — the usage ledger (SQLite) that cost accounting reads
and writes. Safe to delete; you lose history, nothing else.

