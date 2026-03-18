# Security, Privacy & Data Handling: Diya vs Ziya Comparison

## Executive Summary

Diya and Ziya have fundamentally different deployment architectures, which
drives most security design differences.  Diya is a multi-tenant cloud
service (Lambda + API Gateway) where data privacy between users is the
primary concern.  Ziya is a single-user localhost application where the
developer IS the admin, making tool execution security the primary concern
rather than data privacy from administrators.

---

## 1. Security Comparison Matrix

| Security Dimension | Diya | Ziya | Status |
|-|-|-|-|
| **Deployment Model** | Multi-tenant cloud (Lambda, API Gateway) | Single-user localhost (Python FastAPI) | Architectural difference, not a gap |
| **ASR Certification** | Red (Highly Confidential / SDO) | None documented | **HIGH GAP** — needed for enterprise adoption |
| **Data Classification** | Up to Highly Confidential (Level 3) | Not formally classified | **HIGH GAP** |
| **Conversation Storage** | Client-side only (browser localStorage) | Server-side filesystem (local machine) | Different by design — see §4 |
| **Encryption at Rest** | Session data encrypted; key in browser session only | No encryption of stored conversations | **MEDIUM GAP** — less critical for localhost |
| **Authentication** | Midway via API Gateway MidwayAuthorizer | Midway-aware (checks ~/.midway/cookie, STS) | LOW GAP — Ziya validates credentials, just differently |
| **Server-Side Data Persistence** | None — stateless Lambda | Conversation files on local filesystem | Design tradeoff, not a bug |
| **Session Data TTL** | Temporary; encrypted; auto-expires | 90-day retention policy (configurable) | OK — AmazonDataRetentionProvider |
| **Input Sanitization** | Minimal — prompt passes through to Bedrock | `escapeHtml()` applied to all server-supplied error strings in HTML interpolation | ~~MEDIUM GAP~~ **CLOSED** (v0.6.0) |
| **Tool Execution Surface** | None — pure Bedrock API wrapper | Full MCP tool execution including shell commands | HIGH RISK AREA — Ziya's primary attack surface |
| **Tool Execution Audit Logging** | N/A — no tools | Append-only JSONL audit log under `~/.ziya/audit/` with tool name, args, result, verification status, timing | ~~HIGH GAP~~ **CLOSED** (v0.6.0) |
| **WebSocket Security** | N/A — stateless REST API | Protocol auto-detects (`ws://` for http, `wss://` for https); CSP allows both | LOW — localhost traffic only |
| **CORS Configuration** | Explicit allowlist in CDK (apiStack.ts) | Localhost-bound, no CORS needed | N/A — different deployment models |
| **Admin Data Access** | Cannot read user data (encryption key in browser only) | User IS the admin (single-user localhost) | N/A — different threat models |
| **XSS Prevention** | N/A — no server-rendered HTML | `escapeHtml()` utility; CSP headers; X-XSS-Protection | ~~MEDIUM GAP~~ **CLOSED** (v0.6.0) |
| **Content Security Policy** | N/A (SPA served from S3/CloudFront) | `SecurityHeadersMiddleware` on all responses: CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy | ~~MEDIUM GAP~~ **CLOSED** (v0.6.0) |
| **Tool Result Verification** | N/A | Cryptographic HMAC signing of tool results; hallucinated results blocked with corrective feedback | COVERED |
| **MCP Tool Security (ATC)** | N/A | Tool poisoning scanning, shadowing prevention, rug-pull fingerprinting, OAuth bearer auth for remote servers | COVERED (v0.6.0) — see §10 |

---

## 2. Client-Side Encryption Analysis

### Diya's Approach

- Chat history stored exclusively in browser localStorage — server never persists conversations
- Streaming session data temporarily stored, encrypted at rest with key in browser session only
- When session ends, key is gone — even Diya admins cannot decrypt

### Ziya's Approach

- Conversations stored server-side on local filesystem (plain JSON)
- 90-day retention policy via `AmazonDataRetentionProvider`
- The user's local machine IS the secure boundary

### Should Ziya Adopt Diya's Model?

**No, but adopt elements selectively.** Diya's client-side-only model makes sense for multi-tenant cloud. For Ziya:

- The user's local machine is already a trust boundary
- Server-side storage enables MCP tool chains and multi-turn context
- **Optional encryption-at-rest** remains a P1 for compliance checkbox purposes

---

## 3. Data Classification & ASR Compliance

### Ziya's Position

Ziya's architecture can support classification but needs:

- **Formal ASR review** — not yet started
- **Encryption-at-rest** for conversation files (P1)
- **Documented data flow** — where input goes, what persists, what's ephemeral
- **MCP tool execution audit logging** — ✅ implemented (v0.6.0)

---

## 4. Server-Side Storage Tradeoffs

| Aspect | Diya (No Server Storage) | Ziya (Server Storage) |
|-|-|-|
| Privacy from admins | ✅ Strong | N/A — single user |
| Multi-turn context | ❌ Limited | ✅ Full |
| MCP tool chains | ❌ Not supported | ✅ Critical enabler |
| Data loss risk | ⚠️ Browser clear = data gone | ✅ Persisted |
| Compliance | ✅ Nothing to audit on server | ⚠️ Must protect stored files |
| Offline access | ❌ No | ✅ Yes |

**Verdict:** Ziya's server-side storage is correct for its use case.

---

## 5. Midway Authentication Flow Comparison

### Diya: API Gateway → MidwayAuthorizer → Lambda (pre-authenticated)

### Ziya: `mwinit` → `~/.midway/cookie` → `AmazonAuthProvider.check_credentials()` → STS validation

**Hardening patterns borrowed:**
- Cookie freshness enforcement (Ziya warns at 20h; recommend hard gate)
- Auth error propagation (chatApi.ts detects auth errors inline)
- No credential exposure in logs (audit log strips internal `_` prefixed args)

---

## 6. Session Data Lifecycle

| Aspect | Diya | Ziya |
|-|-|-|
| Streaming transport | SSE via API Gateway | SSE via fetch + ReadableStream |
| Real-time feedback | N/A | WebSocket per conversation (`ws://` or `wss://`) |
| Session state | None (stateless Lambda) | In-memory `streamedContentMap` (React state) |
| Encryption in transit | HTTPS (API Gateway) | Localhost binding (http); `wss://` supported for https |
| Session timeout | Implicit (Lambda timeout) | No explicit max — **P2 recommendation** |

---

## 7. Input Sanitization — Post-Fix Status

### Fixed in v0.6.0

| Vector | Before | After |
|-|-|-|
| `showError()` HTML interpolation | Raw `errorDetail` in template literal | `escapeHtml(errorDetail)` applied before interpolation |
| Throttling notification HTML | Raw `retry_message` embedded | `escapeHtml()` applied |
| CSP headers | Not registered | `SecurityHeadersMiddleware` active on all responses |
| CSP connect-src | Missing `wss://` | Includes `ws://localhost:*` and `wss://localhost:*` |

### Remaining

- Prompt injection detection for MCP tool results — P3 (large effort)
- HTML in Ant Design `message.error()` popup — safe (React escapes by default)

---

## 8. Tool Execution Audit Logging — Implementation Details

**Added in v0.6.0.** Each MCP tool invocation now produces a structured
JSONL entry in `~/.ziya/audit/tool_audit_YYYY-MM-DD.jsonl`:

```json
{
  "ts": "2025-01-15T14:32:01.123456+00:00",
  "tool": "run_shell_command",
  "args": {"command": "ls -la src/"},
  "status": "ok",
  "conv": "abc123def456",
  "verified": true,
  "error": "",
  "ms": 142.3
}
```

Properties:
- **Append-only** — entries are never modified or deleted
- **Daily rotation** — one file per day for easy archival
- **Truncation** — argument values capped at 500 chars, errors at 200
- **Non-blocking** — failures in audit logging never break the main flow
- **Disableable** — `ZIYA_DISABLE_AUDIT_LOG=1` for local development

---

## 9. Prioritized Hardening Recommendations (Updated)

| Priority | Recommendation | Effort | Status |
|-|-|-|-|
| ~~P0~~ | ~~Fix XSS in showError() and throttling HTML~~ | Small | ✅ **DONE** (v0.6.0) |
| ~~P0~~ | ~~Add proactive credential validation before starting streams~~ | Small | ⚠️ Warn-only; hard gate deferred |
| ~~P1~~ | ~~Add audit logging for MCP tool execution~~ | Medium | ✅ **DONE** (v0.6.0) |
| ~~P2~~ | ~~Add Content Security Policy headers~~ | Small | ✅ **DONE** (v0.6.0) |
| P1 | Start ASR certification process | Medium | NOT STARTED |
| P1 | Add optional encryption-at-rest for conversation files | Medium | NOT STARTED |
| ~~P1~~ | ~~Add MCP tool security mitigations (ATC)~~ | Medium | ✅ **DONE** (v0.6.0) — see §10 |
| P2 | Add streaming session timeout | Small | NOT STARTED |
| P2 | Make credential check a hard gate (not warn-only) | Small | NOT STARTED |
| ~~P3~~ | ~~Investigate prompt injection detection for tool results~~ | Large | ✅ **DONE** (v0.6.0) — tool_guard.py scans descriptions; HMAC signing prevents hallucinated results |
| P3 | Add optional client-side encryption mode | Large | NOT STARTED |

---

## 10. MCP Tool Security — Agent Tool Checker (ATC) Mitigations

Ziya is not a standalone MCP server that external parties install. It is an
AI coding assistant that acts as an **MCP client** hosting 2 built-in MCP
servers (time, shell) and optionally connecting to user-configured external
servers. The ATC threat categories are addressed as follows.

### Threat Assessment

| ATC Threat | Risk Level | Mitigation |
|---|---|---|
| **Tool Poisoning** | Low (built-in) / Medium (external) | `tool_guard.py` scans external tool descriptions for 13 prompt-injection patterns at connect time |
| **Tool Overreach** | Low | Shell server uses command allowlist, write policy, always-blocked list, in-place edit detection, command chain validation |
| **Cross-Origin Escalation (Shadowing)** | Medium | `detect_shadowing()` prevents external tools from overriding built-in tool names; built-ins always take precedence |
| **Insecure Action Sequences** | Low | Shell validates every segment of chained commands (&&, \|\|, ;, \|) and command substitutions |
| **Rug-Pull** | Medium | `fingerprint_tools()` hashes tool definitions (SHA-256) at connect time; changes on reconnect trigger security warnings |

### Implementation Details

**Tool Description Poisoning Scanner** (`app/mcp/tool_guard.py`):
- 13 regex patterns for prompt injection indicators (instruction override, system tag injection, bypass attempts, etc.)
- Flags excessively long descriptions (>4000 chars) that may hide instructions
- Runs automatically for all non-builtin servers at connect time
- Warnings logged at WARN level for operator review

**Tool Shadowing Prevention** (`app/mcp/manager.py:get_all_tools()`):
- Built-in tool names are collected first during tool enumeration
- External tools that collide with built-in names are skipped with a warning
- Built-in implementations always take precedence — no override path

**Rug-Pull Fingerprinting** (`app/mcp/tool_guard.py`):
- On each server connection, tool definitions are canonicalized and hashed (SHA-256)
- On reconnection, the new fingerprint is compared against the stored baseline
- Changes trigger a security warning (possible tool definition mutation after install)
- Fingerprints stored per-server in the MCPManager instance

**OAuth / Bearer Token Auth** (`app/mcp/client.py`, `app/mcp/manager.py`):
- Remote MCP servers (SSE / StreamableHTTP) support bearer token authentication
- Config: `"auth": {"type": "bearer", "token": "..."}` or `"auth": {"type": "bearer", "token_env": "VAR_NAME"}`
- Tokens injected as `Authorization: Bearer <token>` header
- Custom headers also supported via `"headers"` config block

### Existing Security Controls (pre-ATC)

| Control | Module | Description |
|---|---|---|
| HMAC-SHA256 result signing | `app/mcp/signing.py` | All tool results cryptographically signed; hallucinated results blocked |
| Per-tool permissions | `app/mcp/permissions.py` | Individual tools can be enabled/disabled via UI |
| Write policy enforcement | `app/mcp_servers/write_policy.py` | Blocks writes to non-approved filesystem paths |
| Command allowlist | `app/mcp_servers/shell_server.py` | Shell only runs approved commands; always-blocked list |
| Loop detection | `app/mcp/manager.py` | Conversation-aware tracking prevents repetitive tool abuse |
| Parameter normalization | `app/mcp/client.py` | Type coercion and schema validation prevent injection via malformed parameters |
| Rate limiting | `app/mcp/client.py` | Per-tool rate limits prevent rapid-fire abuse |
| Audit logging | `~/.ziya/audit/` | Append-only JSONL log of all tool invocations |

## 10. Key Architectural Insight

Diya's security model is optimized for a different threat model than Ziya's.

- **Diya's threat:** Protecting user A's data from user B (and from operators)
- **Ziya's threat:** Protecting the developer's machine from unintended tool execution side effects

Copying Diya's client-side-only storage would weaken Ziya by eliminating
the server-side context that enables MCP tool chains, multi-turn debugging,
and conversation persistence.

The correct approach is to harden Ziya for **its own** threat model:
sanitize inputs, audit tool execution, encrypt at rest for compliance,
and pursue ASR certification based on the localhost security boundary.
