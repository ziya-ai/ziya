# Ziya Session & Context Management - Implementation Spec

## Overview

This document describes the implementation of a project-based session and context management system for Ziya. The goal is to replace the current client-side IndexedDB storage with server-side persistence, add support for multiple projects, and enable reusable context groups.

### Key Concepts

- **Project**: A workspace tied to a directory. All contexts and chats are scoped to a project.
- **Context**: A saved, named selection of files that can be reused across conversations.
- **Skill**: A saved prompt/instruction set that modifies AI behavior (e.g., "Code Review", "Debug Mode").
- **Chat**: An individual conversation with the AI.
- **Chat Group**: A folder/collection of related chats that share default contexts.
- **Lens**: The currently active combination of contexts (what files the AI can see).

### Design Principles

1. **Zero friction for existing users** - Current behavior works unchanged until user opts into new features
2. **Progressive disclosure** - Simple by default, power features discoverable
3. **Context flows with work** - Switching chats switches context automatically
4. **No mandatory organization** - Ungrouped chats and ad-hoc file selections always work

---

## Data Model

### Directory Structure
~/.ziya/
├── config.json                      # Global settings (theme, preferences)
└── projects/
    └── {project-id}/
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
