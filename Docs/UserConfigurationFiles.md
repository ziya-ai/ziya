# User Configuration Files

All user-level configuration lives under `~/.ziya/`. These files are optional — Ziya works without them.

---

## `~/.ziya/mcp_config.json`

Defines MCP servers available to all projects. Merged with any project-level `mcp_config.json` (project-level entries take precedence for the same server name).

```json
{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"],
    "enabled": true
  },
  "my_api": {
    "command": "python",
    "args": ["-m", "my_mcp_server"],
    "env": { "API_KEY": "..." },
    "tool_enhancements": {
      "search": {
        "description_suffix": "\nThe query parameter must be a non-empty string."
      }
    }
  }
}
```

### Server config keys

**stdio servers (local subprocess):**

| Key | Type | Description |
|---|---|---|
| `command` | string | Executable to run |
| `args` | string[] | Command-line arguments |
| `env` | object | Additional environment variables |
| `enabled` | bool | Set `false` to disable without removing (default: `true`) |
| `disabled` | bool | Alternate form (Q Developer / Claude Code compat); normalized to `enabled` internally |
| `workspace_scoped` | bool | If `true`, a separate server instance is spawned per project directory |
| `tool_enhancements` | object | Per-tool description hints (see below) |

**Remote HTTPS servers:**

| Key | Type | Description |
|---|---|---|
| `url` | string | HTTPS endpoint URL (triggers remote mode instead of subprocess) |
| `transport` | string | `"streamable-http"` (default) or `"sse"` (legacy Server-Sent Events) |
| `auth` | object | Authentication config (see below) |
| `headers` | object | Additional HTTP headers (e.g., API keys) |
| `enabled` | bool | Set `false` to disable without removing (default: `true`) |
| `tool_enhancements` | object | Per-tool description hints (same as stdio servers) |

**Authentication (`auth` block):**

| Key | Type | Description |
|---|---|---|
| `type` | string | Currently only `"bearer"` is supported |
| `token` | string | Inline bearer token value |
| `token_env` | string | Name of environment variable containing the bearer token (preferred over inline) |

### `tool_enhancements`

A dict keyed by tool name (as reported by the MCP server, without the `mcp_` prefix). Each value is an object with:

| Key | Type | Description |
|---|---|---|
| `description_suffix` | string | Text appended to the tool's description before it reaches the model |

This is the recommended way to fix model mistakes with specific MCP tools — add a rule or clarification directly in your config without modifying the server.

---

## `~/.ziya/models.json`

Restricts or extends the models available in the model picker.

**Filter to specific models:**

```json
{
  "allowed_models": ["sonnet4.0", "haiku-4.5", "nova-lite"]
}
```

**Add a custom inference profile:**

```json
{
  "allowed_models": ["sonnet4.0", "my-throughput"],
  "bedrock": {
    "my-throughput": {
      "model_id": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/my-profile",
      "family": "claude",
      "max_output_tokens": 64000,
      "supports_vision": true,
      "supports_context_caching": true
    }
  }
}
```

Both sections are optional. Restart Ziya after changes.

---

## `~/.ziya/tool_enhancements.json`

Global tool description overrides that apply regardless of which MCP server provides the tool. These take highest priority — they override both enterprise plugin enhancements and
 per-server config enhancements.

```json
{
  "enhancements": {
    "WorkspaceSearch": {
      "description_suffix": "\n<Rule>Always use regex type for wildcard searches.</Rule>"
    },
    "run_shell_command": {
      "description_suffix": "\nPrefer piped_commands for multi-step operations."
    }
  }
}
```

The tool name key should match the tool's original name as reported by the MCP server (without the `mcp_` prefix Ziya adds).

---

## Enhancement Priority

Tool description enhancements are merged from three sources. For the same tool, later sources override earlier:

| Priority | Source | Scope |
|---|---|---|
| 1 (lowest) | Enterprise plugin (`ToolEnhancementProvider`) | Organization-wide |
| 2 | MCP server config (`tool_enhancements` in `mcp_config.json`) | Per-server |
| 3 (highest) | `~/.ziya/tool_enhancements.json` | Personal |

---

## `~/.ziya/projects/`

Per-project settings, contexts, and skills are stored here automatically. You don't need to edit these directly — they're managed through the UI.

---

## Configuration File Locations Summary

| File | Purpose |
|---|---|
| `~/.ziya/mcp_config.json` | Global MCP server definitions |
| `~/.ziya/models.json` | Model allowlist and custom profiles |
| `~/.ziya/tool_enhancements.json` | Global tool description overrides |
| `~/.ziya/projects/` | Per-project state (managed by UI) |
| `./mcp_config.json` | Project-level MCP server definitions |
