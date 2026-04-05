# Competitive Analysis: Ziya vs. Claude Code Agent Harness

**Source:** Nate B. Jones, *"Your Agent Is 80% Plumbing. Here Are the 12 Pieces
You're Missing."* — [Nate's Substack](https://natesnewsletter.substack.com/p/your-agent-has-12-blind-spots-you),
April 3, 2026.

**Context:** On March 31, 2026 Anthropic accidentally shipped a `.map` file in
`@anthropic-ai/claude-code` v2.1.88 on npm, exposing 512,000+ lines of
TypeScript across 1,902 files.  Jones' analysis extracts the 12
infrastructure primitives that make the harness work — prioritized into
Day One / Week One / Month One tiers — and argues these are *structural
requirements of any agent that has to work for real*, not Anthropic-specific
patterns.

His thesis:

> "The LLM call is maybe 20% of Claude Code. The other 80% is plumbing:
> session persistence, permission pipelines, context budget management, tool
> registries, security stacks, error recovery. The boring stuff that nobody
> writes tutorials about."

This document maps each of Jones' 12 primitives (plus his Month One
extensions) against Ziya's current codebase, identifies gaps, calls out
areas where Ziya has a **superior** approach, and proposes implementation
work where needed.

---

## Scorecard Summary

| # | Primitive | Tier | Ziya Status | Rating |
|---|-----------|------|-------------|--------|
| 1 | Tool Registry with Metadata-First Design | Day 1 | ✅ Implemented + superior MCP-native design | **SUPERIOR** |
| 2 | Permission System with Trust Tiers | Day 1 | ✅ Implemented + HMAC signing (unique) | **SUPERIOR** |
| 3 | Session Persistence that Survives Crashes | Day 1 | ✅ Implemented with dual-write + shell guard | **COMPARABLE+** |
| 4 | Workflow State and Idempotency | Day 1 | ⚠️ Partial — streaming state only | **GAP** |
| 5 | Token Budget Tracking with Pre-Turn Checks | Day 1 | ⚠️ Partial — tracking exists, no pre-turn gate | **GAP** |
| 6 | Structured Streaming Events | Day 1 | ✅ Rich typed SSE event system | **COMPARABLE** |
| 7 | System Event Logging | Day 1 | ✅ Comprehensive structured logging | **COMPARABLE** |
| 8 | Basic Verification Harness | Day 1 | ✅ Diff validation + language validators | **SUPERIOR** |
| 9 | Tool Pool Assembly | Wk 1 | ✅ Skill-based tool prioritization | **COMPARABLE** |
| 10 | Transcript Compaction | Wk 1 | ❌ Missing — no context compression | **MAJOR GAP** |
| 11 | Permission Audit Trail | Wk 1 | ⚠️ Partial — logging exists, no structured trail | **GAP** |
| 12 | The Doctor Pattern | Wk 1 | ✅ `/api/info`, `/debug/mcp-state`, health checks | **COMPARABLE** |
| — | Agent Type System | Mo 1 | ✅ Delegate/swarm system with typed roles | **SUPERIOR** |
| — | Memory System | Mo 1 | ⚠️ Contexts + Skills exist, no auto-consolidation | **GAP** |
| — | Skills and Extensibility | Mo 1 | ✅ Plugin system + MCP + dynamic tool loading | **SUPERIOR** |

**Summary: 5 Superior · 4 Comparable · 4 Gaps · 1 Major Gap**

---

## Detailed Analysis by Primitive

---

### PRIMITIVE 1 — Tool Registry with Metadata-First Design

**Jones' description:**
> "Define your agent's capabilities as a data structure before writing any
> implementation code. The registry should answer 'what exists and what does
> it do?' without executing anything."

Claude Code maintains two parallel registries: 207 user-facing commands and
184 model-facing tools, each with name, source hint, and responsibility
description.

**Ziya's implementation:**

Ziya's tool system is **MCP-native from the ground up**, which is
architecturally more forward-looking than Claude Code's monolithic registry.

| Component | Location | Role |
|-----------|----------|------|
| `MCPManager` | `app/mcp/manager.py` | Central registry for all tool servers |
| `BaseMCPTool` | `app/mcp/tools/` | Metadata-first interface: name, description, input_schema |
| `builtin_tools.py` | `app/mcp/` | Static registration of in-process tools |
| `dynamic_tools.py` | `app/mcp/` | Runtime tool loading based on file selection |
| `enhanced_tools.py` | `app/mcp/` | LangChain `StructuredTool` wrappers with caching |

The tool cache (`_mcp_tools_cache` in `server.py`) auto-invalidates every
30 seconds, and `create_secure_mcp_tools()` returns introspectable metadata
without executing anything — matching Jones' `listTools()` requirement.

**Rating: SUPERIOR.** Claude Code bolted MCP support onto a monolithic tool
system. Ziya's entire tool architecture *is* MCP, meaning any
standards-compliant server is automatically a first-class citizen. The
30-second cache TTL, skill-based prioritization (`preferredToolIds`), and
`/api/dynamic-tools/update` endpoint for file-selection-driven loading have
no Claude Code equivalent.

---

### PRIMITIVE 2 — Permission System with Trust Tiers

**Jones' description:**
> "Claude Code segments capabilities into three trust tiers: built-in
> (always available, highest trust), plugin (medium trust, can be disabled),
> and skill (user-defined, lowest trust by default). BashTool alone has an
> 18-module security architecture."

**Ziya's implementation:**

| Layer | Location | Mechanism |
|-------|----------|-----------|
| Tool poisoning scanner | `app/mcp/tool_guard.py` | 13 regex patterns at connect time |
| Tool shadowing prevention | `tool_guard.py` | Built-in names registered first; collisions silently dropped |
| Rug-pull fingerprinting | `tool_guard.py` | SHA-256 hash of tool definitions; drift detection on reconnect |
| Cryptographic result signing | `app/mcp/signing.py` | **HMAC-SHA256 on every tool result** |
| Result verification | `streaming_tool_executor.py` | Signature check before display or model consumption |
| Shell write policy | Runtime enforcement | `sed -i`, `>`, `rm` etc. blocked on project files |
| Tool result sanitization | `app/utils/tool_result_sanitizer.py` | Base64 replacement, PDF extraction, size cap |
| Plugin filters | `ToolResultFilterProvider` | Extension point for site-specific cleanup |

**Rating: SUPERIOR.** Ziya has something Claude Code completely lacks:
**cryptographic verification of tool results**. Every result is
HMAC-SHA256-signed by `MCPClient` before return, and verified before the
model or user sees it.  Unverified results are rejected with a corrective
error message that tells the model the call was rejected.  This prevents an
entire class of result-spoofing attacks that Claude Code's architecture
cannot detect.

The rug-pull fingerprinting (detecting post-install tool definition
mutations) is also unique to Ziya.

Where Claude Code's 18-module BashTool pipeline is deeper: Ziya's shell
security is runtime enforcement (blocked commands list) rather than a
multi-stage classification pipeline. This is adequate for the browser-first
architecture but less granular than Claude Code's approach for terminal-first
use.

---

### PRIMITIVE 3 — Session Persistence that Survives Crashes

**Jones' description:**
> "Your agent's session is more than the conversation history. It's a
> recoverable state object that includes the conversation, usage metrics,
> permission decisions, and configuration."

Claude Code persists sessions as JSON files on disk with session ID,
messages, and token usage. `resumeSession(id)` reconstructs full state.

**Ziya's implementation:**

Ziya's persistence is **browser-first with server dual-write**, which is a
fundamentally different (and in some ways more resilient) architecture:

| Layer | Mechanism |
|-------|-----------|
| Primary store | IndexedDB in browser (`db.ts`) |
| Shell loading | `getConversationShells()` — first+last messages only for instant sidebar |
| Shell guards | `_isShell` marker, `_fullMessageCount`, save blocker, IDB write guard |
| Message count regression guards | 9 separate layers preventing message loss (see `ArchitectureOverview.md`) |
| Server dual-write | `queueSave()` → `bulkSync()` to server; debounced at 2s during streaming |
| Cross-tab sync | `BroadcastChannel` via `projectSync` |
| IDB read-before-write | Every write reads existing record and preserves higher message counts |
| Web Lock | `navigator.locks` during streaming to signal browser the tab is active |
| Wake Lock | `navigator.wakeLock` to prevent OS sleep during streaming |

**Rating: COMPARABLE+.** Both systems survive crashes. Ziya's approach is
architecturally different — browser-primary with server backup rather than
filesystem-primary — but the defense-in-depth against message loss (9
separate guard layers documented in `ArchitectureOverview.md`) is more
extensive than what's been reported about Claude Code's JSONL persistence.

The Web Lock + Wake Lock combination for preventing OS-level sleep during
streaming has no Claude Code equivalent (Claude Code runs in a terminal
where this isn't an issue).

---

### PRIMITIVE 4 — Workflow State and Idempotency

**Jones' description:**
> "Almost every agent framework conflates conversation state with task
> state. They're different problems with different solutions. Without
> workflow state, your agent can't survive a crash mid-tool-execution
> without potentially duplicating a write."

Jones recommends: explicit states (planned → awaiting_approval → executing →
completed → failed), workflow checkpoints after every side-effecting step,
and idempotency keys for mutating operations.

**Ziya's current state:**

Ziya has `ProcessingState` tracking (`idle`, `sending`,
`awaiting_model_response`, `processing_tools`, etc.) but this is
**streaming UI state**, not workflow state in Jones' sense.

- ✅ `FileStateManager` tracks file state across conversations (4 copies per file)
- ✅ Delegate system (`delegate_manager.py`) has plan persistence across restarts
- ❌ No idempotency keys on tool executions
- ❌ No checkpoint/resume for mid-tool-execution crashes
- ❌ Conversation-level "what step are we in" isn't persisted

**Rating: GAP.** The delegate system's plan persistence (`_persist_plan` in
shutdown) is the closest thing to workflow state, but the main conversation
loop lacks it.  A crash during a multi-tool iteration restarts from
scratch.

**Implementation plan:**

| Phase | Work | Effort |
|-------|------|--------|
| Phase 1 | Add idempotency keys to `MCPManager.call_tool()` — hash of (tool_name, arguments, conversation_id, iteration) as dedup key. Cache results for 5 minutes. | 1-2 days |
| Phase 2 | Persist iteration state in `StreamingToolExecutor` — after each tool result, write `{iteration, tool_results, partial_content}` to a recovery file. On crash, `stream_with_tools()` checks for recovery state before starting fresh. | 2-3 days |
| Phase 3 | Expose workflow state to frontend — `processing_state` already exists; extend with `completed_tools`, `pending_tools`, `checkpoint_id`. | 1 day |

---

### PRIMITIVE 5 — Token Budget Tracking with Pre-Turn Checks

**Jones' description:**
> "Claude Code's query engine configuration defines hard limits: maximum
> turns, maximum budget tokens, and a compaction threshold. Every turn
> calculates projected token usage. If the projection exceeds the budget,
> execution halts with a structured stop reason before the API call is
> made."

Jones highlights a specific horror story from the leak: Claude Code's
`autoCompact` mechanism retried 3,272 times with no upper limit, silently
burning tokens.  The fix was three lines of code:
`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`.

**Ziya's current state:**

| Feature | Status |
|---------|--------|
| Per-session token tracking | ✅ `GlobalUsageTracker` in `streaming_tool_executor.py` |
| Per-iteration usage logging | ✅ `IterationUsage` with input/output/cache tokens |
| Cache health telemetry | ✅ `/api/telemetry/cache-health` endpoint |
| Max iterations cap | ✅ 200 iterations in `stream_with_tools()` |
| Pre-turn budget gate | ❌ No projection before API call |
| Structured stop reason | ⚠️ Partial — `stream_end` type exists but no `max_budget_reached` |
| Cost threshold alerts | ❌ Not implemented |
| User-visible budget status | ❌ Not exposed to frontend |

**Rating: GAP.** Ziya tracks usage after the fact but doesn't gate on it
before making API calls.  The iteration cap (200) prevents runaway loops but
is a blunt instrument compared to token-budget projection.

**Implementation plan:**

| Phase | Work | Effort |
|-------|------|--------|
| Phase 1 | Add `budget_tokens` to `StreamingToolExecutor.__init__()`. Before each `invoke_model_with_response_stream` call, sum `GlobalUsageTracker` totals. If projected to exceed budget, emit `{type: 'budget_warning'}` event and switch to wrap-up mode (reduce `max_tokens` to 500, add "please wrap up concisely" to system prompt). | 2 days |
| Phase 2 | Add `max_budget_reached` stop reason. Frontend shows "Budget limit reached" with token breakdown. | 1 day |
| Phase 3 | Per-conversation cost tracking exposed in conversation metadata. User-configurable budget per conversation. | 2-3 days |

---

### PRIMITIVE 6 — Structured Streaming Events

**Jones' description:**
> "Claude Code's query engine emits typed events: message_start,
> command_match, tool_match, permission_denial, message_delta, and
> message_stop. The final event carries usage statistics and a stop reason."

**Ziya's implementation:**

Ziya emits rich typed SSE events throughout `stream_chunks()`:

| Event Type | Data |
|------------|------|
| `heartbeat` | Connection keepalive |
| `content` | Text chunks (with REWIND_MARKER stripping) |
| `tool_start` | Tool name, status |
| `tool_result` / `tool_display` | Tool output for frontend rendering |
| `error` | Error type, can_retry flag, retry_message |
| `throttling_error` | Rate limit details, inline retry |
| `processing` / `processing_state` | Thinking heartbeats |
| `validation_retry` | Diff validation failure + corrected output |
| `context_sync` | Auto-added files notification |
| `rewind` | Continuation splicing metadata |
| `done` | Stream completion |

The `_keepalive_wrapper` emits SSE comment pings (`: keepalive`) every 15
seconds during idle periods — preventing browser/proxy disconnections during
long tool executions.

**Rating: COMPARABLE.** Both systems have rich typed event streams. Ziya's
`validation_retry` and `context_sync` events have no Claude Code equivalent.
Claude Code's `stop_reason` in the final event is more formalized than
Ziya's `done` marker (see Primitive 5 gap).

---

### PRIMITIVE 7 — System Event Logging

**Jones' description:**
> "Separate from the conversation, Claude Code maintains a HistoryLog of
> system events: context loading, registry initialization, routing
> decisions, execution counts, permission denials, session persistence
> events."

**Ziya's implementation:**

Ziya uses structured Python logging (`app/utils/logging_utils.py`) with
category-prefixed messages throughout:

- `🔧 MCP:` — tool initialization, connection, execution
- `🔐 SECURITY:` — verification failures, result signing
- `🔄 PROJECT:` — project root changes, context switches
- `📡 SERVER_SYNC:` / `DUAL_WRITE:` — persistence events
- `🚀 DIRECT_STREAMING:` — model routing decisions
- `📂` — folder cache, file watcher events

The `_PollingAccessFilter` suppresses routine polling from uvicorn access
logs to keep the signal-to-noise ratio manageable.

**Rating: COMPARABLE.** Both systems have structured logging separate from
conversation transcripts. Claude Code's is more formalized (typed
`HistoryLog` entries); Ziya's is more ad-hoc (emoji-prefixed log messages).
The content coverage is similar.

---

### PRIMITIVE 8 — Basic Verification Harness

**Jones' description:**
> "A small set of invariant tests: destructive tools always require
> approval, structured outputs validate against schema, denied tools never
> execute, budget exhaustion produces a graceful stop."

**Ziya's implementation:**

Ziya goes **well beyond** basic invariant tests with a full post-stream
**diff validation pipeline**:

| Layer | Location | Mechanism |
|-------|----------|-----------|
| Diff validation hook | `app/utils/diff_validation_hook.py` | Validates all diffs in model output against actual files |
| Language validators | `app/utils/diff_utils/language_handlers/` | Python, TypeScript, JavaScript, Java, C++, Rust syntax checking |
| TypeScript validation | Language handler | `tsc --isolatedModules --noResolve`, TS1xxx = hard fail, TS2xxx+ = pass |
| Auto-retry on failure | `stream_chunks()` | Failed diffs trigger re-generation with feedback to model |
| Context auto-enhancement | `DiffToken` component | Missing files auto-added to context before diff application |
| Multi-strategy application | `pipeline_manager.py` | system patch → git apply → difflib fuzzy → (future: LLM resolver) |

This is a **verification harness that operates on the model's output
quality**, not just system invariants. When a diff fails validation, the
system adds the failing diff as feedback to the model and re-generates —
something Claude Code does not do.

**Rating: SUPERIOR.** Claude Code's verification (per the leak) is
employee-only feature-flagged and checks whether generated code compiles.
Ziya's verification is always-on, language-aware, and auto-correcting.

---

### PRIMITIVE 9 — Tool Pool Assembly

**Jones' description:**
> "Not every conversation needs every tool. Claude Code assembles a
> session-specific 'tool pool' based on mode flags, permission context, and
> deny-lists."

**Ziya's implementation:**

```python
# server.py — skill-based tool prioritization
if preferred_tool_ids and mcp_tools:
    preferred_set = set(preferred_tool_ids)
    preferred = [t for t in mcp_tools if t.name in preferred_set]
    others = [t for t in mcp_tools if t.name not in preferred_set]
    mcp_tools = preferred + others
```

Active skills specify `preferredToolIds` which reorder (not filter) the tool
list.  The `dynamic_tools.py` loader adds/removes tools based on file
selection changes.  The 30-second MCP tools cache prevents stale pools.

**Rating: COMPARABLE.** Both systems assemble context-specific tool sets.
Claude Code's deny-lists and prefix-level filtering are more granular; Ziya's
skill-based prioritization and file-driven dynamic loading are more
user-facing.

---

### PRIMITIVE 10 — Transcript Compaction

**Jones' description:**
> "Conversation history is a managed resource, not an append-only log.
> Claude Code automatically compacts after a configurable number of turns,
> keeping recent entries and discarding older ones."

The WaveSpeed analysis identified **five** compaction strategies in Claude
Code: time-based clearing, conversation summarization, session memory
extraction, full history summarization, and oldest-message truncation.

**Ziya's current state:**

- ✅ Tool result sanitization truncates large results (`TOOL_RESULT_MAX_CHARS`, default 100K)
- ✅ `_content_optimizer` in `StreamingToolExecutor` exists (name suggests optimization)
- ❌ **No automatic conversation summarization**
- ❌ **No context window pressure management**
- ❌ **No compaction threshold or strategy**
- ❌ Long sessions rely entirely on the model's context window with no active management

**Rating: MAJOR GAP.** This is the most significant architectural gap. Jones
specifically highlights that Claude Code treats context management as *"a
first-class correctness concern — not something added when things start
breaking in production."*  Ziya has no equivalent.  Long-running
conversations (15+ tool iterations) will silently degrade as the context
window fills.

**Implementation plan:**

| Phase | Work | Effort |
|-------|------|--------|
| Phase 1 | **Token pressure gauge** — Before each API call in `stream_with_tools()`, estimate total context size. When >70% of model's `token_limit`, emit `{type: 'context_pressure', level: 'warning'}` event. | 2 days |
| Phase 2 | **Oldest-message truncation** — When pressure exceeds 80%, drop the oldest non-system messages from the conversation array passed to the model. Keep system prompt + last N turns. Log what was dropped. | 2-3 days |
| Phase 3 | **Tool result summarization** — Before truncation, replace verbose tool results (>5000 chars) with LLM-generated summaries (single service-model call). Preserve the summary in the conversation. | 3-5 days |
| Phase 4 | **Session memory extraction** — At compaction time, extract key facts/decisions into a structured summary block that persists at the top of context. Similar to Claude Code's session memory. | 1 week |

---

### PRIMITIVE 11 — Permission Audit Trail

**Jones' description:**
> "Claude Code tracks every permission denial as a structured data object
> (tool name and reason) and accumulates denials per session, including them
> in turn results."

**Ziya's current state:**

- ✅ `_security_stats` tracks total/successful/failed verifications
- ✅ `hallucination_attempts` list with tool_name, error, timestamp (last 100)
- ✅ Tool Guard logs warnings for shadowed tools and poisoning detections
- ❌ No per-session denial accumulation
- ❌ Denials not included in turn results to model
- ❌ No structured permission audit export

**Rating: GAP.** The security stats infrastructure exists but isn't
structured as a per-session audit trail.

**Implementation plan:**

| Phase | Work | Effort |
|-------|------|--------|
| Phase 1 | Add `PermissionDecision` dataclass: `{tool_name, decision, reason, timestamp, session_id}`. Accumulate in `StreamingToolExecutor` per-request. | 1 day |
| Phase 2 | Include denial count in tool results sent back to model so it can adjust behavior. | 0.5 day |
| Phase 3 | `/api/permission-audit` endpoint for admin visibility. | 0.5 day |

---

### PRIMITIVE 12 — The Doctor Pattern

**Jones' description:**
> "Claude Code has a dedicated health check command that inspects the
> system and reports problems. Build a /doctor endpoint."

**Ziya's implementation:**

| Endpoint | Purpose |
|----------|---------|
| `/api/info` | Full system info: version, model, AWS credentials, plugins, MCP status, features |
| `/api/debug/mcp-state` | MCP connection health, process status, tool counts |
| `/api/debug/reset-mcp` | Recovery from stuck tool execution |
| `/api/telemetry/cache-health` | Cache hit rates, throttle pressure, cost tracking |
| `/api/cache-test` | Context caching configuration validation |
| `/debug2` | Full HTML system info page |

**Rating: COMPARABLE.** Both systems have comprehensive health inspection.
Ziya's is split across multiple endpoints rather than a single `/doctor`
command, but the coverage is equivalent.

---

## Month One Extensions

### Agent Type System

**Jones:** Claude Code defines 6 agent types (explore, plan, verification,
guide, general purpose, statusline setup), each with own prompt, allowed
tools, and behavioral constraints.

**Ziya's implementation:** The delegate/swarm system (`app/agents/delegate_manager.py`)
goes further — it's a full **multi-agent orchestration system** with:

- Coordinator → Worker decomposition with structured task plans
- Per-delegate conversation isolation
- WebSocket live streaming relay (`delegate_stream_relay.py`)
- Plan persistence across server restarts
- Status polling with terminal state detection

**Rating: SUPERIOR.** Claude Code's agent types are prompt-driven role
variations within a single process. Ziya's delegates are fully isolated
concurrent agents with their own conversations, streaming connections, and
lifecycle management.

---

### Memory System

**Jones:** Claude Code has an 8-module memory subsystem with relevance
scoring, aging, type categorization, and scoping (personal, team, project).

**Ziya's current state:**

| Feature | Status |
|---------|--------|
| Project Contexts | ✅ Per-project reusable context documents (`app/api/contexts.py`) |
| Project Skills | ✅ Per-project behavioral instructions with model overrides |
| Per-project settings | ✅ File selections, external paths, preferences |
| Auto-consolidation | ❌ No "dream" or background memory tidying |
| Relevance scoring | ❌ No automatic relevance-based retrieval |
| Memory aging/decay | ❌ Not implemented |

**Rating: GAP.** Ziya has the *manual* building blocks (contexts, skills)
but lacks the *automatic* memory management that Jones identifies as
critical.  Claude Code's approach — *"the agent is instructed to treat its
own memory as a 'hint' and verify against the actual codebase before
acting"* — is a design principle Ziya should adopt.

---

### Skills and Extensibility

**Jones:** Claude Code supports 20 skill modules: bundled, user-defined
from a directory, and auto-generated from MCP server capabilities.

**Ziya's implementation:**

| Extension Point | Mechanism |
|-----------------|-----------|
| MCP servers | stdio + StreamableHTTP/SSE transports |
| Builtin tools | `BaseMCPTool` interface, in-process |
| Dynamic tools | File-selection-driven loading (`dynamic_tools.py`) |
| Plugin system | `AuthProvider`, `ConfigProvider`, `ServiceModelProvider`, `DataRetentionProvider`, `ShellConfigProvider`, `FormatterProvider`, `ToolResultFilterProvider` |
| Skills | Per-project behavioral configurations with model overrides and preferred tools |
| MCP Registry | Discovery and installation of community MCP servers |

**Rating: SUPERIOR.** Ziya's plugin architecture is more formally
structured than Claude Code's skill system.  The 7 provider types create
well-defined extension points for enterprise deployment.  The MCP Registry
for community server discovery has no Claude Code equivalent.

---

## Implementation Priority Matrix

Based on Jones' tier framework, Ziya's gaps align to this build order:

### Immediate (This Sprint)

1. **Token budget pre-turn gate** (Primitive 5, Phase 1) — Highest ROI
   safety improvement. Prevents runaway cost without architectural changes.
   *2 days.*

2. **Tool execution idempotency keys** (Primitive 4, Phase 1) — Hash-based
   dedup on `call_tool()`. Prevents double-execution on retry.
   *1-2 days.*

### Next Sprint

3. **Context pressure gauge + oldest-message truncation** (Primitive 10,
   Phases 1-2) — The major gap. Start with measurement, then add the
   simplest compaction strategy. *4-5 days.*

4. **Permission audit trail** (Primitive 11) — Lightweight structured
   logging. *2 days.*

### Following Sprint

5. **Tool result summarization** (Primitive 10, Phase 3) — Use the
   existing `GroundingService` pattern (service model call) to summarize
   large tool results before they enter conversation context. *3-5 days.*

6. **Workflow checkpointing** (Primitive 4, Phase 2) — Recovery file per
   streaming session. *2-3 days.*

### Quarter Plan

7. **Session memory extraction** (Primitive 10, Phase 4) — Extract
   key decisions into a persistent summary. *1 week.*

8. **Automatic memory consolidation** (Month One Memory gap) — Background
   process to tidy contexts and detect stale/contradictory information.
   *2-3 weeks.*

---

## Architectural Advantages Unique to Ziya

These capabilities have no equivalent in the Claude Code architecture as
revealed by the leak:

| Capability | Description |
|------------|-------------|
| **Cryptographic tool result signing** | HMAC-SHA256 on every tool result, verified before display or model consumption. Prevents result spoofing attacks. |
| **Multi-endpoint architecture** | Bedrock (Claude, Nova, DeepSeek, OpenAI-on-Bedrock), Google Gemini, OpenAI direct, Anthropic direct. Claude Code is Claude-only. |
| **Post-stream diff validation** | Language-aware syntax validation of model-generated diffs, with auto-retry on failure. |
| **Rug-pull fingerprinting** | SHA-256 hash of tool definitions detects post-install mutations. |
| **Browser-first persistence** | Dual-write (IndexedDB + server) with 9-layer message loss prevention. Local-first privacy model. |
| **MCP-native tool architecture** | Tools are MCP servers, not monolithic internal modules. Standards-compliant extensibility. |
| **Visual rendering pipeline** | Mermaid, Graphviz, Vega-Lite, DrawIO, packet diagrams, HTML mockups — all rendered inline. |
| **SSE keepalive wrapper** | Comment-ping heartbeats prevent proxy/browser disconnection during long tool executions. |
| **Wake Lock streaming** | `navigator.wakeLock` prevents OS sleep during response generation. |

---

## Key Quotes for Reference

Jones on the meta-lesson:
> "Is your development velocity outrunning your operational discipline?"

On verification (relevant to Ziya's superior diff validation):
> "Observability tells you what happened. Verification tells you whether
> the system is still good. Without a verification layer, prompt tweaks,
> tool changes, and model swaps silently degrade behavior."

On memory trust (design principle Ziya should adopt):
> "The agent is instructed to treat its own memory as a 'hint' and verify
> against the actual codebase before acting. It doesn't trust what it
> remembers."

On over-engineering (validates Ziya's approach):
> "The most common mistake I see in agentic system design isn't
> under-engineering. It's over-engineering — building a multi-agent
> coordination layer before you have a working permission system."

On universality (validates MCP-native approach):
> "The same architectural primitives transfer across languages, frameworks,
> and vendors because they're structural requirements of the problem, not
> Anthropic-specific patterns."
