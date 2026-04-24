# Ziya Threat Model (Public Summary)

This document is the public threat-model summary for Ziya. It describes the deployment model Ziya is designed for, the assets it handles, the actors it interacts with, the trust boundaries it crosses, and how each boundary is controlled. It is the document to cite when a security reviewer asks *"is there a threat model I can reference? Any architecture diagram covering data flow and trust boundaries?"*

Enterprise deployments may layer an additional, more detailed threat model on top of this one (see `Enterprise.md`). The controls described here apply to every Ziya deployment.

---

## 1. Deployment model (the facts that drive everything)

Three architectural facts drive the entire threat model:

1. **Single user.** Ziya runs as one Python process on the developer's own machine. The developer is the only human principal.
2. **Loopback only.** The HTTP/SSE/WebSocket server binds to `127.0.0.1`. There is no network-reachable API surface.
3. **User's own data.** Every file Ziya reads is on the developer's workstation and was explicitly selected by that developer. There is no shared corpus.

The non-trivial complexity is the **tool execution subsystem**: the LLM can invoke tools (shell, file I/O, external MCP servers, web search). Each invocation is a trust-boundary crossing the executor is designed to contain.

---

## 2. Assets

| Asset | Sensitivity | Location |
|---|---|---|
| Source code / files selected into context | Up to Highly Confidential | Workstation filesystem |
| Cloud LLM API credentials | Secret | Environment, `~/.aws/`, OS keychain |
| External MCP server credentials | Secret | `mcp_config.json`, environment |
| Conversation history | Confidential | Browser IndexedDB |
| Tool execution audit log | Internal | `~/.ziya/audit/tool_audit_YYYY-MM-DD.jsonl` (mode 0600) |
| HMAC session secret (tool signing) | Secret | Process memory only, regenerated per run |
| Structured memory | Confidential | `~/.ziya/memory/` |

Secrets never enter the LLM prompt. File content is opt-in, not auto-indexed.

---

## 3. Actors

| Principal | Trust | How authenticated |
|---|---|---|
| Developer (local user) | Fully trusted | OS login; POSIX permissions |
| Browser (loopback origin) | Trusted-by-origin | Loopback + same-origin |
| **LLM** (Bedrock/Anthropic/OpenAI/Gemini) | **Semi-trusted** — its *outputs* are treated as untrusted input to the executor | User's own API credential authenticates Ziya → provider |
| External MCP servers | Semi-trusted per-server | `mcp_config.json`; OAuth bearer for remote |
| Enterprise plugins | Trusted when loaded | Opt-in via `ZIYA_LOAD_INTERNAL_PLUGINS=1` |
| Anyone else on the network | Untrusted and unreachable | N/A |

The **LLM is the central novel actor**. It is neither a human user nor a passive library: it is an untrusted output generator the executor is explicitly built to contain.

---

## 4. Data flow diagram

```drawio
<mxfile host="app.diagrams.net">
  <diagram name="Ziya Threat Model DFD" id="ziya-dfd-public-v1">
    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1400" pageHeight="900" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="tb1" value="TB1 — Developer Workstation (OS trust boundary)" style="rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;strokeColor=#999999;verticalAlign=top;fontStyle=1;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="1320" height="780" as="geometry"/>
        </mxCell>
        <mxCell id="tb2" value="TB2 — Ziya Process (python, 127.0.0.1)" style="rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;strokeColor=#4A90E2;verticalAlign=top;fontStyle=1;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="320" y="90" width="760" height="540" as="geometry"/>
        </mxCell>
        <mxCell id="tb3" value="TB3 — Browser (loopback SSE/WS)" style="rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;strokeColor=#7B68EE;verticalAlign=top;fontStyle=1;fontSize=10;" vertex="1" parent="1">
          <mxGeometry x="60" y="120" width="240" height="160" as="geometry"/>
        </mxCell>
        <mxCell id="browser" value="Browser SPA&#xa;(React, IndexedDB)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1">
          <mxGeometry x="90" y="170" width="180" height="80" as="geometry"/>
        </mxCell>
        <mxCell id="server" value="FastAPI server&#xa;app/server.py" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="1">
          <mxGeometry x="360" y="160" width="180" height="70" as="geometry"/>
        </mxCell>
        <mxCell id="exec" value="StreamingToolExecutor&#xa;(tool dispatch + sanitization)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="1">
          <mxGeometry x="590" y="160" width="220" height="70" as="geometry"/>
        </mxCell>
        <mxCell id="mcp" value="MCP Manager&#xa;(signing, guard, timeout)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="1">
          <mxGeometry x="590" y="260" width="220" height="70" as="geometry"/>
        </mxCell>
        <mxCell id="builtin" value="Builtin tools&#xa;file_*, ast_*, nova_web_search&#xa;(WritePolicyManager)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="1">
          <mxGeometry x="360" y="260" width="210" height="80" as="geometry"/>
        </mxCell>
        <mxCell id="shell" value="shell-server (subprocess)&#xa;allowlist + write policy" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;" vertex="1" parent="1">
          <mxGeometry x="360" y="380" width="210" height="70" as="geometry"/>
        </mxCell>
        <mxCell id="mcpext" value="External MCP servers&#xa;(stdio subprocess)" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;" vertex="1" parent="1">
          <mxGeometry x="590" y="380" width="220" height="70" as="geometry"/>
        </mxCell>
        <mxCell id="storage" value="Local storage&#xa;~/.ziya/ (projects, memory, audit 0600)&#xa;project workspace" style="shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;fillColor=#fff2cc;strokeColor=#d6b656;" vertex="1" parent="1">
          <mxGeometry x="360" y="500" width="260" height="90" as="geometry"/>
        </mxCell>
        <mxCell id="tb4" value="TB4 — Cloud LLM API (HTTPS)" style="rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;strokeColor=#D0021B;verticalAlign=top;fontStyle=1;fontSize=10;" vertex="1" parent="1">
          <mxGeometry x="1120" y="120" width="220" height="220" as="geometry"/>
        </mxCell>
        <mxCell id="llm" value="Bedrock / Anthropic /&#xa;OpenAI / Gemini" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#f8cecc;strokeColor=#b85450;" vertex="1" parent="1">
          <mxGeometry x="1150" y="170" width="160" height="70" as="geometry"/>
        </mxCell>
        <mxCell id="tb5" value="TB5 — Remote MCP servers (HTTPS, OAuth)" style="rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;strokeColor=#F5A623;verticalAlign=top;fontStyle=1;fontSize=10;" vertex="1" parent="1">
          <mxGeometry x="1120" y="360" width="220" height="140" as="geometry"/>
        </mxCell>
        <mxCell id="remotemcp" value="Remote MCP servers" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#ffe6cc;strokeColor=#d79b00;" vertex="1" parent="1">
          <mxGeometry x="1150" y="400" width="160" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="f1" value="1: prompt + selected files (SSE/WS, loopback)" style="endArrow=classic;html=1;" edge="1" parent="1" source="browser" target="server"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f2" value="2: model request (HTTPS, user creds)" style="endArrow=classic;html=1;" edge="1" parent="1" source="exec" target="llm"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f3" value="3: streamed tokens + tool_use" style="endArrow=classic;html=1;dashed=1;" edge="1" parent="1" source="llm" target="exec"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f4" value="4: dispatch" style="endArrow=classic;html=1;" edge="1" parent="1" source="exec" target="mcp"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f5" value="5: builtin call" style="endArrow=classic;html=1;" edge="1" parent="1" source="exec" target="builtin"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f6" value="6: JSON-RPC (stdio)" style="endArrow=classic;html=1;" edge="1" parent="1" source="mcp" target="shell"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f7" value="7: JSON-RPC (stdio)" style="endArrow=classic;html=1;" edge="1" parent="1" source="mcp" target="mcpext"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f8" value="8: HTTPS + OAuth" style="endArrow=classic;html=1;" edge="1" parent="1" source="mcp" target="remotemcp"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f9" value="9: write-policy-gated I/O" style="endArrow=classic;html=1;" edge="1" parent="1" source="builtin" target="storage"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f10" value="10: subprocess stdout (signed)" style="endArrow=classic;html=1;dashed=1;" edge="1" parent="1" source="shell" target="mcp"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f11" value="11: audit append (0600)" style="endArrow=classic;html=1;" edge="1" parent="1" source="exec" target="storage"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="f12" value="12: SSE response (tokens + results)" style="endArrow=classic;html=1;dashed=1;" edge="1" parent="1" source="server" target="browser"><mxGeometry relative="1" as="geometry"/></mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

---

## 5. Trust boundaries & controls

| # | Boundary | Controls |
|---|---|---|
| **TB1** | **Workstation ↔ anything off-host** | Only outbound HTTPS to LLM and remote MCP. Server binds `127.0.0.1`; no inbound listener off-loopback. |
| **TB2** | **Ziya process ↔ subprocesses** (shell, local MCP) | Separate subprocess per server (`shell=False`). Shell allowlist + write policy. HMAC-SHA256 result signing verified on return. Tool-guard scans descriptions, fingerprints tool sets, prevents built-in shadowing. 4-layer nested timeout chain (30s/30s/120s/300s). |
| **TB3** | **Browser ↔ server** (loopback HTTP/SSE/WS) | Loopback-only bind; same-origin SPA; CORS locked to localhost. No auth on API because no off-host surface. |
| **TB4** | **LLM API** (Bedrock/Anthropic/OpenAI/Gemini) | HTTPS. **Output treated as untrusted**: tool args validated against JSON schema before dispatch; response validator strips hidden characters; result sanitizer caps size and safely extracts base64 documents; results HMAC-signed so the model cannot hallucinate one. |
| **TB5** | **Remote MCP** (third-party / enterprise) | MCP SDK `ClientSession` over StreamableHTTP/SSE. OAuth bearer. Injection scan + fingerprint rug-pull detection at connect; shadowing prevention; result signing; result sanitization. |

---

## 6. Residual risks (accepted)

1. **Loopback assumption.** Confidentiality of prompts depends on other local users not being able to reach loopback.
2. **File-selection discipline.** The developer must not select secret-bearing files into context. `.ziyaignore` and gitignore-respect reduce accidents but are not secret scanners.
3. **Upstream provider trust.** Content sent to the LLM is subject to the provider's data policy.
4. **Plugin trust.** Enterprise plugins run with full process privilege (opt-in via env var).
5. **Browser compromise.** A malicious extension with `localhost` host permission can read streamed responses.

---

## 7. Out of scope

- **Multi-tenant hosted deployment.** Explicitly unsupported. Would require a separate threat model.
- **Adversarial external users.** There are no external users.
- **Provider-side compromise.** Contractual, not a local control.
- **Physical endpoint compromise.** Handled by endpoint management.

---

## 8. Where to look next

| Topic | Document |
|---|---|
| High-level system architecture, component map, request flow | `Docs/ArchitectureOverview.md` |
| MCP threat mitigations in depth (poisoning, overreach, shadowing, rug-pull, signing, timeouts) | `Docs/MCPSecurityControls.md` |
| Public security policy / vulnerability reporting | `Docs/SECURITY.md` |
| Plugin system, endpoint restriction, data retention, tool-result filters | `Docs/Enterprise.md` |
| MCP execution timeout chain details | `Docs/MCPSecurityControls.md` §7 |

### Code anchors

- `app/streaming_tool_executor.py` — the single tool-dispatch choke point
- `app/mcp/manager.py` — MCP lifecycle, timeouts
- `app/mcp/client.py` — JSON-RPC transport, policy-block detection
- `app/mcp/tool_guard.py` — injection scan, shadowing, fingerprinting
- `app/mcp/signing.py` — HMAC-SHA256 result signing
- `app/mcp/response_validator.py` — hidden-char stripping, schema validation
- `app/mcp_servers/shell_server.py` — shell allowlist + subprocess execution
- `app/mcp_servers/write_policy.py` — `ShellWriteChecker`, `WritePolicyManager`
- `app/utils/tool_result_sanitizer.py` — size cap + base64 extraction
- `tests/test_atc_self_scan.py` — internal Agent Tool Checker equivalent scan
