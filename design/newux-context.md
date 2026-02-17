# Ziya Context & Skill Management

## Overview

This document describes Ziya's context and skill management system:
saved contexts (named file groups), reusable skills (prompt templates),
project organization, and token management.

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
│   ├── chat.py
│   └── group.py
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
├── api/
│   ├── projects.py         # /api/v1/projects/*
│   ├── contexts.py         # /api/v1/projects/{id}/contexts/*
│   ├── skills.py           # /api/v1/projects/{id}/skills/*
│   ├── chats.py            # /api/v1/projects/{id}/chats/*
│   └── tokens.py           # /api/v1/projects/{id}/tokens/*
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
│   └── token.ts
├── api/
│   ├── index.ts             # Base API client
│   ├── projectApi.ts
│   ├── contextApi.ts
│   ├── skillApi.ts
│   ├── tokenApi.ts
│   ├── conversationSyncApi.ts  # IndexedDB ↔ server sync
│   └── folderSyncApi.ts
├── context/
│   ├── ProjectContext.tsx   # Contexts, skills, token state
│   ├── ChatContext.tsx      # Conversations, folders, sync
│   └── FolderContext.tsx    # File tree, checkedKeys
├── components/
│   ├── FolderTree.tsx       # Three-tab container (Files/Contexts/Chats)
│   ├── ProjectSwitcher.tsx  # Project dropdown
│   ├── ActiveContextBar.tsx # Active contexts/skills pills + token bar
│   ├── ContextsTab.tsx      # Browse & manage contexts/skills
│   ├── MUIChatHistory.tsx   # Chat history with folders & drag-drop
│   └── SendChatContainer.tsx # Message input with skill prompt wiring
└── utils/
    ├── projectSync.ts      # BroadcastChannel cross-tab sync
    └── tabState.ts          # Per-tab sessionStorage
```

> **Dead code to remove**: `LeftPanel.tsx` and `ChatsTab.tsx` duplicate
> functionality already in `FolderTree.tsx`. They are never imported.

---

## Schemas

### Project

```typescript
interface Project {
  id: string;
  name: string;              // Default: directory basename
  path: string;              // Absolute path to working directory
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
  color: string;             // Auto-generated from name hash
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

Future: Skills discovered from `.agents/skills/` directories in the
project root will surface with `source: 'project'`, compatible with the
[Agent Skills standard](https://agentskills.io/specification).

### Chat

```typescript
interface Chat {
  id: string;
  title: string;
  groupId: string | null;
  contextIds: string[];       // Stored but NOT used for restore (see Design Decisions)
  skillIds: string[];         // Stored but NOT used for restore
  additionalFiles: string[];
  additionalPrompt: string | null;
  messages: Message[];
  createdAt: number;
  lastActiveAt: number;
  projectId?: string;
  isGlobal?: boolean;
}
```

### Conversation (Frontend/IndexedDB)

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

> Note: The frontend `Conversation` type does NOT have `contextIds`,
> `skillIds`, or `additionalPrompt`. The server-side `Chat` model has
> these fields but they are not read back on chat load. This is
> intentional — see Design Decisions.

---

## State Management

### Three Context Providers

```
ProjectContext    → projects, contexts, skills, active selections, tokens
ChatContext       → conversations, folders, streaming, cross-tab sync
FolderContext     → file tree, checkedKeys, expandedKeys
```

### Data Flow: Sending a Message

```
SendChatContainer.handleSend()
    → checkedKeys from FolderContext (file selection)
    → activeSkillPrompts from ProjectContext (skill prompts)
    → sendPayload(..., activeSkillPrompts, ..., currentProject)
        → getApiResponse(..., activeSkillPrompts)
            → POST /api/chat with:
                - messages: conversation history
                - question: user's input
                - files: checkedKeys (file paths)
                - systemPromptAddition: skill prompts
                - conversation_id
                - project_root
```

### Context ↔ File Tree Sync

Activation is **one-directional**: activating a context checks its files
in the tree. The reverse (unchecking files auto-deactivates a context)
is not implemented — users deactivate contexts explicitly.

```
User activates context in ContextsTab
    → ProjectContext.addContextToLens()
    → Dispatches 'addFilesToSelection' CustomEvent
    → FolderContext listener adds files to checkedKeys

User deactivates context via ActiveContextBar ×
    → ProjectContext.removeContextFromLens()
    → Files remain checked (user can uncheck manually)
```

### Token Calculation

```
Active state changes (contexts, skills, files, prompt)
    → Debounce 300ms
    → POST /api/v1/projects/{id}/tokens/calculate
    → Request: { files, contextIds, skillIds, additionalPrompt }
    → Response: { totalTokens, deduplicatedTokens, fileTokens, ... }
    → ActiveContextBar re-renders with updated count and bar
```

### Per-Tab Isolation

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

**Parallel task workflow**: Open multiple browser tabs in the same
project. Each tab selects different files and activates different
contexts/skills independently. Conversation list and folder changes
sync automatically.

**Session persistence**: File selections and active contexts/skills
do not survive browser restart. Users restore their working set by
activating a saved Context (one click). This avoids stale state.

---

## API Endpoints

Base URL: `/api/v1`

### Projects

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects` | List all projects |
| GET | `/projects/current` | Get/create project for cwd |
| POST | `/projects` | Create project |
| GET | `/projects/:id` | Get project |
| PUT | `/projects/:id` | Update project |
| DELETE | `/projects/:id` | Delete project and all data |

### Contexts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects/:pid/contexts` | List contexts |
| POST | `/projects/:pid/contexts` | Create context (auto-color, auto-tokens) |
| GET | `/projects/:pid/contexts/:id` | Get context |
| PUT | `/projects/:pid/contexts/:id` | Update (recalculates tokens if files changed) |
| DELETE | `/projects/:pid/contexts/:id` | Delete context |

### Skills

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects/:pid/skills` | List skills (includes built-ins) |
| POST | `/projects/:pid/skills` | Create custom skill |
| GET | `/projects/:pid/skills/:id` | Get skill |
| PUT | `/projects/:pid/skills/:id` | Update (cannot update built-ins) |
| DELETE | `/projects/:pid/skills/:id` | Delete (cannot delete built-ins) |

### Chats

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects/:pid/chats` | List chats (summaries by default) |
| POST | `/projects/:pid/chats` | Create chat |
| POST | `/projects/:pid/chats/bulk-sync` | Bulk upsert from frontend |
| GET | `/projects/:pid/chats/:id` | Get chat with messages |
| PUT | `/projects/:pid/chats/:id` | Update chat metadata |
| DELETE | `/projects/:pid/chats/:id` | Delete chat |
| POST | `/projects/:pid/chats/:id/messages` | Add message |

### Chat Groups

| Method | Path | Description |
|--------|------|-------------|
| GET | `/projects/:pid/chat-groups` | List groups |
| POST | `/projects/:pid/chat-groups` | Create group |
| POST | `/projects/:pid/chat-groups/bulk-sync` | Bulk upsert from frontend |
| PUT | `/projects/:pid/chat-groups/:id` | Update group |
| DELETE | `/projects/:pid/chat-groups/:id` | Delete (chats become ungrouped) |
| PUT | `/projects/:pid/chat-groups/reorder` | Reorder groups |

### Tokens

| Method | Path | Description |
|--------|------|-------------|
| POST | `/projects/:pid/tokens/calculate` | Calculate tokens for a selection |

Request:
```json
{
  "files": ["src/server.py"],
  "contextIds": ["uuid-1"],
  "skillIds": ["uuid-2"],
  "additionalPrompt": "Be concise"
}
```

Response:
```json
{
  "totalTokens": 12500,
  "deduplicatedTokens": 11200,
  "fileTokens": { "src/server.py": 3400 },
  "skillTokens": { "uuid-2": 150 },
  "additionalPromptTokens": 25,
  "overlappingFiles": ["src/server.py"]
}
```

---

## Sync Architecture

### Dual-Write (Current)

```
Frontend action (create/update/delete conversation)
    → Write to IndexedDB (immediate, local)
    → POST to /api/v1/projects/{pid}/chats/bulk-sync (async)
    → Server writes to ~/.ziya/projects/{pid}/chats/{id}.json
```

Both directions use version timestamps to resolve conflicts
(last-write-wins with `_version` or `lastActiveAt`).

### Cross-Tab Sync

```
Tab A modifies conversation
    → Saves to IndexedDB
    → projectSync.post('conversations-changed', { ids })
    → Tab B receives via BroadcastChannel
    → Tab B reloads from IndexedDB
```

Messages synced: `conversations-changed`, `conversation-created`,
`conversation-deleted`, `folders-changed`, `streaming-chunk`,
`streaming-state`, `streaming-ended`.

### Legacy Migration

`migrateUntaggedConversations` in ChatContext auto-tags conversations
missing a `projectId` with the current project. No separate migration
step is needed — dual-write handles incremental sync.

---

## Design Decisions

### Per-Chat Context Association (Deferred)

The original plan called for storing `contextIds` and `skillIds` on
each `Conversation`, snapshotting on chat creation and restoring on
chat load. This was deferred because:

1. **Multi-tab solves it**: Different tasks → different tabs. Each tab
   has independent file selection and context/skill activation.
2. **Matches industry UX**: VS Code Copilot, Cursor, and similar tools
   use global context per session, not per-conversation.
3. **Edge cases add complexity**: What if a context was deleted? What if
   its files changed? The snapshot/restore UX has subtle failure modes.

### Folder-Sticky Contexts (Future Work)

Conversation folders could specify default contexts/skills that
auto-activate when opening a chat in that folder. The
`ConversationFolder` model already has `useGlobalContext` and
`systemInstructions` fields for this. Deferred because the UX for
override behavior and cross-folder moves gets complex. Revisit when
there's clear user demand.

### One-Directional Context → File Sync

Activating a context checks its files; deactivating does NOT uncheck
them. This avoids the complexity of tracking which files were manually
selected vs context-selected. Users deactivate contexts explicitly.

A future enhancement could auto-deactivate a context when all its
files are unchecked, and show a `*` indicator when some files are
unchecked (modified context). Deferred as UX polish.

### Session-Ephemeral Selections

File selections (`checkedKeys`) and active contexts/skills use
`sessionStorage` and React state respectively — they don't survive
browser restart. This is intentional:

- Avoids stale file selections from days ago
- Saved Contexts provide one-click restore (their purpose)
- Multiple tabs can't conflict over "last saved selection"

### No Global Config File

`~/.ziya/config.json` (activeProjectId, theme, shortcuts) was planned
but deferred. Projects auto-discover from cwd. Theme uses browser
defaults. Shortcuts haven't been implemented. Build this when needed.

### No Schema Migrations

`storage/migrations.py` was planned but deferred. The JSON schema
hasn't changed since initial implementation. Pydantic models with
`extra = "allow"` handle forward compatibility. Add migrations when
the schema actually changes.

---

## Future Enhancements

In rough priority order:

1. **Folder-sticky contexts**: Default contexts/skills per folder
2. **Bidirectional context-file sync**: Auto-deactivate contexts when
   files unchecked, show modified indicator
3. **Context file browser**: Expand context in ContextsTab to see/edit
   individual files with per-file token counts
4. **Keyboard shortcuts**: Cmd+K (quick switcher), Cmd+N (new chat)
5. **Implicit context update**: Right-click context pill → "Update to
   match current selection" when files added/removed
6. **Per-chat context restore**: Store and restore active contexts
   when switching chats (if multi-tab proves insufficient)
7. **Global config persistence**: Theme, shortcuts, last project
8. **Automated tests**: Storage CRUD, context activation flow,
   cross-tab sync, token calculation

---

## Color Generation

Contexts and skills get auto-assigned colors from a name hash:

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
   token limit. ActiveContextBar shows usage but no  warning.
