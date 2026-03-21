# Ziya MCP Security Controls

This document describes the security controls Ziya implements for MCP (Model Context Protocol) tool interactions. These controls address the threat categories identified by the Agent Tool Checker (ATC) framework.

## Architecture Context

Ziya operates as an **MCP client** that connects to MCP servers, and also hosts **built-in tools** that are exposed directly to the LLM. It does not itself expose an MCP server endpoint to external consumers.

The internal tool surface consists of:

| Server | Transport | Tools |
|---|---|---|
| `shell-server` | stdio (local) | `run_shell_command` |
| `time-server` | stdio (local) | `get_current_time` |
| Built-in tools | in-process | `file_read`, `file_write`, `file_list`, `ast_get_tree`, `ast_search`, `ast_references`, `list_architecture_shape_categories`, `search_architecture_shapes`, `get_architecture_diagram_template`, `nova_web_search`, `get_skill_details` |

External MCP servers (e.g., `builder-mcp`) are connected as configured by the user or enterprise plugin.

## Threat Mitigations

### 1. Tool Poisoning

**Module:** `app/mcp/tool_guard.py` — `scan_tool_description()`

All tool descriptions are scanned for prompt-injection patterns before being exposed to the LLM. The scanner checks for:

- "Ignore previous instructions" variants
- System prompt override attempts (`system:`, `<system>`)
- Permission/safety bypass instructions
- Concealment directives ("do not mention", "pretend to be")
- Hidden HTML comments
- Excessively long descriptions (>4000 chars) that may hide instructions

**Module:** `app/mcp/response_validator.py`

Tool responses are validated for hidden character smuggling (Unicode zero-width characters, orphaned surrogates, bidi overrides, control characters) per SDO-183 guidelines.

**ANSI Escape Handling:** ANSI escape sequences (color codes like `\x1b[91m`) from shell command output are always preserved through the sanitization pipeline. In CLI mode the terminal renders them natively as colored text. In server/web mode the frontend converts them to styled HTML `<span>` tags via the `ansiToHtml()` utility (`frontend/src/utils/ansiToHtml.ts`), supporting standard 4-bit, 256-color, and RGB truecolor SGR codes, as well as bold, italic, underline, and dim attributes. Non-SGR escape sequences (cursor movement, etc.) are stripped by the frontend converter. All other control character, hidden character, and bidi override stripping remains active.

### 2. Tool Overreach

**Module:** `app/mcp_servers/shell_server.py` — command allowlist

The shell server maintains an allowlist of permitted commands (117 patterns). Commands not on the list are rejected before execution.

**Module:** `app/mcp_servers/write_policy.py` — `ShellWriteChecker`

A second security layer after the allowlist catches output redirection, in-place edits (`sed -i`), destructive commands (`rm`, `mv` on project files), and interpreter escapes. Write operations are restricted to approved paths (`.ziya/`, `/tmp/`, configured patterns).

**Module:** `app/mcp/client.py` — policy block detection

When the shell server rejects a command (BLOCKED or WRITE BLOCKED), the MCP client recognizes the rejection as a permanent policy violation and returns immediately without retrying. The error is tagged with `policy_block: True` so the streaming executor can provide clear feedback to the model indicating the command is permanently blocked and should not be reattempted.

**Module:** `app/mcp/tools/fileio.py` — path restrictions

The `file_write` tool enforces write policies through `WritePolicyManager`, restricting writes to project-configured approved paths.

### 3. Cross-Origin Escalation (Shadowing)

**Module:** `app/mcp/tool_guard.py` — `detect_shadowing()`

When external MCP servers connect, their tool names are checked against built-in tool names. If an external server tries to register a tool with the same name as a built-in (e.g., `file_read`), the collision is detected and the built-in version takes precedence.

### 4. Rug-Pull Detection

**Module:** `app/mcp/tool_guard.py` — `fingerprint_tools()`, `check_fingerprint_change()`

At connection time, each MCP server's tool definitions (names, descriptions, schemas) are fingerprinted with SHA-256. On reconnection, the fingerprint is compared to the baseline. Any change triggers a warning, catching post-install tool definition mutations.

### 5. Result Integrity

**Module:** `app/mcp/signing.py`

All MCP tool results are signed with HMAC-SHA256 using a per-session secret generated at startup. This prevents model hallucination of tool results — unsigned or incorrectly signed results are rejected.

### 6. Execution Tracking

**Module:** `app/mcp/security.py`

Each tool execution receives a cryptographic token (`ToolExecutionToken`) with a SHA-256 signature binding the tool name, arguments, conversation ID, and timestamp. Executions are tracked in a registry for verification.

### 7. Permission Management

**Module:** `app/mcp/permissions.py`

Per-server and per-tool permissions can be set to `enabled` or `disabled`, giving users control over which MCP servers and tools are active.

## ATC Self-Scan

The test suite `tests/test_atc_self_scan.py` performs an equivalent scan to the ATC CLI against all internal tools:

```bash
python -m pytest tests/test_atc_self_scan.py -v -s 2>&1 | tee atc_scan_results.txt
```

This produces a report covering all four ATC threat categories and can be submitted as a compliance artifact.

## Test Coverage

| Test File | What It Tests |
|---|---|
| `tests/test_tool_guard.py` | Injection scanning, shadowing detection, fingerprinting |
| `tests/test_atc_self_scan.py` | Full ATC-equivalent scan of all internal tools |
| `tests/test_mcp_security.py` | Execution tokens, registry, signature verification |
| `tests/test_signing.py` | HMAC signing and verification of tool results |
| `tests/test_response_validator.py` | Hidden character stripping, response schema validation |
| `tests/test_mcp_client_retry.py` | Retry logic: policy blocks not retried, transient errors retried |
| `tests/test_mcp_client_timeout.py` | Timeout alignment: tool-requested timeouts honoured by MCP client |

## Shell Command Timeout Chain

Three layers of timeouts protect shell command execution. They must be aligned so that inner layers fire before outer layers, producing clean error messages:

| Layer | Default | Env Var | Description |
|---|---|---|---|
| Shell server (`subprocess.run`) | 30s | `COMMAND_TIMEOUT` | Kills the actual shell process. Model can request up to `MAX_COMMAND_TIMEOUT` (300s) via the `timeout` tool parameter. |
| MCP client (`readline`) | 30s | — | Waits for the shell server's JSON-RPC response. Automatically extended to `tool_timeout + 10s` when the tool call includes a `timeout` argument. |
| Tool executor (`asyncio.wait_for`) | 300s | `TOOL_EXEC_TIMEOUT` | Outermost guard wrapping the entire MCP call from the streaming executor. |

The 10-second buffer between the shell server timeout and the MCP client readline timeout ensures that `subprocess.TimeoutExpired` fires first, giving a descriptive "Command timed out after N seconds" error rather than a generic "Timeout waiting for response from MCP server".
