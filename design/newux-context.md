# Ziya Session & Context Management - Implementation Spec

**Status: IN PROGRESS - Phase 0-2 Implementation, Critical Sync Issue Identified**

## Current Status (Updated: Current Session)

### ✅ Completed

**Phase 0: Backend Storage & API (95% complete)**
- ✅ Created directory structure utilities (`app/utils/paths.py`)
- ✅ Created Pydantic models for all entities (project, context, skill, chat, group)
- ✅ Implemented base storage class with file locking (`app/storage/base.py`)
- ✅ Implemented ProjectStorage, ContextStorage, SkillStorage, ChatStorage, ChatGroupStorage
- ✅ Created TokenService and color generation service
- ✅ Created built-in skills data file
- ✅ Implemented all API endpoints (projects, contexts, skills, chats, tokens)
- ⚠️ API routes defined but not yet fully wired into server.py

**Phase 1: Frontend API Clients (100% complete)**
- ✅ Created base API client (`frontend/src/api/index.ts`)
- ✅ Created projectApi, contextApi, skillApi, tokenApi clients
- ✅ Created TypeScript type definitions (project, context, skill, token)

**Phase 2: Frontend State Management (90% complete)**
- ✅ Created ProjectContext provider with full state management
- ✅ Created ActiveContextBar component
- ✅ Created ContextsTab component with inline creation
- ✅ Created ProjectSwitcher component
- ⚠️ ProjectProvider added to App.tsx but needs verification
- ⚠️ Components integrated but activeSkillPrompts parameter fixes in progress

### 🚧 In Progress

**Critical Bugs Being Fixed:**
1. ❌ `activeSkillPrompts` parameter missing in multiple sendPayload calls
   - Fixed in: SendChatContainer.tsx (needs re-application)
   - Fixed in: Conversation.tsx (applied)
   - Fixed in: RetrySection.tsx (applied)
   - Fixed in: EditSection.tsx (applied)
   - Fixed in: StreamedContent.tsx (applied)
   - Fixed in: ThrottlingErrorDisplay.tsx (applied)
   - Fixed in: MarkdownRenderer.tsx (applied)
   - ⚠️ Stub removed from chatApi.ts getApiResponse function

2. ❌ "Save files as context" button always disabled
   - Root cause: ContextsTab checking `additionalFiles` instead of `checkedKeys`
   - Fix created but not applied

3. ❌ ProjectSwitcher showing pulsing grey (API not loading)
   - Root cause: API routes not registered in server.py
   - Fix created: Add imports and route registration
   - Not yet applied

4. ❌ Server startup may fail due to missing dependencies
   - API route imports reference modules that may have import errors
   - Need to test server startup after applying fixes

### ⚠️ Critical Design Issue Discovered

**State Sync Problem: File Tree vs Active Contexts**

**Issue:** Two sources of truth can get out of sync:
- `ProjectContext.activeContextIds` = which contexts are active
- `FolderContext.checkedKeys` = which files are checked in tree

**Scenario that breaks:**
1. User activates "UI Components" context (3 files)
2. Files get checked in tree
3. User unchecks one file in tree
4. What happens to the context? Is it still "active"?

**Proposed Solution: Bidirectional Sync**

Make `checkedKeys` the single source of truth:
- When context activated → add its files to `checkedKeys`
- When `checkedKeys` changes → auto-update context state
- If NO files from context remain → auto-deactivate context
- If SOME files removed → mark context as "modified" with indicator

Implementation needed in `ProjectContext.tsx`:
```typescript
useEffect(() => {
  // Watch checkedKeys and sync activeContextIds
  activeContextIds.forEach(ctxId => {
    const ctx = contexts.find(c => c.id === ctxId);
    const filesStillChecked = ctx.files.filter(f => checkedKeys.has(f));
    if (filesStillChecked.length === 0) {
      removeContextFromLens(ctxId); // Auto-deactivate
    }
  });
}, [checkedKeys, activeContextIds, contexts]);
```

**Enhanced UX Proposal:** Collapsible file trees in ContextsTab
- Expand context → see all its files
- Checkbox each file → include/exclude from saved context
- Allows curating contexts as a library
- Users can browse and edit without activating
- See implementation section below for details

**Status:** Design documented, implementation deferred until Phase 2 bugs fixed

### 📋 Not Started (Phases 3-5)

**Phase 3: Chat-Context Association (0% complete)**
- Extend Chat type with contextIds/skillIds
- Update chat creation to snapshot contexts
- Update chat loading to restore contexts
- Display context indicators on chat items

**Phase 4: Chat Groups (0% complete)**
- Implement group UI in ChatsTab
- Add drag-and-drop for chat organization
- Implement group default contexts/skills
- Show override indicators

**Phase 5: Migration & Polish (0% complete)**
- IndexedDB to server migration
- Offline handling
- Edge cases
- Performance optimization

--

## Next Steps for Resume

1. **Immediate (get system working):**
   - Apply the SendChatContainer.tsx fix properly (import useProject, extract activeSkillPrompts)
   - Apply the server.py imports and route registration
   - Apply the ContextsTab checkedKeys fix
   - Test: Can create contexts? Does project switcher load?

2. **Verify backend:**
   - Start server and check for import errors
   - Test GET `/api/v1/projects/current` endpoint
   - Test POST `/api/v1/projects/{id}/contexts` endpoint
   - Check `~/.ziya/` directory structure is created

3. **Then continue Phase 2:**
   - Finish integrating ContextsTab into FolderTree tabs
   - Test context creation flow end-to-end
   - Test context activation/deactivation
   - Test multi-context composition with token math

4. **Then Phase 3:**
   - Wire up chat-context association
   - Make chat switching restore contexts
   - Show context pills on chat list items

--

## Critical State Management: File Tree ↔ Context Sync

### The Problem

**Two sources of truth that must stay synchronized:**
1. `ProjectContext.activeContextIds` - which contexts are active (high-level)
2. `FolderContext.checkedKeys` - which files are selected (low-level)

**Conflict scenario:**
- User activates "UI Components" context (contains App.tsx, Button.tsx, Modal.tsx)
- Files get checked in tree ✅
- User unchecks Button.tsx in file tree
- **What happens to the context?** (This is what we need to define)

### The Solution: Bidirectional Sync

**Principle:** `checkedKeys` is the source of truth (what user sees in tree)  
**Rule:** Contexts are smart helpers that manipulate checkedKeys but don't override it

#### Implementation Requirements (Phase 2 - Critical)

**1. Context Activation → Update File Tree**
```typescript
// In ProjectContext.addContextToLens():
addContextToLens(contextId) {
  const ctx = contexts.find(c => c.id === contextId);
  setActiveContextIds(prev => [...prev, contextId]);
  
  // CRITICAL: Dispatch event to add files to checkedKeys
  window.dispatchEvent(new CustomEvent('addFilesToSelection', {
    detail: { files: ctx.files }
  }));
}
```

**2. File Tree Changes → Update Active Contexts**
```typescript
// In ProjectContext, watch checkedKeys:
useEffect(() => {
  activeContextIds.forEach(ctxId => {
    const ctx = contexts.find(c => c.id === ctxId);
    if (!ctx) return;
    
    const filesStillChecked = ctx.files.filter(f => checkedKeys.has(f));
    
    if (filesStillChecked.length === 0) {
      // NO files remain → auto-deactivate context
      setActiveContextIds(prev => prev.filter(id => id !== ctxId));
    }
    // If SOME files remain, context stays active (marked as modified)
  });
  
  // Recalculate additionalFiles
  const contextFiles = new Set();
  activeContextIds.forEach(id => {
    contexts.find(c => c.id === id)?.files.forEach(f => contextFiles.add(f));
  });
  setAdditionalFiles(Array.from(checkedKeys).filter(f => !contextFiles.has(f)));
}, [checkedKeys, activeContextIds, contexts]);
```

**3. FolderContext listens for context activation**
```typescript
// In FolderContext:
useEffect(() => {
  const handleAddFiles = (e: CustomEvent) => {
    setCheckedKeys(prev => new Set([...prev, ...e.detail.files]));
  };
  window.addEventListener('addFilesToSelection', handleAddFiles);
  return () => window.removeEventListener('addFilesToSelection', handleAddFiles);
}, []);
```

#### User Experience Flow

**Scenario 1: Uncheck file from active context**
- Context active with 3 files
- User unchecks 1 file in tree
- Context stays active, pill shows: `UI Components*` (modified indicator)
- Tooltip: "2 of 3 files active (Button.tsx excluded)"

**Scenario 2: Uncheck ALL files from context**
- User unchecks last file
- Context auto-deactivates (pill disappears)
- Checkbox in ContextsTab becomes unchecked

**Scenario 3: Update modified context**
- User right-clicks modified context pill
- Menu: "Update context to match current selection"
- Context saves with new file list

---

## Enhanced Feature: Collapsible Context File Trees (Phase 2+)

**Status: 🎯 PLANNED ENHANCEMENT (implement after core system working)**

### Concept

Add collapsible file trees inside each context item in ContextsTab, allowing users to:
- **Browse** context contents without activating
- **Edit** file membership inline (check/uncheck files)
- **Curate** contexts as a reusable library
- **See** per-file token counts

### Visual Design

```
☑ ▼ Backend Services       5 files    4.8k  ← Expanded, active
 ┃
 ┃  ☑ server.py                       1.2k
 ┃  ☑ api.py                          0.8k
 ┃  ☐ database.py (excluded)          1.5k  ← Unchecked = exclude from context
 ┃  ☑ models.py                       0.9k
 ┃  ☑ utils.py                        0.4k
 ┃  ─────────────────────────────────
 ┃  Active: 4 of 5 files         3.3k
 ┃
 ┃  [+ Add files to context...]
```

### Interaction Model

**Three-level interaction:**
1. **Context checkbox** → Activate/deactivate entire context (adds all included files to selection)
2. **Expand arrow (▶/▼)** → Show/hide file list (browse without activating)
3. **File checkbox** → Include/exclude file from saved context (persists to backend)

**Key behaviors:**
- Unchecking a file saves the change to the context immediately
- If context is active, also removes file from main tree selection
- Contexts must have at least 1 file (prevent empty contexts)
- Show summary: "Active: 4 of 5 files" when some excluded
- Per-file token counts displayed

### Implementation Components

**New components needed:**
- `ContextItem.tsx` - Single context with collapsible tree
- `ContextFileItem.tsx` - Individual file row with checkbox
- `AddFilesToContextModal.tsx` - File picker for explicit addition

**API enhancement needed:**
- Context model should include `fileTokens: Record<string, number>`
- Token calculation includes per-file breakdown
- Update endpoint recalculates tokens when files change

**Benefits:**
- ✅ No separate "edit context" modal needed
- ✅ Browse context contents without activating
- ✅ Curate contexts independently of current work
- ✅ Two workflows: implicit (modify-then-update) and explicit (direct editing)
- ✅ Contexts feel like "smart folders" you can manage

**Priority:** Implement AFTER Phase 2 core functionality is working and bugs are fixed.

---

## Design Evolution Notes

### What Changed During Implementation

**Original design had:**
- Separate management views
- Icon selection for contexts
- Complex project hierarchy

**Refined to:**
- Inline creation (no modals)
- Auto-generated colors from name hash
- Flat project structure (directory = project)
- Contexts and Skills in same tab (unified browsing)
- Progressive disclosure (no upfront organization required)

**Key UX principle decided:**
> "Context shouldn't be something you manage—it should be something that flows with your work."

### Critical Implementation Decisions

1. **Storage: File-based JSON, not database**
   - Simpler, more debuggable
   - User can inspect/edit `~/.ziya/` manually if needed
   - Atomic writes with temp files + rename

2. **Contexts are composable via checkboxes**
   - Check multiple → they stack
   - Token math shows overlap deduplication
   - Visual: colored pills + segmented token bar

3. **Skills are first-class, same pattern as contexts**
   - Built-in skills (read-only) + custom skills
   - Same checkbox pattern for activation
   - Prompts concatenated when multiple active

4. **Projects scope everything**
   - One project = one working directory
   - All contexts/skills/chats scoped to project
   - Switching projects = switching entire workspace

--

## Context Update Workflows

### Problem: How do users update an existing context to include new files?

**Scenario:** User has "UI Components" context with 3 files. Later realizes needs 2 more files. How to add them?

### Solution: Two Complementary Approaches

#### Approach 1: Implicit Update (Modify-Then-Save)

**User flow:**
1. Activate existing context "UI Components"
2. Check 2 additional files in main file tree
3. Context pill shows modified: `UI Components* +2`
4. Right-click pill → "Update context to include new files"
5. Context saves, asterisk disappears

**Visual feedback:**
```
Active Context Bar:
┌────────────────────────────────────┐
│ [UI Components* +2] [×]            │ ← Asterisk = modified
│   └── Tooltip: "+Input.tsx, +Form.tsx added"
└────────────────────────────────────┘

Right-click menu:
┌─────────────────────────────────────┐
│ ✏️  Update context (add 2 files)    │
│ ↩️  Revert to saved                 │
│ 💾  Save as new context...          │
│ ❌  Deactivate                      │
└─────────────────────────────────────┘
```

**Implementation:**
```typescript
const updateContextToMatchSelection = async (contextId: string) => {
  const currentFiles = Array.from(checkedKeys);
  await contextApi.updateContext(projectId, contextId, { files: currentFiles });
  setContexts(updated);
  message.success('Context updated');
};
```

#### Approach 2: Explicit Editing (Collapsible File Tree)

**User flow:**
1. In ContextsTab, expand context (click arrow)
2. See full file list with checkboxes
3. Uncheck files to exclude (saves immediately)
4. Click "+ Add files..." to include more
5. Changes persist to backend, affect all uses of this context

**Benefits over Approach 1:**
- Can edit WITHOUT activating context
- See what's in context before using it
- Curate context library for future use
- More discoverable (no right-click required)

**See "Enhanced Feature: Collapsible Context File Trees" section above for full visual design and implementation.**

### Workflow Comparison

| Workflow | Use Case | Discovery | Speed |
|----------|----------|-----------|-------|
| **Implicit** | Quick edits while working | Requires knowing right-click | 2 clicks |
| **Explicit** | Building context library | Highly visible (expand arrow) | 3 clicks |

**Recommendation:** Implement both
- Implicit for power users (faster iteration)
- Explicit for discoverability (clear affordances)
- Both lead to same backend operation (update context files)

**Status:** Design documented. Implement AFTER Phase 2 bugs fixed.

---

## Overview

This document describes the implementation of a project-based session and context management system for Ziya. The goal is to replace the current client-side IndexedDB storage with server-side persistence, add support for multiple projects, and enable reusable context groups.

### Key Concepts
        ├── project.json             # Project metadata
        ├── contexts/
        │   └── {context-id}.json    # Saved file selections
        ├── skills/
        │   └── {skill-id}.json      # Saved prompts/instructions
        └── chats/
            ├── _groups.json         # Chat group definitions & hierarchy
            └── {chat-id}.json       # Individual conversations


### Schema Definitions

#### Global Config (`~/.ziya/config.json`)

```typescript
interface GlobalConfig {
  version: number;                    // Schema version for migrations
  activeProjectId: string | null;     // Last active project
  recentProjects: string[];           // Project IDs, most recent first
  theme: 'light' | 'dark' | 'system';
  keyboardShortcuts: Record<string, string>;
}
Project (~/.ziya/projects/{id}/project.json)
interface Project {
  id: string;                         // UUID
  name: string;                       // Display name (default: directory basename)
  path: string;                       // Absolute path to working directory
  createdAt: number;                  // Unix timestamp ms
  lastAccessedAt: number;
  settings: {
    defaultContextIds: string[];      // Contexts auto-applied to new chats
  };
}
Context (~/.ziya/projects/{id}/contexts/{id}.json)
interface Context {
  id: string;                         // UUID
  name: string;                       // User-provided name
  files: string[];                    // Relative paths from project root
  color: string;                      // Hex color, auto-generated from name hash
  tokenCount: number;                 // Cached token count
  tokenCountUpdatedAt: number;        // When cache was last updated
  createdAt: number;
  lastUsedAt: number;
}
```

#### Skill (`~/.ziya/projects/{id}/skills/{id}.json`)

```typescript
interface Skill {
  id: string;                         // UUID
  name: string;                       // User-provided name (e.g., "Code Review")
  description: string;                // Short description shown in UI
  prompt: string;                     // The actual prompt/instruction text
  color: string;                      // Hex color, auto-generated from name hash
  tokenCount: number;                 // Cached token count of prompt
  isBuiltIn: boolean;                 // True for system-provided skills
  createdAt: number;
  lastUsedAt: number;
}

// Built-in skills provided by default
const BUILT_IN_SKILLS = [
  { name: "Code Review", description: "Detailed analysis, security focus", prompt: "..." },
  { name: "Debug Mode", description: "Step-by-step diagnosis", prompt: "..." },
  { name: "Refactoring", description: "Clean code suggestions", prompt: "..." },
  { name: "Concise", description: "Minimal explanations, code-focused", prompt: "..." },
];
```

Chat Group (~/.ziya/projects/{id}/chats/_groups.json)
interface ChatGroupsFile {
  version: number;
  groups: ChatGroup[];
}

interface ChatGroup {
  id: string;                         // UUID
  name: string;
  defaultContextIds: string[];        // Contexts applied to new chats in group
  defaultSkillIds: string[];          // Skills applied to new chats in group
  collapsed: boolean;                 // UI state
  order: number;                      // Sort order
  createdAt: number;
}
Chat (~/.ziya/projects/{id}/chats/{id}.json)
interface Chat {
  id: string;                         // UUID
  title: string;                      // Auto-generated from first message or user-set
  groupId: string | null;             // Parent group, null if ungrouped
  contextIds: string[];               // Saved contexts active for this chat
  skillIds: string[];                 // Saved skills active for this chat
  additionalFiles: string[];          // Ad-hoc files not in any saved context
  additionalPrompt: string | null;    // Ad-hoc prompt additions not in a saved skill
  messages: Message[];                // Conversation history
  createdAt: number;
  lastActiveAt: number;
}

interface Message {
  id: string;
  role: 'human' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  // ... existing message fields (images, muted, etc.)
}
Color Generation
Contexts get auto-assigned colors based on their name. This avoids user friction while still providing visual distinction.

function generateContextColor(name: string): string {
  // Predefined palette that works in dark mode
  const palette = [
    '#3b82f6', // blue
    '#8b5cf6', // violet
    '#06b6d4', // cyan
    '#10b981', // emerald
    '#f59e0b', // amber
    '#ef4444', // red
    '#ec4899', // pink
    '#6366f1', // indigo
  ];
  
  // Simple hash of name to pick from palette
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) - hash) + name.charCodeAt(i);
    hash = hash & hash;
  }
  
  return palette[Math.abs(hash) % palette.length];
}
API Specification
Base URL
All endpoints are prefixed with /api/v1/.

Projects
GET /projects
List all known projects.

Response:

{
  projects: Array<{
    id: string;
    name: string;
    path: string;
    lastAccessedAt: number;
    isCurrentWorkingDirectory: boolean;  // True if matches server's cwd
  }>;
}
POST /projects
Create or register a project.

Request:

{
  path: string;           // Absolute path to directory
  name?: string;          // Optional display name
}
Response: The created Project object.

Behavior:

If project already exists for path, returns existing
If path is current working directory and no project exists, creates default project
GET /projects/:id
Get project details.

PUT /projects/:id
Update project metadata (name, settings).

DELETE /projects/:id
Delete project and all its data.

Contexts
GET /projects/:projectId/contexts
List all contexts for a project.

Response:

{
  contexts: Context[];
}
POST /projects/:projectId/contexts
Create a new context.

Request:

{
  name: string;
  files: string[];        // Relative paths
}
Response: The created Context object with auto-generated color and token count.

GET /projects/:projectId/contexts/:id
Get a single context.

PUT /projects/:projectId/contexts/:id
Update context (name, files).

Behavior:

Recalculates token count if files changed
Regenerates color if name changed
DELETE /projects/:projectId/contexts/:id
Delete a context.

Behavior:

Removes context from any chats/groups that reference it

### Skills

#### `GET /projects/:projectId/skills`

List all skills for a project (includes built-in skills).

Response:

{
  skills: Skill[];
}

#### `POST /projects/:projectId/skills`

Create a new custom skill.

Request:

{
  name: string;
  description: string;
  prompt: string;
}

Response: The created `Skill` object with auto-generated color and token count.

#### `GET /projects/:projectId/skills/:id`

Get a single skill.

#### `PUT /projects/:projectId/skills/:id`

Update skill (name, description, prompt). Cannot update built-in skills.

Behavior:

- Recalculates token count if prompt changed
- Regenerates color if name changed

#### `DELETE /projects/:projectId/skills/:id`

Delete a skill. Cannot delete built-in skills.

Behavior:

- Removes skill from any chats/groups that reference it

Chat Groups
GET /projects/:projectId/chat-groups
List all chat groups.

Response:

{
  groups: ChatGroup[];
}
POST /projects/:projectId/chat-groups
Create a chat group.

PUT /projects/:projectId/chat-groups/:id
Update group (name, default contexts, collapsed state).

DELETE /projects/:projectId/chat-groups/:id
Delete group.

Behavior:

Chats in group become ungrouped (not deleted)
PUT /projects/:projectId/chat-groups/reorder
Reorder groups.

Request:

{
  orderedIds: string[];
}

Chats
GET /projects/:projectId/chats
List all chats for a project.

Query params:

groupId - Filter by group (use ungrouped for null group)
limit - Max results
offset - Pagination
Response:

{
  chats: Array<Omit<Chat, 'messages'> & { messageCount: number }>;
}
Note: Messages not included in list view for performance.

POST /projects/:projectId/chats
Create a new chat.

Request:

{
  groupId?: string;           // Optional group to add to
  contextIds?: string[];      // Initial contexts (defaults to group's defaults or project defaults)
  skillIds?: string[];        // Initial skills (defaults to group's defaults or project defaults)
  additionalFiles?: string[];
  additionalPrompt?: string;
  title?: string;             // Optional, will auto-generate
}
GET /projects/:projectId/chats/:id
Get full chat including messages.

PUT /projects/:projectId/chats/:id
Update chat metadata (title, group, contexts).

DELETE /projects/:projectId/chats/:id
Delete chat.

POST /projects/:projectId/chats/:id/messages
Add a message to chat.

Request:

{
  role: 'human' | 'assistant' | 'system';
  content: string;
  // ... other message fields
}
PUT /projects/:projectId/chats/:chatId/messages/:messageId
Update a message (for editing).

Token Calculation
POST /projects/:projectId/calculate-tokens
Calculate token count for a set of files.

Request:

{
  files: string[];            // Relative paths
  contextIds?: string[];      // Or reference existing contexts
  skillIds?: string[];        // Include skill prompts in calculation
  additionalPrompt?: string;  // Ad-hoc prompt text
}

Response:

{
  totalTokens: number;
  fileTokens: Record<string, number>;
  skillTokens: Record<string, number>;   // Token count per skill
  additionalPromptTokens: number;
  overlappingFiles: string[];     // Files that appear in multiple contexts
  deduplicatedTokens: number;     // Total after removing overlaps
}

Frontend Components

Component Hierarchy
App
├── ProjectSwitcher              # Dropdown at top of left panel
├── LeftPanel
│   ├── ActiveContextBar         # Shows current lens, token count
│   ├── TabBar                   # Files | Contexts | Chats
│   ├── FilesTab                 # Existing FolderTree, enhanced
│   ├── ContextsTab              # List of saved contexts
│   └── ChatsTab                 # Chat list with groups
└── ChatArea                     # Existing, mostly unchanged
ProjectSwitcher
Location: Top of left panel, always visible.

Behavior:

Shows current project name with green dot (connected) indicator
Click opens dropdown with recent projects
Search/filter projects
"Open folder as project..." action
Switching projects reloads everything
State:

const [currentProject, setCurrentProject] = useState<Project | null>(null);
const [projects, setProjects] = useState<Project[]>([]);
ActiveContextBar
Location: Below tabs, above content.

Displays:

Active context pills with × to remove
Total token count with visual bar
"+ add" button to open context picker
State:

const [activeContextIds, setActiveContextIds] = useState<string[]>([]);
const [activeAdditionalFiles, setActiveAdditionalFiles] = useState<string[]>([]);
const [tokenInfo, setTokenInfo] = useState<TokenInfo | null>(null);
Key interactions:

Click × on pill removes that context
Click "+ add" switches to Contexts tab
Token bar shows proportion of context window used

ContextsTab

Location: Left panel, second tab. This tab shows BOTH contexts (file selections) AND skills (prompts) in a unified list, separated by section headers.

Displays:

Search/filter input
Section: "File Contexts"
  - List of saved contexts with checkbox, color bar, name, file count, token count
Section: "Skills & Prompts"  
  - List of saved skills with checkbox, color bar, name, description, token count
  - Built-in skills marked with subtle indicator
"Save current selection" button in footer
"New skill" button in footer

Token math display:

If context is NOT active:
  Show "+{tokens}" indicating what it would add

If context IS active:
  Show "{tokens}" as its contribution
Creating a context:

User selects files in Files tab
Clicks "Save current selection" (in either tab's footer)
Inline input appears for name
On save, context created with auto-color
New context auto-activates
ChatsTab
Location: Left panel, third tab.

Displays:

"New Chat" button at top
Chat groups (collapsible)
Group header with name, chat count, context indicators
Chats within group
Ungrouped chats section
Each chat shows:
Title
Time ago
Context indicators (colored dots matching active contexts)
Override indicator if different from group default
Chat item context display:

┌─────────────────────────────────────────┐
│ Fix scroll behavior                     │
│ 3h ago  ●● (blue, purple dots)          │
└─────────────────────────────────────────┘

If chat has contexts different from group:
┌─────────────────────────────────────────┐
│ Token persistence bug                   │
│ 5h ago  ●●● +Database                   │
└─────────────────────────────────────────┘
Group interactions:

Click group header to expand/collapse
Right-click group for context menu:
Rename
Set default contexts
Delete (chats become ungrouped)
Drag chat to different group
FilesTab Enhancement
Changes from current:

Files that belong to active contexts show colored left border
Checkboxes for context-included files are disabled (show as locked)
Can still select additional files on top of contexts
Footer shows "Save current selection" when files selected
Visual treatment:

┌─────────────────────────────────────────┐
│ ▼ 📁 components                         │
│   ┃● ☑ App.tsx              2.1k       │  ← Blue border = in "UI" context
│   ┃● ☑ Conversation.tsx     3.8k       │
│   ┃  ☐ FolderTree.tsx       1.2k       │  ← No border = not in any context
│   ┃● ☑ ChatContext.tsx      4.2k       │  ← Gradient = in multiple contexts
└─────────────────────────────────────────┘

State Management

New Context: ProjectContext

interface ProjectContextType {
  // Current project
  currentProject: Project | null;
  projects: Project[];
  switchProject: (projectId: string) => Promise<void>;
  
  // Contexts (file selections) for current project
  contexts: Context[];
  createContext: (name: string, files: string[]) => Promise<Context>;
  updateContext: (id: string, updates: Partial<Context>) => Promise<void>;
  deleteContext: (id: string) => Promise<void>;
  
  // Skills for current project
  skills: Skill[];
  createSkill: (name: string, description: string, prompt: string) => Promise<Skill>;
  updateSkill: (id: string, updates: Partial<Skill>) => Promise<void>;
  deleteSkill: (id: string) => Promise<void>;
  
  // Active lens (what's currently selected)
  activeContextIds: string[];
  activeSkillIds: string[];
  additionalFiles: string[];          // Files selected but not in a saved context
  additionalPrompt: string | null;    // Prompt text not in a saved skill
  setActiveContextIds: (ids: string[]) => void;
  setActiveSkillIds: (ids: string[]) => void;
  setAdditionalFiles: (files: string[]) => void;
  setAdditionalPrompt: (prompt: string | null) => void;
  
  // Computed
  activeFiles: string[];              // All files in active contexts + additional
  tokenInfo: TokenInfo | null;
}
Modified: ChatContext
// Additions to existing ChatContext
interface ChatContextAdditions {
  // Chat groups
  chatGroups: ChatGroup[];
  createChatGroup: (name: string) => Promise<ChatGroup>;
  updateChatGroup: (id: string, updates: Partial<ChatGroup>) => Promise<void>;
  deleteChatGroup: (id: string) => Promise<void>;
  
  // Enhanced chat operations
  moveChatToGroup: (chatId: string, groupId: string | null) => Promise<void>;
  setChatContexts: (
    chatId: string, 
    contextIds: string[], 
    skillIds: string[],
    additionalFiles: string[],
    additionalPrompt: string | null
  ) => Promise<void>;
}

State Flow: Switching Chats
User clicks on Chat B (currently viewing Chat A)
    │
    ▼
Load Chat B from API (or cache)
    │
    ▼
Update currentChatId
    │
    ▼
Update activeContextIds from Chat B's contextIds
    │
    ▼
Update activeSkillIds from Chat B's skillIds
    │
    ▼
Update additionalFiles from Chat B's additionalFiles
    │
    ▼
ActiveContextBar re-renders with new lens
    │
    ▼
FilesTab re-renders showing Chat B's file selection
State Flow: Creating a Context
User has files selected in Files tab
    │
    ▼
User clicks "Save current selection"
    │
    ▼
Inline input appears, user types name
    │
    ▼
On Enter/Save:
    │
    ├── POST /projects/:id/contexts with name + files
    │
    ▼
Response includes new Context with color + tokenCount
    │
    ▼
Add to local contexts state
    │
    ▼
Add new context ID to activeContextIds
    │
    ▼
Clear additionalFiles (they're now in the context)
    │
    ▼
If viewing a chat, update chat's contextIds
Implementation Phases
Phase 0: Backend Storage & API
Create ~/.ziya/ directory structure utilities
Implement file-based storage for each entity type
Add JSON schema validation
Implement all API endpoints
Add API error handling and validation
Write API integration tests
Add token counting endpoint using existing token counting logic
Add built-in skills initialization
│   ├── skills.py         # Skill storage
│   ├── skills.py         # Skill endpoints
└── data/
    └── built_in_skills.py  # Default skill definitions

Built-in skills appear automatically for new projects

Add built-in skills initialization
│   ├── skills.py         # Skill storage
│   ├── skills.py         # Skill endpoints
└── data/
    └── built_in_skills.py  # Default skill definitions

Built-in skills appear automatically for new projects

Files to create:

server/
├── storage/
│   ├── __init__.py
│   ├── base.py           # Base storage class
│   ├── config.py         # Global config storage
│   ├── projects.py       # Project storage
│   ├── contexts.py       # Context storage
│   ├── chats.py          # Chat & group storage
│   └── migrations.py     # Schema migrations
├── api/
│   ├── projects.py       # Project endpoints
│   ├── contexts.py       # Context endpoints
│   └── chats.py          # Chat endpoints
Acceptance criteria:

All endpoints return correct data
Data persists across server restarts
Concurrent access doesn't corrupt data
Invalid requests return appropriate errors
Phase 1: Project Awareness
Create ProjectContext provider
Implement project API client functions
Build ProjectSwitcher component
Auto-create project for current working directory on first load
Update App.tsx to wrap with ProjectProvider
Store/restore last active project
Handle project switching (clear and reload state)
Files to modify:

frontend/src/
├── context/
│   └── ProjectContext.tsx    # NEW
**Status: 🚧 IN PROGRESS**

Core infrastructure complete, integration in progress:
- ✅ ProjectContext provider implemented
- ✅ ProjectSwitcher component created
- ✅ API clients created
- 🚧 Integration with existing components incomplete
- ❌ activeSkillPrompts parameter causing crashes

- [x] Create ProjectContext provider ✅
- [x] Implement project API client functions ✅
- [x] Build ProjectSwitcher component ✅
- [x] Auto-create project for current working directory on first load ✅
- [ ] Update App.tsx to wrap with ProjectProvider ⚠️ IN PROGRESS
- [ ] Store/restore last active project ⚠️ TODO
- [x] Handle project switching (clear and reload state) ✅
├── components/
│   ├── App.tsx               # Add ProjectProvider, ProjectSwitcher
│   └── ProjectSwitcher.tsx   # NEW
├── apis/
│   └── projectApi.ts         # NEW
Acceptance criteria:

Opening Ziya shows current directory as project
Project name appears in header
Can switch between projects (if multiple exist)
Switching projects clears chat state
Phase 2: Saved Contexts
Implement context API client functions
Add contexts state to ProjectContext
Build ContextsTab component
Build ActiveContextBar component
Add "Save current selection" flow
Implement token overlap calculation display
Add context CRUD operations (create, rename, delete)
Update FolderContext to track which files are from contexts vs ad-hoc
Implement skill API client functions
Add skills state to ProjectContext
Build skill list UI within ContextsTab
Add "New skill" creation flow
Add skill CRUD operations (create, rename, delete)
│   ├── SkillItem.tsx         # NEW - single skill in list
│   ├── SkillCreateModal.tsx  # NEW - skill creation UI
│   └── skillApi.ts           # NEW
Implement skill API client functions
Add skills state to ProjectContext
Build skill list UI within ContextsTab
Add "New skill" creation flow
Add skill CRUD operations (create, rename, delete)
│   ├── SkillItem.tsx         # NEW - single skill in list
│   ├── SkillCreateModal.tsx  # NEW - skill creation UI
│   └── skillApi.ts           # NEW
Files to create/modify:

frontend/src/
├── context/
│   └── ProjectContext.tsx    # Add context state
├── components/
│   ├── ContextsTab.tsx       # NEW
│   ├── ActiveContextBar.tsx  # NEW
│   ├── ContextItem.tsx       # NEW - single context in list
│   └── FolderTree.tsx        # Enhance with context indicators
├── apis/
│   └── contextApi.ts         # NEW
Acceptance criteria:

Can save current file selection as named context
Contexts appear in list with correct token counts
Checking context adds its files to selection
Multiple contexts can be active simultaneously
Token count shows overlap-adjusted totals
Can rename and delete contexts
Skills appear in Contexts tab below file contexts
Can create custom skills with name, description, prompt
Built-in skills are visible but not editable/deletable
Checking skill adds its prompt to active lens
Multiple skills can be active simultaneously
Skill token counts shown, including overlap with context tokens

Extend Chat type with skillIds and additionalPrompt
Update chat creation to snapshot current skills
Update chat loading to restore skills
Handle skill deletion (remove from chats that use it)
Phase 3: Chat-Context Association
Extend Chat type with contextIds and additionalFiles
Update chat creation to snapshot current contexts
Update chat loading to restore contexts
Display context indicators on chat list items
Implement chat context editing
Handle context deletion (remove from chats that use it)
Files to modify:

frontend/src/
├── context/
│   └── ChatContext.tsx       # Add context association
├── components/
│   ├── ChatsTab.tsx          # Show context indicators
│   └── ChatItem.tsx          # Context dots display
├── apis/
│   └── chatApi.ts            # Update for new fields
Acceptance criteria:

New chat captures current active contexts
Switching chats switches active contexts
Chat list shows context indicators (colored dots)
Can modify a chat's contexts without affecting others
Deleting a context updates affected chats
New chat captures current active skills
Switching chats switches active skills
Chat list shows skill indicators where different from default
Deleting a skill updates affected chats

Implement group default skills
Phase 4: Chat Groups
Implement chat groups API client
Add groups state to ChatContext
Build collapsible group UI in ChatsTab
Implement group default contexts
Add drag-and-drop for moving chats between groups
Add group context menu (rename, set contexts, delete)
Show override indicator when chat differs from group defaults
"New chat in group" respects group defaults
Files to create/modify:

frontend/src/
├── context/
│   └── ChatContext.tsx       # Add group state
├── components/
│   ├── ChatsTab.tsx          # Group rendering
│   ├── ChatGroup.tsx         # NEW - collapsible group
│   └── ChatGroupContextMenu.tsx  # NEW
├── apis/
│   └── chatApi.ts            # Group endpoints
Acceptance criteria:

Can create chat groups
Chats can be dragged between groups
Groups have configurable default contexts
Groups have configurable default skills
New chats in group inherit group's contexts
New chats in group inherit group's skills
Groups collapse/expand
Visual indicator when chat overrides group defaults
Phase 5: Migration & Polish
Implement migration from IndexedDB to server storage
Detect existing IndexedDB data on load
One-click migration flow
Map old conversations to default project
Clean up IndexedDB after successful migration
Add loading states for all async operations
**Status: ⏸️ NOT STARTED**

Implement optimistic updates where appropriate
Add error handling and retry logic
Handle offline state gracefully
Add keyboard shortcuts
Cmd+K - Quick switcher
Cmd+N - New chat
Cmd+Shift+N - New chat in current group
Performance optimization
Lazy load chat messages
Cache context token counts
Debounce saves
Edge case handling
Deleted files in saved contexts
Very large contexts (token warnings)
Acceptance criteria:

Existing users can migrate data seamlessly
App remains responsive with many chats/contexts
Errors are surfaced clearly to user
Keyboard navigation works throughout
Testing Strategy
Backend Tests
# Test each storage class
class TestProjectStorage:
    def test_create_project(self): ...
    def test_list_projects(self): ...
    def test_update_project(self): ...
    def test_delete_project_cascades(self): ...

class TestContextStorage:
    def test_create_context(self): ...
    def test_token_count_cached(self): ...
    def test_delete_context_updates_chats(self): ...

class TestChatStorage:
    def test_create_chat_with_contexts(self): ...
    def test_move_chat_to_group(self): ...
    def test_delete_group_ungroups_chats(self): ...
Frontend Tests
// Component tests
describe('ContextsTab', () => {
  it('displays contexts with correct token counts', () => {});
  it('shows overlap-adjusted tokens for inactive contexts', () => {});
  it('allows multi-select', () => {});
});

describe('ActiveContextBar', () => {
  it('displays all active contexts as pills', () => {});
  it('removes context when × clicked', () => {});
  it('shows correct total tokens', () => {});
});

describe('ChatsTab', () => {
  it('groups chats correctly', () => {});
  it('shows context indicators', () => {});
  it('collapses/expands groups', () => {});
});
Integration Tests
describe('Context Flow', () => {
  it('saves selection as context and activates it', async () => {
    // Select files
    // Click save
    // Enter name
    // Verify context created and active
  });
  
  it('switching chats switches contexts', async () => {
    // Create chat A with context X
    // Create chat B with context Y
    // Switch to chat A
    // Verify context X active
    // Switch to chat B
    // Verify context Y active
  });
});
Open Questions
Conflict resolution: If same project opened in multiple tabs, how do we handle concurrent edits?
Recommendation: Last-write-wins with timestamp, surface conflicts in UI
Context versioning: When files change, should we track context "versions"?
Recommendation: No, just update token count. Keep it simple.
Sharing contexts: Should contexts be exportable/shareable?
Recommendation: Defer to future phase
Maximum contexts: Should we limit number of active contexts?
Recommendation: No hard limit, but show warning if token count exceeds threshold
Glossary
Project: A workspace tied to a directory path. Scopes all contexts and chats.
Context: A named, saved selection of files that can be reused.
Lens: The currently active combination of contexts (what the AI sees).
Chat: An individual conversation with message history.
Chat Group: A folder that organizes chats and provides default contexts.
Additional Files: Files selected ad-hoc that aren't part of any saved context.
Token Count: Estimated number of tokens the files will consume in the AI context window.
