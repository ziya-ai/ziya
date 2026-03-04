# Design Document: Conversation Graph Tracker & Delegate Orchestration

**Project ID:** `clarification_system_v1`
**Status:** Phase 0-A — Implemented; Phase 2 Layer 0+1 — Implemented; Layer 2+ — Design Complete
**Author:** AI + User collaborative design session
**Last Updated:** 2025-01-21 (refined after cross-document alignment review)

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Vision](#vision)
3. [Agreed Principles](#agreed-principles)
4. [Architecture Overview](#architecture-overview)
5. [Data Model](#data-model)
6. [Phase Breakdown](#phase-breakdown)
7. [Phase 0-A: Detailed Implementation Plan](#phase-0-a-detailed-implementation-plan)
8. [Phase 0-B: Status Marking](#phase-0-b-status-marking)
9. [Phase 0-C: Inline Questions](#phase-0-c-inline-questions)
10. [Phase 2: Delegate Orchestration](#phase-2-delegate-orchestration)
11. [Known Limitations & Mitigations](#known-limitations--mitigations)
12. [Future Vision: Design Sessions & Canvas](#future-vision-design-sessions--canvas)
13. [Decisions Log](#decisions-log)
14. [Implementation Notes](#implementation-notes-phase-0-a-code-corrections)
15. [Open Questions](#open-questions)
16. [Task Dependencies](#task-dependencies)

---

## Problem Statement

When AI needs to gather multiple pieces of information from a user (system design,
requirements clarification, complex multi-step tasks), the current interaction model
fails in two ways:

1. **Sequential Q&A is too slow** — one question per round-trip cycle wastes time
2. **Bulk questions are overwhelming** — pages of text questions lose structure

More critically, **extended design conversations lose context over time**. Ideas get
proposed, refined, rejected, and revisited — but the linear chat format has no
mechanism to track which ideas are agreed upon, which are still being explored,
and which were abandoned. This was directly observed during the conversation that
produced this design document.

**Now a third problem has emerged:** As agentic capabilities mature, users need to
orchestrate multiple parallel AI delegate threads working on subtasks simultaneously.
Current tools (Cline, Codex CLI, Devin) force a choice between parallelism and
visibility — you either watch one thing, or you fire and forget many things.
Ziya should offer **parallel, visible, and steerable** orchestration.

### Meta-Observation

This design document itself was created because the conversation designing the
system was losing track of its own decisions. The conversation evolved through:

1. "Questionnaire" concept (interactive forms for clarification)
2. "Design Workspace" concept (live diagrams + questions)
3. "Canvas" vision (freeform 2D collaborative design space)
4. "Task Delegation" (subtasks with their own workspaces)
5. Recognition that we were losing track of all of the above

The solution: **Build the tracking tool first, then use it to design everything else.**

---

## Vision

### North Star (Long-term)

An organic, beautiful, flowing timeline visualization where:

- Conversations branch like taproots of a growing plant
- Every decision point is preserved with full context
- Tangential explorations fork from their heritage point
- Earlier branches remain visible to later work
- Users can revisit any decision point and its related discussions
- **Parallel delegate threads flow as parallel branches of the same river**
- **Completed delegates crystallize into dense green nodes — permanent, referenceable**
- **The graph IS the command center — no separate orchestration UI needed**
- The visualization is graphically beautiful with smooth curves
- It adapts to Ziya's theme (dark mode primary)
- It feels nothing like an IDE — preserves Ziya's conversational character

### Aesthetic Direction

- **Botanical/river delta hybrid** — organic curves, flowing lines
- NOT decorated botanically — the aesthetic is the curves itself
- Transit maps and git graphs represent relationships but NOT aesthetics
- Bezier curves for all connections
- Nodes as soft circles, sized by importance
- Color coding by status (green=agreed/crystal, blue=exploring/running,
  orange=question/blocked, gray=rejected/failed)
- Dark mode primary, adapts to Ziya's existing theme system
- Soft visual boundaries (blur-blend, not hard edges)
- **Delegate branches pulse gently while running — living, organic feel**
- **Crystal nodes (completed delegates) have a subtle gem-like inner glow**

### Immediate Goal

Stop losing information. Build a conversation structure tracker that:
1. Parses conversation history into a graph
2. Renders it as an organic flowing visualization
3. Lets users click any point to see full context
4. Persists graph state for later revisiting
5. **Evolves naturally into a delegate orchestration view** — same graph, new node types

### Why the Graph IS the Command Center

Every competitor building agentic tools has separate "orchestration UI" bolted
on top of their existing interface. This creates a jarring context switch.

In Ziya, the conversation graph tracker and delegate orchestration are the same
visual surface:
- A delegate **is** a branch in the conversation graph
- A completed delegate **is** a `crystal` node (green, agreed, dense)
- A dependency **is** an `implements` edge between nodes
- An open question that pauses a delegate **is** an `open_question` node
- The convergence synthesis **is** a `decision` node where branches merge

Users learn one mental model and it serves both use cases.

---

## Agreed Principles

These are LOCKED IN and inform all implementation decisions:

1. **No explicit submit buttons** — Everything auto-syncs via debouncing (2-3s idle)
2. **Visual representation first** — Diagrams/visualizations show AI's understanding
3. **Live updates** — Diagrams update as questions are answered
4. **Task decomposition native** — Complex designs naturally break into subtasks
5. **Organic aesthetics** — Flowing curves, not rigid trees/boxes
6. **NOT an IDE** — Preserve Ziya's conversational, non-IDE feel
7. **Canvas as north star** — Build incrementally toward 2D freeform workspace
8. **Temporal persistence** — Every branch point preserved with context
9. **Data model works for linear AND canvas** — Design once, render differently
10. **Visibility model** — Earlier branches visible to later work (unless truly separate conversations)
11. **Graph IS the orchestration surface** — No separate "command center" UI; the graph panel handles both
12. **Delegates as branches** — Parallel agent threads are graph branches with the same visual language
13. **Autocompaction is structural** — When a delegate completes, its branch crystallizes; the crystal is a graph node
14. **Orchestrator decides conflicts** — Conflict resolution happens in the orchestrating thread, not the user, except for genuine direction questions
15. **Background continuation** — Delegates continue working while clarification questions are outstanding; work can be retroactively preserved, extended, or discarded based on late answers

---

## Architecture Overview

### System Context

```
┌──────────────────────────────────────────────────────────────┐
│ Frontend (React)                                              │
│                                                               │
│  ┌──────────┐  ┌──────────────────────┐  ┌────────────────┐ │
│  │ Chat UI  │  │ Graph Panel          │  │ Node Detail    │ │
│  │ (exists) │  │ (blur-blend          │  │ (context view  │ │
│  │          │  │  slide-out)          │  │ + crystal view │ │
│  │          │  │                      │  │ + delegate ctl)│ │
│  │          │  │ For conversations:   │  │                │ │
│  │          │  │   organic tree       │  │                │ │
│  │          │  │ For TaskPlans:       │  │                │ │
│  │          │  │   delegate DAG       │  │                │ │
│  └──────────┘  └──────────┬───────────┘  └────────────────┘ │
│                           │                                   │
│                Conversation graph OR task graph              │
│                (same component, different data source)       │
└───────────────────────────┼──────────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────────┐
│ Backend (FastAPI)         │                                   │
│                           ▼                                   │
│  ┌──────────────────────────────────────┐                    │
│  │ Graph Routes (graph_routes.py)        │                    │
│  └──────────────────┬────────────────────┘                   │
│                     │                                         │
│  ┌──────────────────▼────────────────────┐                   │
│  │ Graph Manager                          │                   │
│  │  - Build graph from messages           │                   │
│  │  - Build graph from TaskPlan           │  ← NEW           │
│  │  - Cache in SQLite                     │                   │
│  │  - Serve to frontend                   │                   │
│  │  - Push live updates via WebSocket     │  ← NEW           │
│  └──────────────────┬────────────────────┘                   │
│                     │                                         │
│    ┌────────────────┴──────────────────┐                     │
│    │                                   │                     │
│  ┌─▼──────────────────┐  ┌────────────▼──────────────────┐  │
│  │ Graph Builder       │  │ Delegate Manager               │  │
│  │  - Parse messages   │  │  - Spawn delegate convs        │  │
│  │  - Extract ideas,   │  │  - Track dependency graph      │  │
│  │    decisions, etc.  │  │  - Start blocked delegates     │  │
│  │                     │  │    when crystals arrive        │  │
│  └─────────────────────┘  │  - Detect file conflicts       │  │
│                           │  - Route feedback per delegate │  │
│                           └───────────────────────────────┘  │
│                                                               │
│  ┌──────────────────────────────────────┐                    │
│  │ Compaction Engine (new)               │                    │
│  │  - Runs when delegate stream_with_    │                    │
│  │    tools exhausts                     │                    │
│  │  - Phase A: deterministic extraction  │                    │
│  │  - Phase B: LLM summary (1 cheap call)│                    │
│  │  - Stores MemoryCrystal on Chat       │                    │
│  │  - Notifies DelegateManager via event │                    │
│  └──────────────────────────────────────┘                    │
│                                                               │
│  ┌──────────────────────────────────────┐                    │
│  │ Chat Storage (existing)               │                    │
│  │  app/storage/chats.py                 │                    │
│  │  JSON files per chat in               │                    │
│  │  ~/.ziya/projects/{pid}/chats/        │                    │
│  └──────────────────────────────────────┘                    │
│                                                               │
│  ┌──────────────────────────────────────┐                    │
│  │ Graph Cache (new)                     │                    │
│  │  SQLite table: conversation_graphs    │                    │
│  │  ~/.ziya/conversation_graphs.db       │                    │
│  └──────────────────────────────────────┘                    │
└───────────────────────────────────────────────────────────────┘
```

### Key Integration Points

**Backend:**
- New plugin directory: `app/plugins/conversation_graph/`
- New route file: `app/routes/graph_routes.py`
- New agents directory: `app/agents/` (compaction_engine.py, delegate_manager.py, orchestrator.py)
- Reads from existing chat storage: `app/storage/chats.py` → `ChatStorage.get(chat_id)`
- Chat model: `app/models/chat.py` → `Chat` with `messages: List[Message]`
- Message model: `Message` has `id`, `role` (human/assistant/system), `content`, `timestamp`
- Route registration in `app/server.py` alongside existing routers (line ~923)

**Frontend:**
- New component directory: `frontend/src/components/ConversationGraph/`
- Integration point: `frontend/src/components/App.tsx` — alongside existing layout
- App uses Ant Design (`ConfigProvider`, `Button`, `Tooltip`)
- Theme context: `useTheme()` provides `isDarkMode` and `themeAlgorithm`
- Chat context: `useChatContext()` provides current conversation state
- Layout: `FolderTree` (left) + `chat-container` (center) + new GraphPanel (right)
- **The graph panel switches modes based on whether the active folder is a TaskPlan**

**API Contract:**
- Endpoint: `GET /api/v1/projects/{project_id}/chats/{chat_id}/graph`
- Query param: `?force_rebuild=true` to bypass cache
- Response: `{ nodes: [...], edges: [...], rootId: string, currentId: string }`
- **New endpoint:** `GET /api/v1/projects/{project_id}/groups/{group_id}/delegate-graph`
- **New WebSocket:** `/ws/delegate-graph/{group_id}` — live updates as delegates run

---

## Data Model

### ConversationNode

```python
@dataclass
class ConversationNode:
    id: str                          # Unique node ID (e.g., "node_42" or "delegate_d1")
    timestamp: float                 # When this was said/decided
    type: NodeType                   # See NodeType enum below
    content: str                     # Summary text (max ~60 chars for display)
    full_context: str                # Complete message content OR crystal summary
    author: str                      # 'user' | 'ai' | 'delegate' | 'orchestrator'
    
    # Graph structure
    parent_id: Optional[str]         # Where this branched from
    child_ids: List[str]             # What branches from this
    
    # Visual/state
    status: NodeStatus               # See NodeStatus enum below
    importance: float                # 0-1, affects visual weight (node size)
    tags: List[str]                  # Freeform tags
    
    # Rich content
    attachments: List[Dict]          # Diagrams, code, questions, crystal data
    linked_node_ids: List[str]       # Cross-references (not parent/child)
    
    # Delegate-specific fields (None for regular conversation nodes)
    branch_name: Optional[str]       # Delegate name ("D1: OAuth Provider")
    merged_into: Optional[str]       # Crystal merged into convergence node
    delegate_id: Optional[str]       # Reference to the delegate's conversation ID
    crystal: Optional[Dict]          # MemoryCrystal data when status=agreed
    delegate_color: Optional[str]    # Color from the delegate's auto-generated Context
    
    # Future-proofing (Phase 1+)
    visibility: str = 'global'       # For future scope control
```

### NodeType Enum

| Value | Description | Visual | Importance Base | Phase |
|-------|-------------|--------|----------------|-------|
| `root` | Conversation start | Large circle | 1.0 | 0-A |
| `idea` | General idea/concept | Medium circle | 0.5 | 0-A |
| `question` | Open question | Orange circle with ? | 0.6 | 0-A |
| `decision` | Agreed decision / convergence | Green circle with ✓ | 0.9 | 0-A |
| `task` | Actionable task | Blue circle | 0.8 | 0-A |
| `branch_point` | Where alternatives explored | Diamond/fork shape | 0.7 | 0-A |
| `orchestrator` | Orchestrating thread node | Large indigo circle | 1.0 | 2 |
| `delegate` | Running delegate thread | Pulsing blue circle | 0.8 | 2 |
| `crystal` | Completed/compacted delegate | Gem-glow green circle | 0.85 | 2 |
| `conflict` | File conflict between delegates | Red diamond | 0.75 | 2 |
| `clarification` | Question pausing a delegate | Orange circle (linked to delegate) | 0.65 | 2 |

### NodeStatus Enum

| Value | Color | Description | Phase |
|-------|-------|-------------|-------|
| `proposed` | Light blue (#91d5ff) | Idea mentioned, not yet evaluated | 0-A |
| `exploring` | Blue (#58a6ff) | Actively being discussed OR delegate running | 0-A / 2 |
| `agreed` | Green (#3fb950) | Confirmed, locked in OR delegate crystal | 0-A / 2 |
| `rejected` | Gray (#6e7681) | Explicitly rejected OR delegate cancelled | 0-A / 2 |
| `deferred` | Light gray (#d9d9d9) | Postponed OR delegate blocked on dependency | 0-A / 2 |
| `open_question` | Orange (#d29922) | Needs answer OR delegate awaiting clarification | 0-A / 2 |
| `running` | Animated blue (#58a6ff + pulse) | Delegate actively executing | 2 |
| `compacting` | Animated green (fading in) | Delegate done, crystal forming | 2 |
| `failed` | Red (#f85149) | Delegate errored or was abandoned | 2 |

The existing status color palette covers all delegate states — `exploring` maps
to running, `agreed` maps to crystal, `deferred` maps to blocked. The new
`running`, `compacting`, and `failed` values add animation states on top of
the existing colors.

### Edge Types

| Value | Visual | Description | Phase |
|-------|--------|-------------|-------|
| `continues` | Thick blue, animated | Main conversation flow | 0-A |
| `branches` | Thin gray | Alternative exploration | 0-A |
| `refines` | Dashed blue | Refinement of earlier idea | 0-A |
| `questions` | Dotted orange | Asks about previous node | 0-A |
| `implements` | Thin green | Task implementing an idea | 0-A |
| `spawns` | Thick indigo, dashed | Orchestrator spawning a delegate | 2 |
| `depends_on` | Amber arrow | Delegate blocked until source crystal exists | 2 |
| `injects` | Thin green, dashed | Crystal injected into downstream delegate | 2 |
| `conflicts` | Red double-headed | Two delegates touching the same files | 2 |

### Database Schema

```sql
-- New SQLite database: ~/.ziya/conversation_graphs.db

CREATE TABLE IF NOT EXISTS conversation_graphs (
    project_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    graph_data TEXT NOT NULL,
    last_updated INTEGER NOT NULL,
    version INTEGER DEFAULT 1,
    node_count INTEGER DEFAULT 0,
    PRIMARY KEY (project_id, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_graphs_updated
    ON conversation_graphs(last_updated);

-- Phase 0-B: manual status overrides (preserved across rebuilds)
CREATE TABLE IF NOT EXISTS node_status_overrides (
    project_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (project_id, chat_id, node_id)
);

-- Phase 0-C: inline questions attached to nodes
CREATE TABLE IF NOT EXISTS node_questions (
    question_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    question_type TEXT NOT NULL,  -- 'yes_no' | 'text' | 'choice'
    options TEXT,                  -- JSON array for choice type
    answer TEXT,
    answered_at INTEGER,
    created_at INTEGER NOT NULL
);

-- Phase 2: delegate graph (stored on group/folder, not chat)
-- This is the live task DAG; updated via WebSocket as delegates run
CREATE TABLE IF NOT EXISTS delegate_graphs (
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,       -- The TaskPlan folder ID
    graph_data TEXT NOT NULL,     -- Full ConversationGraph JSON
    last_updated INTEGER NOT NULL,
    PRIMARY KEY (project_id, group_id)
);
```

---

## Phase Breakdown

### Phase 0-A: Basic Graph Visualization (3-4 days)

**Goal:** Stop losing information — can view conversation structure

**Deliverables:**
- Parse conversation messages into graph nodes
- Render with react-flow using vertical organic layout
- Nodes show: idea summary, author, status color
- Edges show: conversation flow with bezier curves
- Click node → show full context in detail panel
- Blur-blend slide-out UI integration
- Database caching for graphs
- Toggle button with keyboard shortcut

**Success Criteria:**
- Can view a real conversation as flowing graph
- Can click any point and see what was discussed
- Visually pleasant in dark mode
- Panel slides in/out smoothly with blur effect

### Phase 0-B: Status Marking (2-3 days)

**Goal:** Annotate the graph — clarify what's agreed vs exploring

**Deliverables:**
- Right-click/long-press node → context menu with status options
- Visual updates (color change, icon) on status change
- Filter view by status (show only agreed, show only open questions, etc.)
- Persist status markings to database
- Status changes reflected immediately in graph

**Success Criteria:**
- Can mark decisions from a conversation
- Generate "Agreed Principles" list from marked nodes
- Clear visual distinction between all states

### Phase 0-C: Inline Questions (3-4 days)

**Goal:** Attach questions to nodes, answer inline

**Deliverables:**
- Nodes can have attached questions (yes/no, short text, choice)
- Click node → expand to show questions
- Answer inline (auto-saves via debounce)
- Answers visible in graph (visual indicator on node)
- Question nodes link to related content nodes

**Success Criteria:**
- Can create a question node attached to a decision
- Can answer inline without leaving graph view
- Decision updates graph structure and visual state

### Phase 1: Design Workspaces (2-3 weeks after Phase 0)

- Live-updating diagrams (mermaid/drawio) tied to question answers
- Auto-sync question panels (no submit buttons)
- Basic task tracking integrated into graph
- Workspace switching (click graph node → open its workspace)
- Designed USING the Phase 0 tool

### Phase 2: Delegate Orchestration (see full section below)

This is where the graph tracker evolves into the delegate command surface.
The data model is already designed to support this; Phase 2 adds the
`DelegateManager`, `CompactionEngine`, and the `build_from_task_plan()`
graph source alongside the existing message-parsing graph source.

---

## Phase 0-A: Detailed Implementation Plan

### Files to Create

```
app/plugins/conversation_graph/
├── __init__.py                    # Plugin registration
├── types.py                       # Data model (ConversationNode, ConversationGraph, enums)
├── graph_builder.py               # Parse messages → graph structure
└── graph_manager.py               # State management, caching, persistence

app/routes/
└── graph_routes.py                # API endpoint for graph data

frontend/src/components/ConversationGraph/
├── GraphView.tsx                  # React-flow graph renderer
├── GraphView.css                  # Dark theme organic styling
├── GraphPanel.tsx                 # Blur-blend slide-out container
├── GraphPanel.css                 # Slide animation, blur effects
├── CustomNode.tsx                 # Custom node component
├── DelegateNode.tsx               # Phase 2: delegate-specific node (crystal glow, pulse)
└── index.ts                       # Public exports
```

### Files to Modify

```
app/server.py                      # Register graph_routes router (~line 923)
frontend/src/components/App.tsx     # Add GraphPanel alongside chat-container
frontend/package.json               # Add reactflow dependency
```

### API Design

**Endpoint:** `GET /api/v1/projects/{project_id}/chats/{chat_id}/graph`

**Query Parameters:**
- `force_rebuild` (boolean, default false): Bypass cache and rebuild graph

**Response:**
```json
{
  "conversationId": "chat_abc123",
  "graphMode": "conversation",
  "nodes": [
    {
      "id": "node_1",
      "timestamp": 1705789200.0,
      "type": "root",
      "content": "Conversation start",
      "fullContext": "",
      "author": "system",
      "parentId": null,
      "childIds": ["node_2"],
      "status": "agreed",
      "importance": 1.0,
      "tags": [],
      "attachments": [],
      "linkedNodeIds": [],
      "delegateId": null,
      "crystal": null
    }
  ],
  "edges": [
    {
      "from": "node_1",
      "to": "node_2",
      "type": "continues"
    }
  ],
  "rootId": "node_1",
  "currentId": "node_15"
}
```

**Phase 2 additional endpoint:**
`GET /api/v1/projects/{project_id}/groups/{group_id}/delegate-graph`

Returns the same `ConversationGraph` structure but with `graphMode: "task_plan"`,
built from the TaskPlan's delegate specs and live statuses rather than parsing
chat messages. The frontend GraphPanel renders identically — it doesn't know or
care about the source.

### Graph Builder: Structure Extraction Strategy

**Phase 0-A approach:** Heuristic-based (regex patterns)

The builder processes each message and looks for:

1. **Task lists** — `- [ ] Task name` or `- [x] Completed task`
   - Creates TASK nodes with proposed/agreed status

2. **Structured questions** — Multiple lines ending with `?`
   - Only creates nodes if 2+ questions found (indicates structured Q&A)
   - Creates QUESTION nodes with open_question status

3. **Decisions** — Keywords: "agreed", "decided", "let's use", "confirmed", "✅"
   - Extracts the sentence containing the keyword
   - Creates DECISION nodes with agreed status and high importance

4. **Branch points** — Keywords: "alternatively", "or we could", "option A/B"
   - Creates BRANCH_POINT nodes with exploring status

5. **Default** — Messages with no specific structure
   - Creates IDEA nodes with first-sentence summary

**Phase 2 approach:** `build_from_task_plan()` — deterministic, no heuristics

```python
class GraphBuilder:
    def build_from_task_plan(self, folder: TaskPlanFolder,
                              delegate_convs: List[Chat]) -> ConversationGraph:
        """
        Build graph from a TaskPlan folder instead of parsing messages.
        Status is live — pulled directly from delegate conversation metadata.
        """
        nodes = []
        edges = []

        # Root = the task description
        nodes.append(ConversationNode(
            id='root',
            type='orchestrator',
            content=folder.task_plan['name'][:60],
            status='exploring',
            importance=1.0,
            author='orchestrator'
        ))

        # One node per delegate
        for spec in folder.task_plan['delegate_specs']:
            conv = next((c for c in delegate_convs if c.id == spec['conversation_id']), None)
            meta = conv.delegate_meta if conv else {}

            if meta.get('crystal'):
                status = 'agreed'        # completed → green crystal
            elif meta.get('status') == 'running':
                status = 'running'       # animated blue pulse
            elif meta.get('status') == 'compacting':
                status = 'compacting'    # fading green
            elif meta.get('status') == 'blocked':
                status = 'deferred'      # gray, waiting
            elif meta.get('status') == 'failed':
                status = 'failed'        # red
            else:
                status = 'proposed'      # planned, not yet started

            nodes.append(ConversationNode(
                id=spec['delegate_id'],
                type='crystal' if status == 'agreed' else 'delegate',
                content=spec['name'][:60],
                full_context=meta.get('crystal', {}).get('summary', ''),
                status=status,
                importance=0.8,
                delegate_id=conv.id if conv else None,
                crystal=meta.get('crystal'),
                delegate_color=spec.get('color'),
                author='delegate'
            ))

            # Dependency edges
            for dep_id in spec.get('dependencies', []):
                edges.append(Edge(from_id=dep_id, to_id=spec['delegate_id'],
                                  type='depends_on'))
                # When dep is complete, add injection edge
                dep_conv = next((c for c in delegate_convs if
                                 c.delegate_meta.get('delegate_id') == dep_id), None)
                if dep_conv and dep_conv.delegate_meta.get('crystal'):
                    edges.append(Edge(from_id=dep_id, to_id=spec['delegate_id'],
                                      type='injects'))
            if not spec.get('dependencies'):
                edges.append(Edge(from_id='root', to_id=spec['delegate_id'],
                                  type='spawns'))

        # Convergence node (once all delegates are crystals)
        all_done = all(
            n.status == 'agreed' for n in nodes if n.type in ('delegate', 'crystal')
        )
        nodes.append(ConversationNode(
            id='convergence',
            type='decision',
            content='Convergence & Review',
            status='agreed' if all_done else 'proposed',
            importance=0.9,
            author='orchestrator'
        ))

        return ConversationGraph(nodes=nodes, edges=edges,
                                  root_id='root', graph_mode='task_plan')
```

### Layout Algorithm

**Phase 0-A:** Vertical flow with branching

```
Root node centered at top
     │
     ▼
Single children: straight down (y += 120)
     │
     ├──────────────┐
     ▼              ▼
Multiple children: spread horizontally (280px spacing)
with gentle vertical offset (y += 150)
```

**Phase 2 delegate layout:** Radial from orchestrator

```
Orchestrator at center
    │ (spawns edges)
    ├── D1 (upper left, running)
    ├── D2 (upper right, running)
    ├── D3 (lower left, blocked — dimmed)
    └── D4 (lower right, running)
         ↓ (injects edges meet at convergence)
    Convergence (bottom center)
```

Delegates with dependencies are positioned further from center and
dimmer. As they unblock, they animate toward active position.

**Edge rendering:** `type: 'bezier'` for organic curves (NOT smoothstep which is angular)

### UI Integration: Blur-Blend Slide-Out Panel

**Behavior:**
- Panel slides from right edge of screen
- Smooth cubic-bezier animation (0.4s duration)
- Background behind panel gets subtle blur (2px) with gradient mask
- Gradient mask: fully visible on left, transparent near panel edge (soft boundary)
- Panel has frosted glass effect (backdrop-filter: blur(12px))
- Resizable via drag handle on left edge (300px min, 800px max)
- Toggle button: floating 🌳 button on right side (always visible)
- Keyboard shortcut: Ctrl+Shift+G (consistent with existing shortcuts)
- **Phase 2:** Toggle button shows 🌳 for conversations, ⚡ for active TaskPlans
- **Phase 2:** Panel header shows graph mode: "Conversation Graph" vs "Task Orchestration"

**Z-index stack:**
- 98: Toggle button
- 99: Blur overlay
- 100: Graph panel

**Integration with existing layout:**
- Panel overlays on top of chat area (does NOT push content)
- FolderTree (left sidebar) unaffected
- Chat container still functional underneath (clicking overlay closes panel)

### Keyboard Shortcut

```typescript
// In App.tsx, alongside existing keyboard handlers
useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
        // Ctrl+Shift+G — toggle graph panel
        if (e.ctrlKey && e.shiftKey && e.key === 'G') {
            e.preventDefault();
            setGraphPanelOpen(prev => !prev);
        }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
}, []);
```

---

## Phase 0-B: Status Marking

### Implementation Plan

**New functionality:**
- Context menu on node right-click/long-press
- Status options: Proposed, Exploring, Agreed, Rejected, Deferred, Open Question
- Visual transition animation when status changes
- Database persistence of status overrides
- Filter controls in graph header

**Database extension:** See `node_status_overrides` table in schema above.

Status overrides are stored separately from the auto-generated graph so
that rebuilding the graph preserves manual annotations.

---

## Phase 0-C: Inline Questions

### Implementation Plan

**New functionality:**
- Question attachment model (questions belong to nodes)
- Inline question UI within node detail panel
- Question types: yes/no, short text, single choice
- Auto-save answers via debounce (2s idle)
- Visual indicator on nodes with attached questions
- Answer persistence to database

**Database extension:** See `node_questions` table in schema above.

---

## Phase 2: Delegate Orchestration

### Overview

Phase 2 wires the delegate orchestration system to the graph tracker.
The graph panel becomes the live control surface for parallel agent threads.
No new visual language is introduced — existing node types and colors
handle everything.

### New Backend Components

```
app/agents/
├── compaction_engine.py      # Converts completed delegate conv → MemoryCrystal
├── delegate_manager.py       # Spawns delegates, tracks deps, routes feedback
└── orchestrator.py           # Task decomposition, crystal injection, convergence
```

### MemoryCrystal

The core unit of compacted delegate work. Stored as a JSON field on the
delegate's `Chat` model. Injected into downstream delegates' context windows.
Also rendered as the `full_context` field of a `crystal` node in the graph.

```python
@dataclass
class MemoryCrystal:
    delegate_id: str
    task: str
    summary: str                    # 2-3 sentences, LLM-generated, max 200 tokens
    files_changed: List[FileChange] # Deterministically extracted from tool results
    decisions: List[str]            # Deterministically extracted (decision-marker sentences)
    exports: Dict[str, str]         # Symbols/APIs other delegates can reference
    tool_stats: Dict[str, int]      # {"file_write": 3, "shell": 2, ...}
    original_tokens: int            # Before compaction
    crystal_tokens: int             # After compaction (typically 300-500 tokens)
    created_at: float
    retroactive_review: Optional[str]  # 'preserved' | 'extended' | 'discarded' | None

@dataclass
class FileChange:
    path: str
    action: str          # 'created' | 'modified' | 'deleted'
    line_delta: str      # e.g., "+48 -12" or "(new, 245 lines)"
```

### Compaction Engine

Triggered automatically when a delegate's `stream_with_tools` generator exhausts.

```
Phase A — Deterministic extraction (zero LLM cost):
  - Scan conversation for file_write / file_read tool results → files_changed
  - Count tool invocations by type → tool_stats
  - Extract decision-marker sentences → decisions
  - Extract exported symbols from file writes → exports

Phase B — LLM summary (one cheap call, ~200 token output):
  - Input: deterministic extractions + final assistant message
  - Constrained to 200 tokens
  - Output: 2-3 sentence summary of what was accomplished

Phase C — Storage and notification:
  - Serialize crystal as JSON
  - Store on Chat.delegate_meta['crystal']
  - Emit crystal_ready event to DelegateManager
  - DelegateManager starts any delegates that were waiting on this crystal
  - Graph WebSocket pushes node status update to frontend
```

### Delegate Lifecycle

```
proposed → ready → running → compacting → crystal (agreed)
                            ↓
                         failed (rejected)
                            ↓
                       open_question (deferred until answer)
```

**Go button flow:**

1. User describes complex task in orchestrator conversation
2. Orchestrator proposes decomposition as Mermaid task graph in chat
3. User reviews, optionally edits scope, clicks "Launch Delegates"
4. `DelegateManager.launch_plan()` executes:
   - Creates TaskPlan folder (a `ChatGroup` with `task_plan` metadata)
   - Creates orchestrator conversation in folder
   - Creates one `Chat` per delegate in folder with `delegate_meta`
   - Auto-creates a `Context` per delegate (scoped file selection)
   - Applies relevant `Skill` per delegate role if specified
   - Starts all delegates with no dependencies immediately
5. Graph panel auto-opens (or badge appears on 🌳 toggle button)
6. Each delegate streams live in its own conversation (clickable in sidebar)
7. As delegates complete → crystals form → blocked delegates unblock → auto-start

### Clarification and Retroactive Review

When a delegate detects ambiguity, it adds an `open_question` node to the graph
and posts the question to the orchestrator conversation. The delegate does NOT
pause unless the question is marked as blocking.

**Default behavior (non-blocking):**
- Delegate continues with its best-guess assumption
- Orchestrator conversation shows a question card
- If user answers before delegate finishes: delegate redirects mid-stream
- If user answers after delegate finishes: crystal undergoes retroactive review

**Retroactive review (Phase 2 "eventually" path):**

```python
async def reevaluate_crystal(crystal: MemoryCrystal, new_directive: str) -> str:
    """
    One cheap LLM call to evaluate whether completed work is
    compatible with a late-arriving directive.
    Returns: 'preserved' | 'extended' | 'discarded'
    """
```

- **preserved** — crystal is compatible, keep as-is, inject normally
- **extended** — crystal is mostly compatible, spawn a small extension delegate
- **discarded** — crystal conflicts, re-spawn the delegate with corrected scope

This gives users a "time travel" mental model: answer a question late,
and the system evaluates what (if anything) needs to be redone. Earlier
work is not wasted by default.

### Graph Panel in Task Plan Mode

When the active folder is a TaskPlan, the graph panel shows the
delegate DAG instead of the conversation tree. The toggle button
changes to ⚡. The panel header reads "Task Orchestration — [task name]".

Node interactions in task plan mode:

| Interaction | Result |
|---|---|
| Click delegate node | Opens that delegate's conversation in the chat area |
| Click crystal node | Shows crystal summary panel (files, decisions, exports) |
| Click conflict node | Shows conflict resolution panel |
| Click clarification node | Shows question + answer input |
| Right-click running delegate | Context menu: Pause, Send feedback, View stream, Cancel |
| Hover crystal node | Tooltip: "18,420 → 340 tokens (98% compaction)" |

---

## Known Limitations & Mitigations

### Phase 0-A Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Heuristic extraction can't detect topic branching within messages | Graph may be more linear than ideal | Phase 0-B adds manual correction; future LLM extraction |
| React-flow edges may not be organic enough | Aesthetic gap vs botanical vision | After 0-A works, prototype custom SVG direction |
| Simple recursive layout | May not handle complex graphs well | Iterate on layout algorithm; move to force-directed or custom |
| No real-time graph updates | Graph is built on-demand, not live | Phase 1: WebSocket updates as conversation progresses |
| Light theme support basic | Dark mode users fine, light mode may need work | Dark mode primary per user preference |

### Phase 2 Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Compaction LLM call adds latency | ~1-2s delay after delegate completes | Run compaction async; crystal appears after brief delay |
| Crystal quality depends on delegate behavior | Poorly structured delegates produce lower-quality summaries | Phase A deterministic extraction is always reliable; Phase B summary degrades gracefully |
| File conflict detection is write-based | Read conflicts not detected | Acceptable for Phase 2; Phase 3 can add semantic conflict analysis |

### Architecture Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Client-side DB (SQLite on server, conversations in browser) | Can't share graphs across machines | Future: server-side DB migration (Phase 0.5) |
| No multi-user support | Single user only | Future: server-side DB enables sharing |

---

## Future Vision: Design Sessions & Canvas

Phase 0 (this document) builds the foundation. The long-term vision includes:

### Phase 3: Multi-Track Layout (2 months after Phase 2)

- Parallel columns: Architecture | Requirements | Tasks | Chat
- Content can move between tracks
- All tracks update as user interacts
- Delegate streams visible as parallel columns in canvas mode

### Phase 4: True Canvas (4-6 months)

- Infinite 2D workspace (tldraw or custom)
- Freeform placement of diagrams, questions, tasks, notes
- AI places elements spatially, user can reorganize
- Task nodes are portals to sub-workspaces (delegate conversations)
- Crystal nodes are collapsed artifacts that expand in-place
- Zoom/pan for large system designs

### Phase 5: Collaborative Environment (1 year+)

- Multi-user (team design sessions)
- Version control (branch design decisions)
- Export to docs/PRDs/code scaffolds
- Import existing systems from codebase

### Data Model Compatibility

The Phase 0 data model is designed to support all future phases:

- `position` fields (x, y) — ignored in Phase 0, used in Phase 4 canvas
- `branch_name` / `merged_into` — used from Phase 2 for delegate names and convergence
- `visibility` — ignored in Phase 0, used in Phase 5 for access control
- `attachments` — used from Phase 0-C for questions, from Phase 2 for crystal data
- `linked_node_ids` — cross-delegate references from Phase 2
- `delegate_id`, `crystal`, `delegate_color` — Phase 2 fields, ignored by Phase 0 renderer

---

## Decisions Log

All decisions made during the design conversation:

| # | Decision | Alternatives Considered | Rationale |
|---|----------|------------------------|-----------|
| 1 | Phase 0 scope: Progressive A→B→C sprint | Big-bang implementation | Avoid fundamental rearchitecture; learn from each phase |
| 2 | React-flow for Phase 0-A | D3.js, Cytoscape.js, custom SVG | Fastest to working state; pivot to custom SVG after |
| 3 | Structure extraction: Start manual, evolve to heuristic, enhance with LLM | LLM-only, manual-only | Hybrid approach balances speed and accuracy |
| 4 | UI: Blur-blend slide-out panel | Side panel, separate tab, split view, full screen | Soft boundary preserves non-IDE feel |
| 5 | Persistence: Client-side SQLite | Server-side DB, file-based, in-memory | Fast to implement; server migration planned for Phase 0.5 |
| 6 | Aesthetic: Botanical/river delta with bezier curves | Transit map, neural network, rigid tree | Organic flow matches user's vision; NOT specifically decorated |
| 7 | Dark mode primary | Light mode primary, both equal | User's preference; adapt to Ziya's theme system |
| 8 | Edge type: bezier (not smoothstep) | smoothstep, straight | Organic curves are non-negotiable per aesthetic direction |
| 9 | No IDE-style tree hierarchies | VS Code explorer, Jira board, kanban | User explicitly rejected IDE patterns; preserve conversational feel |
| 10 | Bootstrap strategy: Use tool to design itself | Traditional waterfall, external tools | Dog-fooding validates utility; each phase designs the next |
| 11 | Graph IS the delegate command surface | Separate "command center" UI | One mental model; delegates as branches is natural and beautiful |
| 12 | Autocompaction on stream exhaustion | Manual trigger, scheduled job, user-triggered | Zero friction; the system manages its own context budget |
| 13 | Orchestrator resolves conflicts | Human resolves all conflicts | Industry direction; human only asked for genuine direction questions |
| 14 | Delegates continue during clarification | Pause all delegates for any question | Maximizes parallelism; retroactive review handles late answers |
| 15 | Crystal retroactive review: preserve/extend/discard | Always discard and redo | Conserves work; most completed work survives late clarifications |
| 16 | True parallelism (multiple simultaneous API streams) | Simulated parallelism with interleaving | Same total cost, faster wall time; existing asyncio supports it |
| 17 | Delegates as conversations in a TaskPlan folder | Separate delegate storage model | Reuses all existing conversation/folder infrastructure with zero new UI |

---

## Implementation Notes (Phase 0-A Code Corrections)

These corrections apply to the initial code drafts from the design conversation
and MUST be incorporated when writing actual implementation code:

### Backend Corrections

1. **API path**: `/api/v1/projects/{project_id}/chats/{chat_id}/graph`
   (NOT `/api/conversation-graph/{conversation_id}`)

2. **Data access**: Use `ChatStorage.get(chat_id)` from `app/storage/chats.py`
   (NOT a nonexistent `get_conversation_from_db` function)

3. **Message field**: The `Message` model uses `role` field
   (`'human'`/`'assistant'`/`'system'`), NOT `type`. Graph builder must read
   `msg.role` not `msg.get('type')`.

4. **DB path**: `~/.ziya/conversation_graphs.db` (separate from main storage)

5. **DB key**: Primary key is `(project_id, chat_id)`, not just `conversation_id`.
   All manager methods need both `project_id` and `chat_id`.

6. **Schema completeness**: Create ALL 4 tables at startup (conversation_graphs,
   node_status_overrides, node_questions, delegate_graphs) even though Phase 0-A
   only uses the first. Avoids migration headaches later.

7. **node_count field**: Populate `node_count` in `conversation_graphs` table when
   saving (it's in the schema but easy to miss).

### Data Model Completeness

8. **ConversationNode**: Include ALL fields from the design even though Phase 0-A
   only uses a subset. Delegate fields (`branch_name`, `merged_into`, `delegate_id`,
   `crystal`, `delegate_color`) default to None. `visibility` defaults to `'global'`.

9. **Enums**: Include ALL values in NodeType, NodeStatus, EdgeType enums even though
   Phase 0-A only renders a subset. This prevents model changes when Phase 2 begins.

10. **ConversationGraph**: Include `graph_mode` field (`"conversation"` for message-
    parsed graphs, `"task_plan"` for delegate DAGs).

### Frontend Corrections

11. **Edge type**: MUST use `type: 'bezier'` (organic curves), NOT `'smoothstep'`
    (angular paths). This is a locked-in aesthetic requirement.

12. **to_dict() serialization**: Must include all delegate fields even when None,
    so the frontend TypeScript types don't need conditional handling.

### Cross-Document References

- **newux-context.md** is the canonical source for: DelegateMeta, DelegateSpec,
  TaskPlan, MemoryCrystal, SwarmBudget, ChatGroup extensions
- **This document** is the canonical source for: ConversationNode, ConversationGraph,
  NodeType/Status/EdgeType enums, graph_builder logic, graph panel UI behavior
- **app/models/chat.py** is the ground truth for the Message model (role field,
  content field, timestamp as int)

---

## Open Questions

### Lower Priority (Don't Block Phase 0-A)

1. **Custom SVG aesthetic** — Will prototype after 0-A working. React-flow is
   stepping stone; need to validate the botanical/river-delta curves feel right
   before investing in custom rendering.

2. **Automatic structure extraction quality** — Heuristic builder is known-rough.
   Will refine patterns as we see real graphs. LLM extraction budget TBD based
   on context caching costs.

3. **Phase 1 scope** — Will be designed using Phase 0-C tool. Not defined yet
   intentionally.

4. **Canvas library choice** — tldraw vs custom SVG/canvas. Deferred until
   Phase 3+ when we understand spatial interaction patterns better.

5. **Graph performance** — Large conversations (100+ messages) may produce
   graphs with many nodes. May need pagination, filtering, or level-of-detail
   rendering. Will measure after Phase 0-A.

6. **Delegate count limits** — How many parallel delegates can run before
   Bedrock throttle becomes noticeable? Empirical testing needed. The
   DelegateManager should have a configurable concurrency cap with a default.

7. **Crystal quality floor** — What's the minimum acceptable crystal? If a
   delegate's work is trivial (single file_write), the crystal may be smaller
   than the overhead of compaction. Add a minimum token threshold below which
   compaction is skipped.

---

## Task Dependencies

### Phase 0-A Tasks (Ordered)

```
T1: Create plugin directory structure + types.py
    └─ Defines all data models used by subsequent tasks
    └─ No dependencies

T2: Implement graph_builder.py
    └─ Depends on: T1 (needs types)
    └─ Reads from: app/models/chat.py (Message model)
    └─ Heuristic extraction of structure from messages

T3: Implement graph_manager.py
    └─ Depends on: T1 (types), T2 (builder)
    └─ Creates SQLite database and schema
    └─ Caches built graphs

T4: Implement graph_routes.py
    └─ Depends on: T3 (manager)
    └─ Reads from: app/storage/chats.py (ChatStorage)
    └─ Needs project_id and chat_id to load messages

T5: Register routes in server.py
    └─ Depends on: T4 (routes exist)
    └─ Single line addition alongside existing routers

T6: Install reactflow dependency
    └─ No dependencies
    └─ `npm install reactflow` in frontend/

T7: Create GraphView.tsx + GraphView.css
    └─ Depends on: T6 (reactflow installed)
    └─ Custom node component (circles, status colors)
    └─ Bezier edges for organic curves
    └─ Dark theme styling

T8: Create GraphPanel.tsx + GraphPanel.css
    └─ Depends on: T7 (GraphView exists)
    └─ Blur-blend slide-out behavior
    └─ Resize handle
    └─ Toggle button

T9: Integrate into App.tsx
    └─ Depends on: T8 (GraphPanel exists)
    └─ Add GraphPanel alongside chat-container
    └─ Add Ctrl+Shift+G keyboard shortcut
    └─ Wire up conversationId from chat context
    └─ Detect TaskPlan folders → switch to delegate graph mode

T10: End-to-end testing
    └─ Depends on: All above
    └─ Test with real conversation
    └─ Verify graph renders correctly
    └─ Test panel slide animation
    └─ Test node click → detail view
```

**Critical path:** T1 → T2 → T3 → T4 → T5 (backend) + T6 → T7 → T8 → T9 (frontend)

Backend and frontend can be developed in parallel after T1 is complete.

### Phase 2 Tasks (After Phase 0 Complete, detailed in newux-context.md)

Priority reordering (2025-03-04): Sidebar-first strategy. Users need to see
and interact with delegates from the existing chat list before the graph panel
is polished. The graph panel (T36-T38) is deferred, not cancelled — it adds
value once the sidebar workflow is solid.

**Layer 0-1: Data + Compaction (Done)**
```
T20: ✅ Define MemoryCrystal + DelegateSpec data models (Layer 0)
    └─ DONE: app/models/delegate.py — 7 models, backward-compat tested
T21: ✅ Extend Chat + ChatGroup models with delegate_meta + task_plan fields
    └─ DONE: app/models/chat.py (delegateMeta), app/models/group.py (taskPlan, systemInstructions, updatedAt)
T22: ✅ Implement CompactionEngine (Layer 1) — the core technical moat
    └─ DONE: app/agents/compaction_engine.py — Phase A deterministic + Phase B LLM with fallback
    └─ KNOWN ISSUE: LLM summary needs RetryingChatBedrock unwrap fix (fallback works)
T23: ✅ Wire compaction to stream_with_tools completion event
    └─ DONE: Hook in streaming_tool_executor.py after iteration loop, before FINAL REPORT
    └─ DONE: LLM wrapper fix in compaction_engine.py (_call_summary_model unwraps RetryingChatBedrock)
    └─ DONE: 8 tests in tests/test_compaction_hook.py
```

**Layer 2: Orchestration Engine (Backend)**
```
T24: ✅ Implement DelegateManager (Layer 2) — launch_plan, dependency tracking
    └─ DONE: app/agents/delegate_manager.py — launch_plan, crystal cascading,
       concurrency semaphore, upstream crystal injection, progress callback
    └─ DONE: 18 tests in tests/test_delegate_manager.py
```

**Layer 3: Sidebar-First UI (the user-facing surface)**

Users click into delegates like any other chat. The sidebar IS the command
center — no graph panel needed for the core workflow.

```
T25: Sidebar delegate status display (MUIChatHistory augmentation)
    └─ Depends on: T24 (DelegateManager exists, Chat.delegateMeta populated)
    └─ TaskPlan folders show ⚡ icon + "3/4" progress badge
    └─ Delegate chats show status: 💎✓ crystal, 🔵⟳ running, ⏳ waiting
    └─ Orchestrator chats show 🎯 marker
    └─ All driven from Chat.delegateMeta — no new WebSocket needed
    └─ Indigo left border on TaskPlan folders to distinguish from regular

T26: Delegate API routes — launch, status, cancel
    └─ Depends on: T24
    └─ POST /api/v1/projects/{pid}/groups/{gid}/launch-delegates
    └─ GET  /api/v1/projects/{pid}/groups/{gid}/delegate-status
    └─ POST /api/v1/projects/{pid}/groups/{gid}/cancel-delegates
    └─ Wires DelegateManager to HTTP layer

T27: "Launch Delegates" button in chat
    └─ Depends on: T25 (sidebar shows results), T26 (API exists)
    └─ When model produces task decomposition (Mermaid graph block),
       show "Launch Delegates" button below the message
    └─ Button calls POST .../launch-delegates
    └─ Sidebar updates via existing BroadcastChannel

T28: Live status updates in sidebar via BroadcastChannel
    └─ Depends on: T25, T26
    └─ Crystal completion triggers 'conversations-changed' broadcast
    └─ Sidebar re-renders with updated badge counts
    └─ No new WebSocket — piggybacks on existing sync infrastructure

T29: Implement task decomposition prompt in orchestrator
    └─ Depends on: T27 (button exists to act on decomposition)
    └─ Orchestrator skill prompt guides model to produce DelegateSpec list
    └─ Model output includes Mermaid task graph for visual review
    └─ User can edit scope before launching
```

**Layer 4: Graph Panel Integration (deferred, not cancelled)**

These tasks add the botanical/river-delta graph visualization for TaskPlans.
The sidebar workflow must be solid first. Graph panel is supplementary.

```
T36: Add build_from_task_plan() to GraphBuilder
    └─ Depends on: T24 (DelegateManager), T25 (sidebar working)
    └─ Converts TaskPlan delegate DAG into graph nodes/edges
    └─ Reuses existing ConversationNode types with delegate status mapping

T37: Add DelegateNode + CrystalNode custom components to GraphPanel
    └─ Depends on: T36
    └─ Pulsing blue nodes for running delegates
    └─ Gem-glow green nodes for completed crystals
    └─ Click delegate node → opens that delegate's chat

T38: Add delegate WebSocket for live graph updates
    └─ Depends on: T37
    └─ WebSocket pushes node status changes to GraphPanel
    └─ Graph panel auto-opens (or ⚡ badge on 🌳 toggle) when TaskPlan active

T39: Retroactive crystal review logic
    └─ Depends on: T28 (sidebar workflow complete)
    └─ Late-arriving answers evaluated against completed crystals
    └─ Returns: 'preserved' | 'extended' | 'discarded'
```

---

## Appendix: Bootstrap Strategy

The implementation strategy is explicitly recursive:

1. **Build Phase 0-A** — Conversation graph tracker
2. **Use Phase 0-A** to view the design conversation structure
3. **Use Phase 0-B** to mark which decisions are agreed/open
4. **Use Phase 0-C** to create questions for Phase 1 design
5. **Phase 1** is designed and tracked using Phase 0 tools
6. **Phase 2** (delegates) — the graph tracker evolves into the orchestration surface;
   the tool is used to track its own delegate execution as it builds itself
7. **Each subsequent phase** is designed using all tools built so far

This ensures:
- Every tool is immediately useful
- We learn from using each tool before designing the next
- Nothing gets lost because we're tracking with the tools themselves
- The tools improve through real-world usage
