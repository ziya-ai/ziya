# Ziya Context, Skill & Delegate Orchestration

## Overview

This document describes Ziya's context and skill management system and its
natural evolution into delegate orchestration: saved contexts (named file
groups), reusable skills (prompt templates), project organization, token
management, and the TaskPlan / MemoryCrystal system for parallel agentic tasks.

### What's Working

- **Projects**: Auto-created from cwd, switchable, dual-write synced
  between IndexedDB and server JSON files
- **Three-tab UI**: Files / Contexts / Chats tabs in `FolderTree.tsx`
- **Saved Contexts**: Create named file groups, activate/deactivate to
  control which files are sent with messages
- **Skills**: Built-in + custom prompt templates, activate to add
  system prompt instructions to all messages
- **ActiveContextBar**: Shows active contexts/skills as colored pills
  with token usage bar
- **Cross-tab sync**: Conversation list and folders sync via
  BroadcastChannel; file selections are per-tab (independent)
- **Chat history**: Folders, drag-drop, search, cross-tab sync,
  global (cross-project) conversations

### Deferred

- Per-chat context association (see Design Decisions)
- Folder-sticky default contexts (see Future Work)
- Global config persistence (`~/.ziya/config.json`)
- Schema migrations (`storage/migrations.py`)
- Automated tests for context/skill system

---

## Architecture

### Storage Layout

```
~/.ziya/
├── projects/
│   └── {project-uuid}/
│       ├── project.json
│       ├── contexts/
│       │   └── {context-uuid}.json
│       ├── skills/
│       │   └── {skill-uuid}.json
│       └── chats/
│           ├── _groups.json
│           └── {chat-uuid}.json
└── token_calibration.json
```

### Backend (Python/FastAPI)

```
app/
├── models/
│   ├── project.py          # Pydantic models
│   ├── context.py
│   ├── skill.py
│   ├── chat.py             # Extended: delegate_meta field
│   └── group.py            # Extended: task_plan field
├── storage/
│   ├── base.py             # BaseStorage with file locking
│   ├── projects.py         # ProjectStorage
│   ├── contexts.py         # ContextStorage
│   ├── skills.py           # SkillStorage
│   ├── chats.py            # ChatStorage
│   └── groups.py           # ChatGroupStorage
├── services/
│   ├── token_service.py    # Token counting
│   └── color_service.py    # Auto color generation
├── agents/                 # NEW — Delegate orchestration
│   ├── compaction_engine.py   # Completed delegate → MemoryCrystal
│   ├── delegate_manager.py    # Spawn, track, unblock, route feedback
│   └── orchestrator.py        # Task decomposition, convergence synthesis
├── api/
│   ├── projects.py         # /api/v1/projects/*
│   ├── contexts.py         # /api/v1/projects/{id}/contexts/*
│   ├── skills.py           # /api/v1/projects/{id}/skills/*
│   ├── chats.py            # /api/v1/projects/{id}/chats/*
│   ├── tokens.py           # /api/v1/projects/{id}/tokens/*
│   └── delegates.py        # NEW: /api/v1/projects/{id}/groups/{gid}/delegates/*
├── data/
│   └── built_in_skills.py  # Default skill definitions
└── middleware/
    └── project_context.py  # X-Project-Root header middleware
```

All routes registered in `server.py` (lines 885-889).

### Frontend (React/TypeScript)

```
frontend/src/
├── types/
│   ├── project.ts
│   ├── context.ts
│   ├── skill.ts
│   ├── token.ts
│   └── delegate.ts         # NEW: TaskPlan, DelegateSpec, MemoryCrystal types
├── api/
│   ├── index.ts
│   ├── projectApi.ts
│   ├── contextApi.ts
│   ├── skillApi.ts
│   ├── tokenApi.ts
│   ├── conversationSyncApi.ts
│   ├── folderSyncApi.ts
│   └── delegateApi.ts      # NEW: TaskPlan launch, delegate status, crystal fetch
├── context/
│   ├── ProjectContext.tsx   # Contexts, skills, token state
│   ├── ChatContext.tsx      # Conversations, folders, sync
│   └── FolderContext.tsx    # File tree, checkedKeys
├── components/
│   ├── FolderTree.tsx           # Three-tab container (Files/Contexts/Chats)
│   ├── ProjectSwitcher.tsx
│   ├── ActiveContextBar.tsx     # Active contexts/skills pills + token bar
│   ├── ContextsTab.tsx          # Browse & manage contexts/skills
│   ├── MUIChatHistory.tsx       # Chat history — TaskPlan folders get ⚡ icon + badges
│   ├── SendChatContainer.tsx    # Message input; "Launch Delegates" button appears here
│   └── ConversationGraph/       # Graph panel (Phase 0-A+)
│       ├── GraphPanel.tsx       # Blur-blend slide-out; switches mode for TaskPlans
│       └── ...
└── utils/
    ├── projectSync.ts      # BroadcastChannel cross-tab sync
    └── tabState.ts         # Per-tab sessionStorage
```

> **Dead code to remove**: `LeftPanel.tsx` and `ChatsTab.tsx` duplicate
> functionality already in `FolderTree.tsx`. They are never imported.

---

## Schemas

### Project

```typescript
interface Project {
  id: string;
  name: string;
  path: string;
  createdAt: number;
  lastAccessedAt: number;
  settings: {
    defaultContextIds: string[];
    defaultSkillIds: string[];
    writePolicy?: WritePolicy;
  };
}
```

### Context

```typescript
interface Context {
  id: string;
  name: string;
  files: string[];           // Relative paths from project root
  color: string;             // Auto-generated from name hash; used for delegate card color
  tokenCount: number;        // Cached
  tokenCountUpdatedAt: number;
  createdAt: number;
  lastUsedAt: number;
}
```

### Skill

```typescript
interface Skill {
  id: string;
  name: string;
  description: string;
  prompt: string;            // System prompt addition
  color: string;
  tokenCount: number;
  isBuiltIn: boolean;
  createdAt: number;
  lastUsedAt: number;
}
```

Built-in skills: Code Review, Debug Mode, Concise, Educational
(defined in `app/data/built_in_skills.py`).

A skill with only `prompt` set is a simple instruction preset (backward
compatible with existing skills). Enhanced skills can compose any
combination of instructions, file contexts, tool bindings, and model
overrides.

**Delegate roles:** Built-in skills map naturally to delegate roles.
When the orchestrator decomposes a task, it can assign a skill to each
delegate: "Code Review" skill → review delegate, "Debug Mode" skill →
bug-fixing delegate, "Educational" skill → documentation delegate.
Custom skills become custom delegate behaviors.

Future: Skills discovered from `.agents/skills/` directories in the
project root will surface with `source: 'project'`, compatible with the
[Agent Skills standard](https://agentskills.io/specification).

### Chat (Extended for Delegates)

```typescript
interface Chat {
  id: string;
  title: string;
  groupId: string | null;
  contextIds: string[];
  skillIds: string[];
  additionalFiles: string[];
  additionalPrompt: string | null;
  messages: Message[];
  createdAt: number;
  lastActiveAt: number;
  projectId?: string;
  isGlobal?: boolean;

  // Delegate fields — null for regular conversations
  delegateMeta?: DelegateMeta | null;
}

interface DelegateMeta {
  role: 'orchestrator' | 'delegate';
  planId: string;              // Parent TaskPlan folder ID
  delegateId?: string;         // Stable ID within the plan (e.g., "D1")
  delegateSpec?: DelegateSpec; // What this delegate was asked to do
  status: 'proposed' | 'ready' | 'running' | 'compacting' | 'completed' | 'failed' | 'blocked';
  crystal?: MemoryCrystal;     // Set when status = 'completed'
  contextId?: string;          // The auto-created Context scoping this delegate's files
  skillId?: string;            // The Skill applied to this delegate's role
}
```

### ChatGroup (Extended for TaskPlans)

```typescript
interface ChatGroup {
  id: string;
  name: string;
  projectId?: string;
  parentId: string | null;
  useGlobalContext: boolean;
  useGlobalModel: boolean;
  systemInstructions?: string;  // The overall task description (was deferred; now used)
  createdAt: number;
  updatedAt: number;
  isGlobal?: boolean;

  // TaskPlan fields — null for regular folders
  taskPlan?: TaskPlan | null;
}

interface TaskPlan {
  name: string;                       // Short task name (e.g., "Auth → OAuth2 Refactor")
  description: string;                // Full task description (mirrors systemInstructions)
  orchestratorId: string;             // Which conversation is the orchestrator
  delegateSpecs: DelegateSpec[];      // One per planned delegate
  crystals: MemoryCrystal[];          // Compacted results from completed delegates
  status: 'planning' | 'approved' | 'executing' | 'converging' | 'completed';
  taskGraph?: TaskGraph;              // Optional: serialized dependency DAG
  createdAt: number;
  completedAt?: number;
}

interface DelegateSpec {
  delegateId: string;            // Stable ID: "D1", "D2", etc.
  conversationId?: string;       // Set when conversation is created
  name: string;                  // "OAuth2 Provider Setup"
  emoji: string;                 // Visual identifier: "🔵", "💎" (updates to 💎 when crystal)
  scope: string;                 // What this delegate should do
  files: string[];               // Which files this delegate works on
  dependencies: string[];        // delegateIds that must complete first
  skillId?: string;              // Optional role (Code Review, Debug Mode, etc.)
  color: string;                 // From auto-generated Context color
}
```

### MemoryCrystal

The core unit of compacted delegate work. Stored in `DelegateMeta.crystal`.
Injected into downstream delegates' system prompts. Rendered as the
`full_context` of a `crystal` node in the graph.

```typescript
interface MemoryCrystal {
  delegateId: string;
  task: string;
  summary: string;               // 2-3 sentences, LLM-generated, ≤200 tokens
  filesChanged: FileChange[];    // Deterministically extracted from tool results
  decisions: string[];             // Deterministically extracted (decision-marker sentences)
  exports: Record<string, string>; // Symbols other delegates can reference
  toolStats: Record<string, number>; // { "file_write": 3, "shell": 2 }
  originalTokens: number;        // Before compaction
  crystalTokens: number;         // After (typically 300-500 tokens)
  createdAt: number;
  retroactiveReview?: 'preserved' | 'extended' | 'discarded';
}

interface FileChange {
  path: string;
  action: 'created' | 'modified' | 'deleted';
  lineDelta: string;             // "+48 -12" or "(new, 245 lines)"
}
```

### Swarm Budget (Extension of Token Calculation)

The existing token calculation endpoint returns per-selection costs.
For delegate swarms, the same endpoint is called per-delegate, and
the orchestrator tracks aggregate:

```typescript
interface SwarmBudget {
  modelLimit: number;          // e.g. 200000
  systemPromptTokens: number;
  orchestratorTokens: number;
  delegates: Record<string, DelegateBudget>;
  totalActive: number;
  totalFreed: number;          // Tokens freed by compaction
  headroom: number;            // modelLimit - totalActive
}

interface DelegateBudget {
  status: DelegateMeta['status'];
  activeTokens: number;        // Current tokens in this delegate's context
  originalTokens?: number;     // For crystals: tokens before compaction
  estimatedTokens?: number;    // For blocked delegates: projected usage
}
```

The `ActiveContextBar` in the main UI shows the swarm budget when a
TaskPlan is active — same component, extended data source.

### Conversation (Frontend/IndexedDB — unchanged)

```typescript
interface Conversation {
  id: string;
  projectId?: string;
  title: string;
  isGlobal?: boolean;
  messages: Message[];
  lastAccessedAt: number | null;
  hasUnreadResponse?: boolean;
  _version?: number;
  isActive: boolean;
  folderId?: string | null;
  displayMode?: 'raw' | 'pretty';
}
```

> Note: `delegateMeta` lives on the server-side `Chat` model and is synced
> to the frontend via the existing bulk-sync endpoint. The frontend
> `Conversation` type remains lean; delegate state is read from the server
> model when needed. This matches the existing pattern for `contextIds` / `skillIds`.

---

## State Management

### Three Context Providers (unchanged)

```
ProjectContext    → projects, contexts, skills, active selections, tokens
ChatContext       → conversations, folders, streaming, cross-tab sync
FolderContext     → file tree, checkedKeys, expandedKeys
```

### Data Flow: Sending a Message (unchanged)

```
SendChatContainer.handleSend()
    → checkedKeys from FolderContext
    → activeSkillPrompts from ProjectContext
    → sendPayload(..., activeSkillPrompts, ..., currentProject)
        → getApiResponse(..., activeSkillPrompts)
            → POST /api/chat with:
                - messages: conversation history
                - question: user's input
                - files: checkedKeys
                - systemPromptAddition: skill prompts
                - conversation_id
                - project_root
```

### Data Flow: Launching a TaskPlan (new)

```
User sends complex task description in any conversation
    → Orchestrator model responds with task decomposition
    → Orchestrator response includes a Mermaid task graph code block
    → "Launch Delegates" button appears below the message
        (same mechanism as existing "Apply Diff" / "Execute" buttons)

User clicks "Launch Delegates"
    → POST /api/v1/projects/{pid}/groups (create TaskPlan folder)
    → POST /api/v1/projects/{pid}/chats (create orchestrator conv in folder)
    → For each delegate spec:
        → POST /api/v1/projects/{pid}/contexts (create scoped Context)
        → POST /api/v1/projects/{pid}/chats (create delegate conv in folder)
    → DelegateManager.start_ready_delegates(plan_id)
        → For each delegate with no dependencies:
            → stream_with_tools() starts concurrently (true parallelism)
    → BroadcastChannel broadcasts 'folders-changed'
        → All tabs update sidebar; TaskPlan folder appears with ⚡ icon
    → Graph panel auto-opens (or ⚡ badge appears on toggle button)
```

### Data Flow: Delegate Completes → Crystal

```
stream_with_tools() generator exhausts
    → CompactionEngine.compact(conversation, delegate_meta) triggered
    → Phase A: deterministic extraction (zero LLM cost)
        → files_changed from file_write tool results
        → tool_stats from all tool invocations
        → decisions from decision-marker sentences in assistant messages
    → Phase B: LLM summary call (one cheap call, ≤200 token output)
    → Crystal stored on Chat.delegate_meta.crystal via PUT /api/v1/.../chats/{id}
    → DelegateManager receives crystal_ready event
        → Updates delegate status to 'completed'
        → Checks for blocked delegates whose dependencies are now all crystals
        → Starts newly unblocked delegates
    → Graph WebSocket pushes node status update to frontend
        → Node changes from pulsing blue to gem-glow green in graph panel
        → Folder sidebar badge updates (e.g., "2/4 ✓")
```

### Context ↔ File Tree Sync (unchanged)

Activation is one-directional: activating a context checks its files
in the tree. Deactivating does NOT uncheck files.

For delegates: each delegate's auto-created Context activates when you
navigate to that delegate's conversation, giving the correct file scope
for that delegate. When you navigate away, the delegate Context is not
auto-deactivated (consistent with the general one-directional policy).

### Token Calculation

```
Active state changes (contexts, skills, files, prompt)
    → Debounce 300ms
    → POST /api/v1/projects/{id}/tokens/calculate
    → Request: { files, contextIds, skillIds, additionalPrompt }
    → Response: { totalTokens, deduplicatedTokens, fileTokens, ... }
    → ActiveContextBar re-renders with updated count and bar

For TaskPlan swarm budget:
    → Same endpoint called per-delegate (parallel calls, cheap)
    → Orchestrator aggregates into SwarmBudget
    → ActiveContextBar extended to show swarm view when TaskPlan is active
```

### Per-Tab Isolation (unchanged)

Each browser tab is an independent workspace:

| State | Storage | Synced across tabs? |
|-------|---------|---------------------|
| `checkedKeys` (file selection) | `sessionStorage` | ❌ No |
| `expandedKeys` (tree state) | `sessionStorage` | ❌ No |
| `currentConversationId` | `sessionStorage` | ❌ No |
| `activeContextIds` | React `useState` | ❌ No |
| `activeSkillIds` | React `useState` | ❌ No |
| `additionalPrompt` | React `useState` | ❌ No |
| Conversation list | IndexedDB + server | ✅ BroadcastChannel |
| Folder structure | IndexedDB + server | ✅ BroadcastChannel |
| Streaming state | React + BroadcastChannel | ✅ Visual indicator only |
| TaskPlan delegate status | Server → BroadcastChannel | ✅ Via crystal_ready events |

**Delegate monitoring workflow**: Open the TaskPlan folder in the sidebar.
Click any delegate conversation to watch its live stream in the current tab.
Navigate to any other delegate to watch a different stream. The TaskPlan
folder's badge (e.g., "3/4 ✓") updates in all tabs as crystals form.

---

## Sidebar: TaskPlan Folder Appearance

TaskPlan folders use the existing folder display infrastructure with
additive visual hints — no new components required for Phase 2 baseline:

```
📁 ⚡ Auth → OAuth2 Refactor    [3/4 ✓]    ← ⚡ icon + progress badge
  💬 🎯 Orchestrator                        ← orchestrator conversation
  💬 💎 D1: OAuth Provider  ✓              ← crystal (green text)
  💬 🔵 D2: Token Mgmt      ⟳             ← running (blue + pulse badge)
  💬 🔵 D3: Test Suite      ⟳             ← running (unblocked by D1 crystal)
  💬 ⏳ D4: Documentation   ⟳             ← running (no deps)
```

When all delegates are complete:

```
📁 ✅ Auth → OAuth2 Refactor    [4/4 ✓]
  💬 🎯 Orchestrator            → synthesis ready
  💬 💎 D1: OAuth Provider  ✓
  💬 💎 D2: Token Mgmt      ✓
  💬 💎 D3: Test Suite      ✓
  💬 💎 D4: Documentation   ✓
```

The orchestrator conversation shows a "Synthesize Results" prompt
automatically when all crystals are available.

---

## API Endpoints

Base URL: `/api/v1`

### Projects, Contexts, Skills, Chats, Chat Groups, Tokens

All existing endpoints unchanged. See previous version for full list.

### Delegates (New — Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/projects/:pid/groups/:gid/launch` | Launch a TaskPlan (create all delegate convs, start ready ones) |
| GET | `/projects/:pid/groups/:gid/swarm-budget` | Get SwarmBudget for all delegates |
| GET | `/projects/:pid/groups/:gid/crystals` | Get all MemoryCrystals for a TaskPlan |
| POST | `/projects/:pid/chats/:id/crystal` | Manually trigger compaction on a completed delegate |
| POST | `/projects/:pid/chats/:id/reevaluate` | Retroactive crystal review given new directive |

### Graph (New — Phase 0-A, extended in Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects/:pid/chats/:cid/graph` | Conversation graph from message parsing |
| GET | `/projects/:pid/groups/:gid/delegate-graph` | Live delegate DAG from TaskPlan |
| WS | `/ws/delegate-graph/:gid` | WebSocket: push node status updates as delegates run |

---

## Sync Architecture

### Dual-Write (Current — unchanged)

```
Frontend action (create/update/delete conversation)
    → Write to IndexedDB (immediate, local)
    → POST to /api/v1/projects/{pid}/chats/bulk-sync (async)
    → Server writes to ~/.ziya/projects/{pid}/chats/{id}.json
```

### Cross-Tab Sync (Extended for Delegates)

```
Existing messages:
  conversations-changed, conversation-created, conversation-deleted,
  folders-changed, streaming-chunk, streaming-state, streaming-ended

New messages (Phase 2):
  delegate-status-changed   { planId, delegateId, status }
  crystal-ready             { planId, delegateId, crystal }
  delegate-conflict         { planId, delegateIds, file }
  task-plan-complete        { planId }
```

### Legacy Migration (unchanged)

`migrateUntaggedConversations` auto-tags conversations missing a
`projectId`. No separate migration step needed for delegate fields —
all new fields are optional and default to null.

---

## Design Decisions

### Per-Chat Context Association (Deferred)

The original plan called for storing `contextIds` and `skillIds` on
each `Conversation`, snapshotting on chat creation and restoring on
chat load. This was deferred because:

1. **Multi-tab solves it**: Different tasks → different tabs
2. **Matches industry UX**: VS Code Copilot, Cursor use global context per session
3. **Edge cases add complexity**: Deleted contexts, changed files, snapshot/restore failure modes

**For delegates:** Delegates get their own auto-created Context (scoped files)
and optional Skill (role). This is not a "restore on load" pattern — the
Context is permanently associated with the delegate's conversation via
`DelegateMeta.contextId` and is created fresh for each TaskPlan.

### Folder-Sticky Contexts → TaskPlan Folders

The `ConversationFolder.systemInstructions` field (already exists, was deferred)
is now used as the task description for TaskPlan folders. This is the "folder-sticky"
behavior that was deferred: a TaskPlan folder carries the task context that all
delegate conversations within it share. Implemented via the `TaskPlan.description`
field which mirrors `systemInstructions`.

### One-Directional Context → File Sync (unchanged)

Activating a context checks its files; deactivating does not uncheck them.
For delegates, the auto-created delegate Context activates when navigating
to a delegate's conversation. This is purely additive.

### Session-Ephemeral Selections (unchanged)

File selections and active contexts/skills don't survive browser restart.
Delegate status and crystals DO survive (stored server-side on Chat model).
Navigating to a completed delegate's conversation shows the crystal summary
and the delegate's full history regardless of session.

### True Parallelism Over Simulated Interleaving

Multiple `stream_with_tools` coroutines run concurrently via Python asyncio.
Total API cost is identical to sequential execution (same tokens, same work).
Wall-clock time is proportional to the longest delegate, not the sum.
Bedrock throttle risk is managed by the existing adaptive backoff in
`StreamingToolExecutor` — the DelegateManager adds a configurable max
concurrency cap (default: 4 simultaneous delegates) to stay below rate limits.

### Orchestrator Resolves Conflicts

File conflicts between delegates are resolved by the orchestrating thread,
not by the user. The orchestrator has full visibility into all delegate
scopes and can determine safe resolution strategies (e.g., delegate A writes
lines 1-50, delegate B writes lines 60-120 — no overlap, safe to merge).
Users are only asked when the orchestrator detects genuine directional
ambiguity (scope overlap, contradictory approaches, architectural decisions).

### Delegates Continue During Clarification

When a delegate detects ambiguity, it posts an `open_question` to the
orchestrator and continues working with its best-guess assumption. This
maximizes parallelism. If the user answers before the delegate finishes,
the delegate redirects. If after, the crystal undergoes retroactive review:
`preserved` (compatible), `extended` (needs additions), or `discarded`
(conflicts — respawn).

This gives users a "time travel" mental model: you can answer a question
late and the system figures out what (if anything) needs to be redone.
Earlier work is preserved by default, not discarded.

---

## Future Enhancements

In rough priority order:

1. **Phase 0-A graph visualization** — Foundation for delegate DAG view
2. **Phase 0-B status marking** — Manual annotation of conversation structure
3. **Phase 0-C inline questions** — Attach questions to graph nodes
4. **Phase 2 compaction engine** — The core technical moat (build after Phase 0)
5. **Phase 2 delegate manager** — Spawn, track, unblock delegates
6. **Swarm budget visualization** — Extend ActiveContextBar for TaskPlan view
7. **Folder-sticky contexts** — Default contexts/skills per folder
8. **Bidirectional context-file sync** — Auto-deactivate, show modified indicator
9. **Context file browser** — Expand context to see/edit individual files
10. **Keyboard shortcuts** — Cmd+K (quick switcher), Cmd+N (new chat)
11. **Per-chat context restore** — Store and restore active contexts on chat switch
12. **Global config persistence** — Theme, shortcuts, last project
13. **Automated tests** — Storage CRUD, context activation, cross-tab sync, compaction

---

## Color Generation (unchanged)

Contexts and skills get auto-assigned colors from a name hash.
Delegate Contexts use the same palette — each delegate has a consistent
color across sidebar icon, graph node, crystal pill, and file conflict display.

```python
PALETTE = ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981',
           '#f59e0b', '#ef4444', '#ec4899', '#6366f1']

def generate_color(name: str) -> str:
    hash_val = sum(ord(c) * (i + 1) for i, c in enumerate(name))
    return PALETTE[hash_val % len(PALETTE)]
```

---

## Open Questions

1. **Conflict resolution**: If same project open in multiple tabs and
   both modify folder structure simultaneously, last-write-wins via
   timestamp. No UI for conflict surfacing yet.

2. **Context staleness**: If files in a saved context are deleted from
   disk, the context still references them. No handling for this yet.

3. **Token limits**: No warning when active context approaches model's
   token limit. ActiveContextBar shows usage but no threshold warning.

4. **Delegate count cap**: What default max concurrency should the
   DelegateManager enforce? Too low wastes the parallelism benefit;
   too high risks Bedrock throttle. Recommend starting at 4, making
   it configurable in project settings.

5. **Crystal quality floor**: If a delegate's work is trivial (single
   file_write, <2000 tokens), is compaction worth the overhead? Add a
   minimum token threshold below which compaction is skipped and the
   conversation is retained as-is.

6. **Orchestrator decomposition quality**: The quality of task decomposition
   determines the quality of the entire delegate run. Poor decomposition
   (bad file scoping, missing dependencies) causes conflicts and rework.
   Should we invest in a "decomposition review" step where the user can
   edit the Mermaid graph before launching, or is best-effort decomposition
   with the existing conflict/retroactive-review system sufficient?
   Current answer: include review step (the "Launch Delegates" button
   is only shown after decomposition, giving user a chance to edit the
   Mermaid graph code block before approving).
