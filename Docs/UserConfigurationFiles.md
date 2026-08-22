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
| `config` | A client exists but connection was never attempted. |
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

After installing a missing command, click **Reload Config** to re-check —
cached per-server details are discarded so you see the fresh result.

Search order — the **first** file found wins, and this one is last:

1. `./mcp_config.json` (current working directory)
2. `<project root>/mcp_config.json`
3. `~/.ziya/mcp_config.json`

## `~/.ziya/models.json`

Restricts or extends the models available in the model picker.

## `~/.ziya/tool_enhancements.json`

## `~/.ziya/templates.json`

Project templates and the default-template preference for new projects. See
[Project Templates](ProjectTemplates.md).

