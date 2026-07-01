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

**Module:** `app/mcp_servers/shell_server.py` — command allowlist and secure execution

The shell server maintains an allowlist of permitted commands (117 patterns). Commands not on the list are rejected before execution.

All subprocess calls use `shell=False`. The server parses shell features (pipes, `&&`/`||`/`;` chaining, environment variable and tilde expansion, glob patterns, and command substitution) in Python and orchestrates individual `subprocess.run(args_list, shell=False)` calls. This eliminates the risk of shell environment manipulation (PATH hijacking, LD_PRELOAD injection, malicious shell config files) and command injection.

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

### 7. Tool Execution Timeout

**Module:** `app/mcp/manager.py` — `_call_tool_with_timeout()`

Every MCP tool invocation is wrapped in `asyncio.wait_for()` with a configurable deadline. If an MCP server hangs (network issue, infinite loop, unresponsive process), the call is cancelled and a structured error is returned to the model instead of blocking the session indefinitely.

| Setting | Default | Description |
|---|---|---|
| `ZIYA_TOOL_TIMEOUT` | `120` (seconds) | Maximum time a single tool call may run before being cancelled |

The timeout applies uniformly to all tool execution paths: direct server calls, workspace-scoped instances, and the auto-routing fallback path. Dynamic tools (in-process) are not subject to this timeout as they run in the same event loop.

### 8. Permission Management

**Module:** `app/mcp/permissions.py`

Per-server and per-tool permissions can be set to `enabled` or `disabled`, giving users control over which MCP servers and tools are active.

### 9. Shell Privilege Escalation Gate

**Modules:** `app/mcp/shell_server.py` (`ShellServer.__init__`, `is_env_scope_authorized`, `strip_escalations`), `app/utils/ziya_approve.py` (`ziya-approve` CLI), `app/routes/mcp_routes.py` (`/shell-config`).

The shell server runs at a fixed **default floor** of privilege (a baseline set of
allowed commands, interpreters, and write paths). Any configuration that widens
privilege beyond that floor — additional `ALLOW_COMMANDS`, extra
`ALLOWED_INTERPRETERS`, broader write paths — is an **escalation** and is honored
only when backed by a valid root-minted Ed25519 signature (`ZIYA_SCOPE_SIG`) in
`~/.ziya/mcp_config.json`, produced out-of-process by `sudo ziya-approve`.

**The gate sits at subprocess spawn, not at the HTTP boundary.** When the shell
subprocess starts, `ShellServer.__init__` calls `is_env_scope_authorized(os.environ)`:

- If the signature verifies against the exact delta-from-floor of the spawn env,
  the escalation is honored.
- Otherwise `strip_escalations` clamps the env back to the floor — fail-closed.

Every privileged read afterward (command allow-list, interpreters, write policy,
git operations) consults the clamped scope, never raw `os.environ`.

**Why this is safe even though the local `/shell-config` endpoints are
unauthenticated.** The endpoints are untrusted by design. The threat model
assumes a local caller can POST arbitrary config (or simply edit
`mcp_config.json` directly) — neither yields privilege. An HTTP caller can
*request* an escalation, but:

1. The HTTP path cannot manufacture a `ZIYA_SCOPE_SIG`; only `sudo ziya-approve`
   (gated by the root key, outside the HTTP boundary) can.
2. Applying a config always **respawns** the subprocess via `restart_server`;
   it never mutates a running process's privileges in place. The respawn
   unconditionally re-runs the spawn gate against whatever env it is handed.
3. An unsigned escalation therefore spawns, fails verification, and is clamped
   to the floor — it never becomes executable.

A signing endpoint callable from the web UX was explicitly rejected: because the
local endpoints are unauthenticated, such an endpoint would be a **signing
oracle**, letting any local process or CSRF'd browser tab mint signatures and
collapsing the root-key gate. Authority must originate outside the HTTP boundary.

**Two acceptance paths: durable and ephemeral.** "Ephemeral" (how long a grant
lives) and "unsigned" (whether an escalation is honored) are orthogonal. Every
honored escalation — durable or ephemeral — must trace to a trust anchor; the
only thing that varies is the *lifetime of the signed artifact*. The spawn gate
honors a delta if it is backed by **either**:

1. **Durable signature** (`ZIYA_SCOPE_SIG` in `~/.ziya/mcp_config.json`, minted
   by `sudo ziya-approve`). Persists across restarts until the config changes.
   Surfaced in the UI as **Save** + `sudo ziya-approve` + restart.

2. **Session grant** (`ZIYA_SESSION_GRANT`, minted by `sudo ziya-approve
   --session`, bound to a per-server-start nonce `ZIYA_SESSION_NONCE`). The
   ephemeral *runtime-consent* tier: alive only for the current server start,
   automatically void on the next cold start (the nonce rotates), and **never
   written to the durable config**. Surfaced as **Apply (this session)** +
   `sudo ziya-approve --session` + **Apply now**.

There is still no ephemeral-*unsigned* escalation: the session grant is itself a
root-signed artifact, so the gate's invariant ("honored ⇒ signed by a trust
anchor") is preserved. The session grant carries its own escalation *delta*; the
manager injects those values into the spawn env and the subprocess re-derives the
delta and re-verifies the signature over it. If the (untrusted) manager injected
anything beyond what was signed, the re-derived delta would be a superset, the
signature would not match, and the env clamps to the floor — so injection cannot
widen scope. This is why the ephemeral path needs no durable config write at all.

**Ephemerality without an on-disk lifecycle.** The server-start nonce is minted
in `MCPManager.__init__` and held in the long-lived manager process. A grant
bound to nonce *N* survives a *shell-subprocess* restart (how the grant is
applied) but is dead the instant a *server* restart mints nonce *N+1*. "This
session only, gone on cold start" is therefore a nonce comparison, not a
session-file with expiry/teardown to get wrong. Transient staging files
(`pending_session_shell.json`, `session_grant_shell.json`) are cleared on
successful apply, on explicit discard, and on a superseding durable Save.

**The provider seam (pluggable trust anchors).** A session grant is honored only
if signed by a key the subprocess trusts. The grant record carries a `provider`
field; verification dispatches to that provider's anchor. This makes the consent
*mechanism* pluggable without changing the gate:

| Provider | Trust anchor | Friction | Availability |
|---|---|---|---|
| `os-credential` (default) | root Ed25519 key (sudo) | one credential prompt | everywhere, incl. pip-installed / headless / remote |
| `biometric` (future) | Secure-Enclave key | Touch ID tap | requires a signed `.app` bundle (not pip) |
| `remote-reauth` (future) | identity-provider key | SSO / re-auth | cloud dev desktops |
| `bypass` | none (honor record unconditionally) | zero | explicit, owned risk |

The default provider is "lighter **artifact**, same **proof**": the grant
auto-expires and never touches the durable config, but it is still gated by the
same root credential prompt. Cheaper *proof* requires a provider that installs a
different anchor. **Which providers the subprocess accepts is itself durable
root-signed config** — it is the subprocess's trust-anchor list. Flipping to the
`bypass` provider is adding a "trust everyone" anchor, so it must require the root
key; otherwise an unauthenticated local caller could simply select `bypass` and
re-open the escalation hole. Provider *selection* lives at the durable-trust tier;
only the per-session *grant* flows through the lightweight path.

**Task-card scope approvals are a separate, durable-only path.** Signing a task
card's scope (`ziya-approve --task/--block`, `--cli-task`) uses a different store
(`~/.ziya/scope_approvals/`, keyed by `task_id` + `scope_hash`) and a different
runtime gate (`scope_approvals.is_scope_authorized`, enforced in the task
executor). It shares only the root key and canonical encoding with the shell
gate. Task-card approvals are deliberately durable-only — they bind to a stable
`scope_hash`, not a server-start nonce — and have no ephemeral equivalent.

The signature-status banner is computed strictly from the on-disk config (the
bytes `ziya-approve` signs and the subprocess re-verifies), never from a merged
in-memory view, so the UI can never advertise an escalation the signer cannot see
or the subprocess would clamp.

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
| `tests/test_mcp_tool_timeout.py` | Manager-level tool timeout: default, env override, tool-param extension, error format |

## Tool Execution Timeout Chain

Four layers of timeouts protect tool execution. They are ordered so that inner layers fire before outer layers, producing the most descriptive error possible:

| Layer | Default | Env Var | Description |
|---|---|---|---|
| Shell server (`subprocess.run`) | 30s | `COMMAND_TIMEOUT` | Kills the actual shell process. Model can request up to `MAX_COMMAND_TIMEOUT` (300s) via the `timeout` tool parameter. |
| MCP client (`readline`) | 30s | — | Waits for the shell server's JSON-RPC response. Automatically extended to `tool_timeout + 10s` when the tool call includes a `timeout` argument. |
| MCP manager (`asyncio.wait_for`) | 120s | `ZIYA_TOOL_TIMEOUT` | Guards every `call_tool()` invocation — covers CLI, server, and streaming paths. When the tool's arguments include a `timeout` key, the effective timeout is `max(ZIYA_TOOL_TIMEOUT, tool_timeout + 15s)` so inner layers fire first. |
| Streaming tool executor (`asyncio.wait_for`) | 300s | `TOOL_EXEC_TIMEOUT` | Outermost guard wrapping the entire tool execution cycle in the streaming path (includes argument normalization, signing, etc.). |

The buffer between each layer ensures that the innermost timeout fires first. For example, a shell command with `timeout=200`:
- Shell server kills the process at 200s
- MCP client readline waits 210s (200+10)
- MCP manager waits 215s (200+15)
- Streaming executor waits 300s

This produces a clean "Command timed out after 200 seconds" error from the shell server rather than a generic timeout from an outer layer.
