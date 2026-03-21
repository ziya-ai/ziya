# Structured Memory System

## Design Status: Draft — Philosophy + Architecture Defined, Pre-Implementation

---

## 1. Problem Statement

Every Ziya session starts from zero. The user must re-explain their domain, their vocabulary, their architecture, their active work threads, and their decisions every time they open a conversation. For an enterprise architect working across multiple large projects over years, this means the first 5–15 minutes of every session is wasted on background that doesn't change.

The system should behave like a colleague who was in every previous meeting — not by announcing what it remembers, but by simply being informed. The user should notice the absence of ignorance, not the presence of recall.

### What This Is Not

- Not a coding-agent feature list tracker (Anthropic's `feature_list.json` pattern)
- Not a vector database or RAG system
- Not a shared team knowledge base
- Not a replacement for documentation

### What This Is

A personal, persistent, structured knowledge store that enables the model to have informed conversations across sessions without the user re-providing context. The store grows organically from conversation, is invisible when working correctly, and is human-auditable when inspected.

---

## 2. Research Foundation

This design is grounded in findings from seven primary sources. Full synthesis in `.ziya/memory-system-research.md`.

**Anthropic — "Effective Harnesses for Long-Running Agents" (Nov 2025)**
> "After some experimentation, we landed on using JSON for this, as the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files."

JSON for machine state. The model treats structured JSON as a contract to respect, not prose to paraphrase.

**Anthropic — "Effective Context Engineering for AI Agents" (Jul 2025)**
Context rot: recall accuracy degrades as context grows. Memory must earn its place — every token competes with the actual conversation. Two-tier loading (always-loaded brief + on-demand depth) is the proven pattern.

**Letta/MemGPT Benchmark (Aug 2025)**
Simple filesystem memory (74.0% on LoCoMo) beats specialized tools like Mem0's knowledge graph (68.5%). Models are trained on file operations. Simple tools > exotic infrastructure.

**Google Research — ReasoningBank (Sep 2025)**
Memory should store **strategies and principles**, not raw traces. +34.2% effectiveness. Critically: failures become negative constraints that prevent repeated mistakes.

**Agent Cognitive Compressor (Jan 2026)**
Unbounded memory causes "memory-induced drift" and "memory poisoning." Bounded state with a commitment gate — not everything observed becomes permanent memory.

**Memory-as-Asset (Mar 2026)**
Memory is a human-owned asset. The user must be able to see, edit, and delete any memory. The agent proposes; the human governs.

**The New Stack Survey (2025)**
Three lifecycle phases: Extraction (salience detection), Consolidation (re-evaluation on retrieval), Retrieval (weighted by recency and importance).

---

## 3. Core Principles

### 3.1 The Usefulness Test

A memory earns its place if and only if **removing it would force the user to re-explain something next session.**

Not "is this interesting" or "did this come up" — but "will I have to say this again?"

### 3.2 Invisible When Working

The system should be invisible during normal operation. The user should not receive a briefing at session start. The model should not announce "I recall from our previous discussion..." It should simply be informed — the user notices competence, not recall machinery.

Memory is visible only when:
- The user explicitly asks (`/memory`, "what do you know about my projects")
- Ambiguity needs resolution ("When you say 'beam,' do you mean coverage area or RF signal?")
- Memories are proposed for saving (at natural pauses, not session boundaries)
- Contradictions are detected ("You mentioned 512MB earlier but 256MB now — which is current?")
- A new domain store is suggested ("This is the third genealogy conversation — want me to maintain context for this area?")

### 3.3 Associative Activation

Memory activates based on conversational content, not session setup. The model reads the user's input, recognizes domain signals, and loads relevant context. This mirrors human associative recall — hearing "return link" activates satellite knowledge without conscious effort.

### 3.4 Progressive Depth

Only the correct depth loads for a given task. Top-level summaries are cheap and always available. Detail loads only when the conversation goes deep on a topic. As conversation moves between topics, detail collapses and expands — like a virtual memory paging system.

### 3.5 Human Ownership

The user owns their memory. They can review, edit, and delete anything. The agent proposes; the user approves. Memory files are local, never transmitted, never shared without explicit user action.

---

## 4. The Layer Model

Enterprise domain memory stratifies naturally by volatility:

| Layer | Description | Volatility | Example |
|-------|-------------|-----------|---------|
| **Domain Context** | What the system/project IS | Years | "LEO is a 1,600-sat broadband constellation, formerly Kuiper" |
| **Architecture** | How it's structured | Quarters | "Gen 1: GMS→Ka→OBP→ISL→OBP→Ka→UT, three orbital shells" |
| **Lexicon** | Domain vocabulary, disambiguations | Grows, rarely deprecated | "FCTS = Forward Channel Transport System (ground→sat→UT path)" |
| **Decisions & Rationale** | What was chosen and why | Permanent | "Chose CCSDS framing over IP — OBP can't handle full IP stack" |
| **Active Threads** | Current work in progress | Weeks | "Investigating credit-based flow control for return link" |
| **Process / Method** | How things get done | Quarters | "Test progression: unit→HIL→flat-sat→IOT→constellation" |

Each layer has a natural staleness rate. Domain context almost never changes. Active threads change weekly.

### Negative Constraints

Per Google's ReasoningBank research, **failure patterns are as valuable as successes**. Memories recording what was tried and why it failed prevent the most wasteful kind of rediscovery — re-making a mistake.

Example: "TRIED AND REJECTED: Static bandwidth allocation per UT on return link. Failed because traffic is bursty — 90% of UTs idle at any time, static allocation wastes 85%+ of return capacity."

---

## 5. Architecture: Dual Representation

Two access patterns over the same data:

### 5.1 Flat Store — For Searching

All memories live in one flat list. Each memory has an ID, content, tags, layer, scope metadata, and status. Searchable by keyword, tag, layer, or any combination.

This is the source of truth. Memories are created, updated, and retired here. Everything else is a view over this store.

### 5.2 Mind-Map — For Traversing

A tree/graph structure where each node has:
- A **handle**: a summary compact enough to scan cheaply (~30 tokens)
- **Children**: sub-nodes for more specific topics
- **Memory references**: pointers into the flat store by ID
- **Cross-links**: connections to related nodes in other branches

The mind-map enables progressive loading. The model traverses from root → domain → topic → detail, expanding only what the conversation needs.

### 5.3 How They Work Together

```
Search path:                          Traverse path:
"Was there something about NACK?"     User mentions "return link"
  → scan flat store by keyword          → model sees LEO handle (Level 0)
  → return m_007 directly               → expands leo-network (Level 1)
  → regardless of tree position         → sees flow-control child handle
                                        → expands leo-fc (Level 2)
                                        → loads m_005, m_006, m_007
                                        → follows cross-link to OBP constraints
```

Search is for targeted lookup. Traversal is for progressive context building. Both access the same underlying memories.

### 5.4 Cell Division

When a cluster of memories grows dense enough (many memories sharing tags, accessed together), the mind-map tree splits: a new node is created with a generated handle, and memory references are assigned to it. The flat store doesn't change — this is purely a tree reorganization.

Trigger for separation:
- **Depth**: >10-15 memories sharing a tag cluster
- **Access correlation**: these memories are accessed together
- **Summary feasibility**: the cluster can be meaningfully summarized in one sentence

---

## 6. Storage Architecture

### 6.1 User-Global Store (Primary)

```
~/.ziya/memory/
  memories.json         # Flat store — all memories, all domains
  mindmap.json          # Tree/graph structure with handles and cross-links
  profile.json          # User preferences, communication style, meta
```

All domain knowledge lives here. Satellite architecture, genetic genealogy, organizational process — everything. This is the single source of truth.

### 6.2 Project-Root Hints (Metadata Only)

```
<any-project-root>/.ziya/
  project-hints.json    # Learned associations: this directory → these domain nodes
```

The project root is a **relevance prior**, not a memory container. When the user opens Ziya in `/workspace/leo-ground-v2`, the hints file tells the system "LEO Network domain is probably relevant here."

Hints are auto-maintained from observed activation patterns. They're small, optional, and can be regenerated from the global store's access history.

### 6.3 Why Not Project-Scoped Stores

A user may have three Ziya projects open for different branches of the same codebase, plus a simulation repo for the same system. Knowledge learned in any of these is relevant in all of them. Project-scoped storage traps knowledge.

Facts that are specific to a particular repo (build commands, file paths, branch names) are stored in the global flat store with `scope.project_paths` metadata that weights them higher when in that directory. They aren't jailed to it.

The boundary rule: **if the fact would be true in a different repo touching the same domain, it's global (no project scope). If it's only true in this repo, it gets project_paths metadata.**

### 6.4 Sharing Is Not a Storage Problem

Memory stores are personal and idiosyncratic. Sharing knowledge between team members is a **synthesis task**: ask the model to read the memory store and produce a clean briefing document. The memories are raw material; the extract is the deliverable.

---

## 7. Memory Schema

### 7.1 Flat Store Entry

```json
{
  "id": "m_003",
  "content": "Chose CCSDS-adjacent framing over pure IP encapsulation for the space segment. Rationale: OBP has 512MB RAM budget, full IP stack with routing tables exceeds memory. CCSDS frames are fixed-size, no fragmentation overhead.",
  "layer": "decision",
  "tags": ["framing", "OBP", "space-segment", "ccsds"],
  "learned_from": "design_discussion",
  "created": "2025-06-15",
  "last_accessed": "2025-07-10",
  "status": "active",
  "scope": {
    "domain_node": "leo-network",
    "project_paths": []
  },
  "related": ["m_004", "m_008"]
}
```

Fields:
- **id**: Stable unique identifier
- **content**: The memory itself — a distilled principle, not a raw transcript
- **layer**: One of: `domain_context`, `architecture`, `lexicon`, `decision`, `active_thread`, `process`, `preference`, `negative_constraint`
- **tags**: Free-form, used for search and cell-division detection
- **learned_from**: Provenance — `user_explanation`, `user_correction`, `design_discussion`, `design_failure`, `observation`
- **created / last_accessed**: Timestamps for staleness detection
- **status**: `active`, `pending`, `deprecated`, `archived`
- **scope.domain_node**: Which mind-map node this memory is most closely associated with
- **scope.project_paths**: Optional — directories where this fact is specifically relevant
- **related**: Cross-references to other memory IDs

### 7.2 Mind-Map Node

```json
{
  "id": "leo-fc",
  "handle": "Return link flow control — credit-based approach chosen over static allocation and NACK. Currently designing credit grant frame format.",
  "parent": "leo-network",
  "children": ["leo-fc-rejected", "leo-fc-credit-grant"],
  "cross_links": ["leo-obp-constraints"],
  "memory_refs": ["m_005"],
  "tags": ["flow-control", "return-link"],
  "access_count": 12,
  "last_accessed": "2025-07-14"
}
```

### 7.3 Project Hints

```json
{
  "project_path": "/workspace/leo-ground-v2",
  "learned_associations": {
    "high": ["leo-network", "leo-lexicon", "leo-arch"],
    "medium": ["org-process"],
    "low": []
  },
  "last_updated": "2025-07-14"
}
```

---

## 8. Tool Interface

The model interacts with memory through tools, like any other capability:

### memory_search

```
memory_search(query, tags?, layer?, limit?)
```
Scans the flat store by keyword/tag/layer match. Returns matching memories with IDs. For targeted lookup: "did we decide about X?", "what does FCTS stand for?"

### memory_context

```
memory_context(node_id?)
```
Returns the handle + children handles for a mind-map node. If node_id is omitted, returns root children (Level 0 overview). For progressive discovery and model self-routing.

### memory_expand

```
memory_expand(node_id)
```
Returns all memories attached to this node and its descendants. For going deep on a topic once the model knows it's relevant.

### memory_propose

```
memory_propose(content, tags, layer)
```
Agent proposes a new memory. Goes to a pending buffer. User sees an indicator ("3 pending memories") and can approve, edit, or dismiss at their convenience. Batch-proposed at natural conversation pauses.

### memory_save

```
memory_save(content, tags, layer, scope?)
```
Direct save — either user-initiated via `/remember` command, or approved from the pending buffer. Written to flat store immediately. Mind-map placement determined by tags and domain_node.

### /remember (User Command)

```
/remember the antenna team's phase 2 design constrains beam scheduling —
their new feed horn pattern creates asymmetric beams that break hexagonal tiling
```
User explicitly saves a memory. Agent classifies layer/tags and writes to flat store. Zero friction.

### /memory (User Command)

Shows memory status: store count by layer, domain overview, active threads, stale candidates. Entry point for review and editing.

---

## 9. Session Protocol

### 9.1 Context Loading

```
1. Load user profile (always — preferences, style)
2. Load Level 0 handles from global mindmap (~500 tokens)
   "Domains: LEO Network, Genetic Genealogy, Org Process..."
3. If project root has hints, foreground hinted domains
4. User speaks — model self-routes:
   a. Reads Level 0 handles, assesses relevance
   b. Calls memory_context() to expand relevant domain(s)
   c. Calls memory_expand() for topics conversation touches
   d. Relevant memories are now in context — model responds informed
5. As conversation shifts, model collapses old detail, expands new
```

### 9.2 Context Budget

Target: ~3,000-4,000 tokens for memory context. Allocation shifts dynamically:

| Component | Tokens | Persistence |
|-----------|--------|-------------|
| User profile | ~100 | Always loaded |
| Level 0 handles (all domains) | ~500 | Always loaded |
| Active domain handles (Level 1) | ~300 | Loaded when domain activates |
| Topic detail (Level 2+) | ~1500 | Loaded when topic is discussed |
| Project-specific facts | ~200 | Loaded when in matching project |
| Buffer / cross-links | ~400 | For expansion during conversation |

### 9.3 Memory Proposals

At natural conversation pauses (topic transitions, before tool calls, extended silence), the agent batches candidate memories:

> "Before we move on — should I save these?
> 1. RCTS = Return Channel Transport System (UT→sat→ground) — lexicon
> 2. OBP output ports use strict priority queuing — architecture
> 3. Decision: credit-based flow control over NACK — decision
> [Approve / Edit / Skip]"

Proposals are batched (3-5 at a time), not per-fact. The user can approve-all as the default action.

### 9.4 Cross-Domain Activation

When conversation bridges domains (e.g., satellite coverage patterns as analogy for population migration):

1. Both domain handles are in Level 0 (always available)
2. User's utterance activates multiple domains simultaneously
3. Model loads Level 1 handles from both branches
4. Model performs synthesis using its own reasoning — memory provides raw material
5. If synthesis produces a reusable insight, it gets proposed as a memory with cross-links to both domains

---

## 10. Phased Implementation

### Phase 0 — Flat Store Only (Minimum Viable Memory)

**What ships:**
- `~/.ziya/memory/memories.json` — user-global flat store
- `memory_search` and `memory_save` tools
- `/remember` command for explicit user saves
- Agent proposes memories at natural pauses (pending → approve flow)
- `/memory` command for status and review
- Level 0 = load entire flat store into context (fine up to ~50-80 memories)
- `project-hints.json` under project roots (auto-maintained)

**What doesn't exist yet:** Mind-map tree, progressive loading, cross-links, cell division.

**Why it's useful now:** The "pick up where we left off" case works. The agent knows domain vocabulary, key decisions, and active threads. The user stops re-explaining background. For a user with < 80 memories across all domains, the entire store fits in context without progressive loading.

### Phase 1 — Mind-Map and Progressive Loading

**What ships:**
- `mindmap.json` — tree structure with handles
- `memory_context` and `memory_expand` tools
- Model self-routing: Level 0 handles always loaded, depth on demand
- Manual organization: `/memory organize` lets user or agent restructure

**Why it matters:** Scales to hundreds of memories across multiple domains. A user working on 5 projects for 2 years can accumulate significant knowledge without context rot — only relevant depth loads.

### Phase 2 — Automatic Structure and Maintenance

**What ships:**
- Cell division detection (tag clustering, access correlation)
- Auto-generated handles (summarization of node contents)
- Cross-link discovery from traversal patterns
- Staleness detection and consolidation on retrieval
- `/memory review` for periodic cleanup prompts

**Why it matters:** The system maintains itself. The user doesn't have to manually organize or prune.

### Phase 3 — Cross-Domain Synthesis

**What ships:**
- Multi-domain simultaneous activation
- Synthesis memories with multi-domain cross-links
- Consolidation across domains (shared patterns extracted)

**Why it matters:** The system can bridge the user's knowledge across domains in ways the user might not have connected themselves.

---

## 11. Open Questions

### 11.1 Activation Accuracy

How precisely can the model identify which domains to activate from a few tokens of user input? Keyword matching works for unambiguous domains (satellite terms vs. genealogy terms) but breaks for common English. Model self-routing (letting the model reason over Level 0 handles) is the current best answer but needs validation.

### 11.2 Hard Cap on Active Memories

The ACC research says memory should be bounded. What's the right cap? If the context budget is ~4,000 tokens and average memory is ~40 tokens, that's ~100 simultaneously loaded memories. But the progressive loading model means we're not loading all memories at once. The cap may need to be per-node-expansion rather than global.

### 11.3 The Consolidation Prompt

When 5 related memories should be compressed into one better memory, what does that prompt look like? The consolidation step is conceptually clear but prompt-engineering it to produce faithful compressions without losing critical nuance is underexplored.

### 11.4 Cold Start

First session with a new domain: zero memories, no handles, no hints. The user does a brain dump. The agent needs to extract a project brief + initial memories from unstructured monologue. This is the initializer pattern from Anthropic's harness, but user-driven rather than autonomous.

### 11.5 Scope Ambiguity

When the user says something in a cross-project context, which domain should the resulting memory be filed under? The agent needs to classify not just what to remember but where it belongs. The tagging system helps, but the domain_node assignment may require user confirmation for ambiguous cases.
