# Multi-Root Projects

## Problem

Ziya projects are currently single-rooted: one project = one directory. This
breaks down for workflows that span multiple repositories or directory trees.
Examples:

- A service repo + a shared proto/schema repo
- A backend repo + a separate frontend repo
- A monorepo sub-directory + a config repo
- An infrastructure-as-code repo + the application repo it deploys

Users want to organize by **intent** ("everything for the payments service")
rather than by directory structure. Today they must either maintain multiple
projects and switch between them, or contort their directory layout.

A deeper problem: the **same logical codebase** can live at different absolute
paths — different git worktrees, branches checked out to different directories,
forks, or simply a project that moved. The identity of a root must survive
path changes.

## Goals

1. A project can have **one or more root directories**, each with a stable
   identity that persists across path changes.
2. **Zero-friction migration**: existing single-root projects continue to
   work without user action.
3. File references remain **unambiguous** across roots.
4. The file tree UI shows multiple roots as top-level nodes.
5. **No project cruft**: smart resolution on launch, ask when ambiguous,
   never silently auto-create when existing projects are related.
6. Users can **repoint** a root to a new path (new branch, different
   checkout, moved directory) without losing contexts, skills, or
   conversation history.
7. Users can **merge** an accidentally-created project into an existing one,
   preserving conversation history and contexts.
8. A user can **open the same project at a different checkout** in a
   separate tab/session — same conversations, same contexts, different
   working directory — without permanently repointing the root.
8. Users can **override** a root's path per-session (per-tab) to work on a
   different branch or checkout without repointing the canonical path —
   preserving shared context while working against different code.

## Non-Goals (for now)

- Remote / URL-based roots (only local directories)
- Per-root independent scan-filter configuration
- Auto-detection of equivalent roots via git remote URL (see Appendix A)
- Git status display per-root (separate future feature)

---

## Core Concept: A Root Has Identity Beyond Its Path

A root is **not** its path. A root is a named slot that *points to* a path.
The path can change; the identity persists.

```
ProjectRoot {
  id: string              // Stable UUID — the root's identity
  label: string           // Human display name ("API", "Frontend")
  path: string            // Current absolute path (mutable, always absolute)
  
  // Future: equivalence hints
  gitRemoteUrl?: string   // For detecting "same repo, different checkout"
  gitBranch?: string      // Informational — which branch is checked out
}
```

Why separate `id` and `label`? Labels can be renamed. IDs can't. All internal
references (context file lists, checked keys, stored state) use the `id` so
they survive label renames. The UI shows `label` everywhere — the `id` is
an implementation detail the user never sees.

**Paths are always stored as absolutes.** Two roots with paths
`/home/user/proj-a/src` and `/home/user/proj-b/src` are different roots
despite both being named `src`. There is no aliasing or deduplication by
basename — the absolute path is the physical locator, the UUID is the
logical identity.

---

## Schema

### Backend: `app/models/project.py`

```python
class ProjectRoot(BaseModel):
    """A single root directory within a project."""
    id: str                                # Stable UUID
    label: str                             # Human display name
    path: str                              # Absolute directory path (mutable)
    gitRemoteUrl: Optional[str] = None     # Future: equivalence hint
    gitBranch: Optional[str] = None        # Future: informational

class Project(BaseModel):
    id: str
    name: str
    roots: List[ProjectRoot] = []          # Ordered; roots[0] is primary
    path: str = ""                         # Legacy compat: always == roots[0].path
    createdAt: int
    lastAccessedAt: int
    settings: ProjectSettings
```

### Frontend: `frontend/src/types/project.ts`

```typescript
export interface ProjectRoot {
  id: string;
  label: string;
  path: string;
  gitRemoteUrl?: string;
  gitBranch?: string;
}

export interface Project {
  id: string;
  name: string;
  roots: ProjectRoot[];
  path: string;                  // legacy compat: roots[0].path
  createdAt: number;
  lastAccessedAt: number;
  settings: ProjectSettings;
}
```

### On-disk format: `project.json`

```jsonc
{
  "id": "abc-123",
  "name": "Payments Service",
  "roots": [
    { "id": "r-001", "label": "API", "path": "/home/user/payments-api" },
    { "id": "r-002", "label": "Frontend", "path": "/home/user/payments-fe" },
    { "id": "r-003", "label": "Protos", "path": "/home/user/shared-protos" }
  ],
  "path": "/home/user/payments-api",
  "settings": { ... }
}
```

---

## File Reference Format

Files are stored internally as `rootId:relative/path`. The UI displays them
as `label: relative/path`.

| Scenario | Stored reference | Displayed as |
|----------|-----------------|--------------|
| Single-root, bare path | `src/main.py` | `src/main.py` |
| Multi-root, qualified | `r-002:src/App.tsx` | `Frontend: src/App.tsx` |
| Absolute path | `/home/user/x.py` | `/home/user/x.py` |

**Backward compatibility**: Bare paths (no `:` prefix) always resolve
against `roots[0]`. Single-root projects always have `roots[0].id = ""`
(empty), so all existing bare-path references remain valid with zero
migration.

**Resolution rules:**
1. If the reference starts with `/` → absolute, used as-is
2. If it contains `:` and doesn't start with `/` → split on first `:`
   into `(rootId, relativePath)`, look up root by id
3. Otherwise → bare path, resolve against `roots[0].path`

```python
def resolve_file_ref(ref: str, roots: List[ProjectRoot]) -> Optional[str]:
    if os.path.isabs(ref):
        return ref
    if ':' in ref and not ref.startswith('/'):
        root_id, rel = ref.split(':', 1)
        for root in roots:
            if root.id == root_id:
                return os.path.join(root.path, rel)
        return None  # Unknown root ID
    if roots:
        return os.path.join(roots[0].path, ref)
    return None
```

---

## Migration: Automatic and Lossless

### Single-root → multi-root normalization (on every read)

```python
def _normalize_roots(self, data: dict) -> dict:
    if not data.get("roots") and data.get("path"):
        data["roots"] = [{
            "id": "",              # Empty ID = primary root (bare paths resolve here)
            "label": Path(data["path"]).name or "root",
            "path": data["path"]
        }]
    if data.get("roots"):
        data["path"] = data["roots"][0]["path"]  # Keep legacy field in sync
    return data
```

No explicit migration endpoint. First write after any read persists both
fields.

### Promoting to multi-root (adding a second root)

When a user adds a second root, the primary root's `id` changes from `""`
(the backward-compat sentinel) to a real UUID. At that point we do a
bounded rewrite: scan this project's context files and rewrite bare-path
entries to `newRootId:path`. This only touches the project's own
`~/.ziya/projects/{id}/contexts/*.json` files.

---

## Project Resolution on Launch

**Principle: never create cruft. Ask when ambiguous. Be helpful but not
presumptuous.**

### The algorithm

When Ziya starts in directory `D`:

```
Step 1 — Exact root match:
  Find all projects P where D ∈ P.roots[*].path
  
  → 1 match:   open that project
  → N matches:  present chooser (see UI below)
  → 0 matches: go to Step 2

Step 2 — Descendant match (user is inside a root):
  Find all projects P where some root.path is an ancestor of D
  
  → 1 match:   open that project
  → N matches:  present chooser
  → 0 matches: go to Step 3

Step 3 — Neighborhood scan:
  Search for "nearby" existing projects to avoid creating cruft.
  
  Nearby = projects whose roots are:
    - Siblings of D (other directories in D's parent)
    - Under ancestors of D, up to 4 levels above D
    - Stop at $HOME — never suggest a project rooted at home or above
  
  → Nearby projects found: present chooser with options
     (see "Neighborhood chooser" UI below)
  → No nearby projects: create new project with D as sole root
```

### Why 4 levels?

The limit prevents absurd suggestions. If you're in
`~/work/team-b/services/payments` we'll check up to `~/work/` for related
projects, but not `~/` or `/home/user`. This catches the common cases:
- Sibling checkouts (`myapp-v1/` and `myapp-v2/` in the same parent)
- Monorepo sub-paths (project rooted at `monorepo/`, user in `monorepo/services/auth/`)

### Chooser UI — exact/descendant matches

When multiple projects claim directory D:

```
┌──────────────────────────────────────────────────────────┐
│ This directory is used by multiple projects:              │
│                                                           │
│  ● Payments — Full Stack                                  │
│    Roots: API, Frontend                                   │
│    Last used: 2 hours ago                                 │
│                                                           │
│  ● API — All Services                                     │
│    Roots: API, Orders-API, Billing-API                    │
│    Last used: 3 days ago                                  │
│                                                           │
│  ○ Create new project                                     │
│                                                           │
│                              [Cancel]  [Open Selected]    │
└──────────────────────────────────────────────────────────┘
```

### Chooser UI — neighborhood matches

When D doesn't match any project but nearby projects exist:

```
┌───────────────────────────────────────────────────────────────────┐
│ No project is rooted at this directory.                            │
│                                                                    │
│ Nearby projects found:                                             │
│                                                                    │
│  ● Open "Payments API"                                             │
│    Rooted at: /home/user/payments-api  (sibling directory)         │
│                                                                    │
│  ● Re-root "Payments API" here                                     │
│    Changes its root from /home/user/payments-api → this directory  │
│                                                                    │
│  ● Add this directory as a new root to "Payments API"              │
│    Makes it a multi-root project                                   │
│                                                                    │
│  ○ Create new project                                              │
│                                                                    │
│                                       [Cancel]  [Continue]         │
└───────────────────────────────────────────────────────────────────┘
```

The options are situational — not all options appear in all cases. If the
nearby project has a root that's clearly a different directory (not a
sibling), "re-root" doesn't make sense and is omitted.

**Multiple projects sharing the same root is explicitly allowed.** This is
intentional: different projects represent different organizational views of
the same code.

---

## Two Operations: Repoint and Merge

These are the two ways a project adapts to directory changes.

### Repoint — "Same root, different path"

**Use case**: You were working on `/home/user/myapp-v1`. You've checked out
v2 at `/home/user/myapp-v2`. Same logical codebase, new location.

**Operation**: In root management, "Repoint" on a root → enter new path →
the root's `id` and `label` stay the same, only `path` changes.

All context file references survive because they're stored as
`rootId:relative/path`. Files that don't exist in the new checkout get
flagged as missing (not deleted), because you might switch back.

**When it happens:**
- Explicitly, via the root management panel
- On launch, if a root's path is stale: "Root 'API' path no longer exists.
  [Repoint] [Remove]"
- Via the neighborhood chooser: "Re-root here" is a repoint

### Merge — "Two projects become one"

**Use case**: You started Ziya in a new checkout directory. It auto-created
Project B. You realize it should have been part of existing Project A.

**Operation**: Merge B into A. B dissolves; A absorbs B's content.

**What moves from B → A:**
- **Conversations**: Their `projectId` is rewritten from B.id to A.id.
  Message content (text, markdown, diffs) is left untouched — those are
  display-only and don't contain structural file references.
- **Contexts**: B's context files are moved to A's context directory.
  File references in the contexts are qualified with the new root's id
  (if B's root becomes a new root in A).
- **Skills**: Merged into A. Duplicates by name are skipped (A's version
  kept).
- **Roots**: Depends on merge variant (see below).

**Merge variants:**

1. **Merge as new root** ("add B's directory to A"):
   B's root becomes a new root in A. Both directories are active.
   This is "I want to work across both directories."

2. **Merge with repoint** ("B replaces A's root"):
   B's root path replaces the path of an existing root in A. The
   old path goes away. This is "I moved to a new checkout and
   accidentally created a new project."

**After merge:**
- Project B is deleted from the project list
- Project A is opened with the merged content
- A toast/notification confirms what happened

**Merge is available from:**
- The neighborhood chooser on launch (implicit merge)
- Project settings → "Merge another project into this one" (explicit)
- The project list → right-click → "Merge into..." (explicit)

### Display: multi-root awareness

The user flagged: "that gets dangerous without a clear display that it's
multi-rooted." Two indicators:

1. **File tree**: Multiple top-level directory nodes with labels and path
   hints. This is the primary indicator — you see it immediately.

2. **Project header**: A subtle badge or count next to the project name:
   `Payments Service 📁×3` or just tooltip text "3 root directories".
   Not intrusive, but discoverable.

---

## Stale Path Detection

On project open, check each root:

```python
for root in project.roots:
    if not os.path.isdir(root.path):
        root._stale = True  # transient flag, not persisted
```

In the file tree, stale roots show a warning:

```
📁 API  (/home/user/payments-api)
   📁 src/
📁 Frontend  (/home/user/payments-fe)  ⚠️ Path not found
   [Repoint]  [Remove]
📁 Protos  (/home/user/shared-protos)
   📁 definitions/
```

The project still opens. Other roots are fully usable. Only the stale root's
files are unavailable until repointed or removed.

---

## Session Root Overrides (Same Project, Different Checkout)

### The Problem

A developer has project "Payments API" with root "API" canonically pointing
at `/home/user/payments-api` (main branch). They open a second tab to work
in `/home/user/payments-api-v2` (a feature branch). They want the **same
project** — same conversations, same saved contexts, same skills — but with
the file tree and file operations resolved against the feature branch
checkout.

This is NOT a repoint (which is permanent). This is a per-session,
per-tab override.

### How It Works

Three layers of path resolution, checked in order:

```
1. Session override    (per-tab, transient, stored in sessionStorage)
2. Canonical root path (per-project, persistent, stored in project.json)
3. Fallback            (ZIYA_USER_CODEBASE_DIR or cwd)
```

A **session override** is a mapping from a root `id` to an alternate path,
scoped to a single browser tab. It lives in `sessionStorage` (not IndexedDB,
not project.json). Closing the tab erases it.

```typescript
// In sessionStorage, keyed per project:
interface SessionRootOverrides {
  [rootId: string]: string;  // rootId → overridden absolute path
}

// Key: `ZIYA_ROOT_OVERRIDES_${projectId}`
// Value: JSON.stringify({ "r-001": "/home/user/payments-api-v2" })
```

### Resolution Chain

When the frontend needs to send root paths to the backend (via headers or
API params), it resolves each root:

```typescript
function resolveRootPaths(project: Project): ResolvedRoot[] {
  const overrides = getSessionOverrides(project.id);
  return project.roots.map(root => ({
    id: root.id,
    label: root.label,
    path: overrides[root.id] || root.path,  // session wins
    isOverridden: root.id in overrides,
    canonicalPath: root.path,
  }));
}
```

The backend receives the **resolved** paths. It doesn't need to know about
overrides — it just gets the paths to use for this request. The
`X-Project-Roots` header (or per-request body) carries the resolved list.

### Setting an Override

Two entry points:

**1. From root management panel** (mid-session):
Each root in the panel gets a "Use different path in this tab" action.
This opens a path input. The override is saved to sessionStorage and takes
effect immediately — the file tree rescans against the new path.

```
┌──────────────────────────────────────────────────────────┐
│ ⭐ API         /home/user/payments-api                    │
│    ≡  [Repoint] [Rename]                        [✕]      │
│                                                           │
│    This tab: /home/user/payments-api-v2  ← override       │
│    [Reset to canonical]                                   │
└──────────────────────────────────────────────────────────┘
```

**2. On launch** (automatic):
When Ziya starts in directory `D` and resolves to an existing project
(Step 1 or 2 of launch resolution), but `D` doesn't exactly match any
canonical root path — it might be a different checkout of the same code.
The launch flow offers:

```
Project "Payments API" found (root "API" → /home/user/payments-api).
You launched from /home/user/payments-api-v2.

What would you like to do?
  [Open with this path for this session]   ← session override
  [Permanently repoint "API" here]         ← repoint
  [Create new project]                     ← genuinely different project
```

"Open with this path for this session" sets a session override and opens
the project. The canonical path in project.json is untouched.

### Visual Safety: Making Overrides Unmissable

This is inherently confusing if not clearly displayed. Two tabs showing
"Payments API" but looking at different code. Mitigations:

**1. Root label annotation in file tree:**
```
📁 API  (/home/user/payments-api-v2) 🔀 session override
   📁 src/
   📁 tests/
```

The root node shows the *actual* resolved path (not the canonical), plus a
visual indicator (icon, color, or label) that it's overridden.

**2. Project header banner:**
When any root has a session override, a persistent subtle banner appears
below the project name in the sidebar:

```
┌────────────────────────────┐
│ 🟢 Payments API            │
│ 🔀 API → /payments-api-v2  │
│    (session override)       │
└────────────────────────────┘
```

**3. Conversation context:**
Conversations don't record which path was active when a message was sent
(that would be too heavy). But the conversation header could show the
current session state so the user knows which code they're talking about.

### What Overrides Do NOT Affect

- **Repointing**: Session overrides never modify project.json.
- **Other tabs**: Each tab has its own sessionStorage. Tab 1's override
  doesn't affect Tab 2.
- **Contexts**: Context file references are `rootId:relative/path`. They
  resolve through the override chain — the same context works in both tabs,
  just resolving to different absolute paths.
- **Conversation history**: Shared across tabs. A conversation started in
  Tab 1 (main branch) is visible in Tab 2 (feature branch). The user gets
  the full history regardless of which checkout they're in.

### When Session Overrides Become Permanent

If a user consistently uses an override, they probably want to repoint.
We could track override usage and after N sessions (or a time threshold)
suggest: "You've been using /payments-api-v2 for 3 sessions. Would you like
to permanently repoint?" — but this is a Phase 4+ refinement.

---

## Backend Changes

### 1. `app/context.py` — Multi-root context

```python
_request_project_roots: contextvars.ContextVar[Optional[List[str]]] = (
    contextvars.ContextVar('request_project_roots', default=None)
)

def set_project_roots(paths: List[str]) -> None:
    _request_project_roots.set(paths)
    if paths:
        set_project_root(paths[0])  # backward compat

def get_project_roots() -> List[str]:
    roots = _request_project_roots.get()
    if roots:
        return roots
    single = get_project_root()
    return [single] if single else []
```

### 2. `app/middleware/project_context.py` — Header resolution

The middleware supports three header strategies (checked in order):

```python
async def dispatch(self, request, call_next):
    # Strategy 1: Frontend sends resolved root paths directly.
    # This is the primary path for multi-root + session overrides.
    # The frontend has already applied any session overrides.
    project_roots_header = request.headers.get("X-Project-Roots")
    if project_roots_header:
        paths = [p.strip() for p in project_roots_header.split(",") if p.strip()]
        valid = [p for p in paths if os.path.isdir(p)]
        if valid:
            set_project_roots(valid)
            return await call_next(request)

    # Strategy 2: Project ID — look up canonical roots from storage.
    # Used when the frontend doesn't have overrides to apply.
    project_id = request.headers.get("X-Project-Id")
    if project_id:
        project = self.project_storage.get(project_id)
        if project and project.roots:
            set_project_roots([r.path for r in project.roots])
            return await call_next(request)

    # Strategy 3: Legacy single-root header.
    project_root = request.headers.get("X-Project-Root")
    if project_root and os.path.isdir(project_root):
        set_project_root(project_root)

    return await call_next(request)
```

This means the backend **never needs to know about session overrides**.
The frontend resolves overrides locally and sends the final paths.

### 3. `app/config/write_policy.py` — Check all roots

```python
def is_write_allowed(self, target_path: str, project_root: str = "") -> bool:
    roots = get_project_roots() or ([project_root] if project_root else [])
    return any(self._check_path(target_path, r) for r in roots)
```

### 4. File tree scanning — multi-root response

```json
{
  "type": "multi-root",
  "roots": [
    { "id": "r-001", "label": "API", "path": "/home/...", "children": { ... } },
    { "id": "r-002", "label": "Frontend", "path": "/home/...", "children": { ... } }
  ]
}
```

Single-root projects continue returning the existing flat format.

### 5. New API endpoints

```
POST   /api/v1/projects/{id}/roots              — add a root
PUT    /api/v1/projects/{id}/roots/{rootId}      — update (repoint, rename)
DELETE /api/v1/projects/{id}/roots/{rootId}       — remove
PUT    /api/v1/projects/{id}/roots/reorder        — set root order
POST   /api/v1/projects/{id}/merge               — merge another project in
GET    /api/v1/projects/resolve?path=<dir>        — launch resolution
```

The `/resolve` endpoint implements the 3-step algorithm and returns either
a single project (auto-open) or a list of candidates (present chooser).

```json
// Single match — auto-open
{ "action": "open", "project": { ... } }

// Ambiguous — present chooser
{ "action": "choose", "candidates": [...], "relationship": "exact|ancestor|nearby" }

// No match — create
{ "action": "create", "suggestedName": "payments-v2" }
```

### 6. Merge endpoint

```
POST /api/v1/projects/{targetId}/merge
Body: { "sourceProjectId": "...", "variant": "add-root" | "repoint", "targetRootId": "..." }
```

Steps:
1. Load source and target projects
2. Move source conversations → target (rewrite projectId)
3. Move source contexts → target (qualify file refs)
4. Merge source skills → target (skip duplicates)
5. Handle roots per variant
6. Delete source project
7. Return updated target project

---

## Frontend Changes

### 1. `api/index.ts` — Send resolved root paths

```typescript
function getProjectHeaders(): Record<string, string> {
  const project = (window as any).__ZIYA_CURRENT_PROJECT__;
  if (!project) {
    // Legacy fallback
    const path = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
    return path ? { 'X-Project-Root': path } : {};
  }

  // Resolve roots through session overrides
  const resolved = resolveRootPaths(project);
  const resolvedPaths = resolved.map(r => r.path).join(',');

  return {
    'X-Project-Id': project.id,
    'X-Project-Roots': resolvedPaths,
    // Legacy header: primary root
    'X-Project-Root': resolved[0]?.path || '',
  };
}

// Resolve overrides from sessionStorage
function resolveRootPaths(project: Project): ResolvedRoot[] {
  const raw = sessionStorage.getItem(
    `ZIYA_ROOT_OVERRIDES_${project.id}`
  );
  const overrides: Record<string, string> = raw ? JSON.parse(raw) : {};
  return project.roots.map(root => ({
    id: root.id,
    label: root.label,
    path: overrides[root.id] || root.path,
    isOverridden: root.id in overrides,
    canonicalPath: root.path,
  }));
}
```

### 2. `FolderContext.tsx` — Multi-root tree

Multiple roots become top-level tree nodes. Keys are `rootId:relative/path`.

```
📁 API  (/home/user/payments-api)
   📁 src/
   📁 tests/
📁 Frontend  (/home/user/payments-fe)
   📁 src/
   📁 public/
```

For single-root projects (root id = `""`), bare keys — no prefix.

### 3. Root management panel

Accessible from project settings / ProjectSwitcher:

```
┌──────────────────────────────────────────────────────────┐
│ Project Roots                                     [+ Add] │
├──────────────────────────────────────────────────────────┤
│ ⭐ API         /home/user/payments-api                    │
│    ≡  [Repoint] [Rename]                        [✕]      │
│                                                           │
│    Frontend    /home/user/payments-fe                      │
│    ≡  [Repoint] [Rename]                        [✕]      │
│                                                           │
│    Protos      /home/user/shared-protos    ⚠️ path missing │
│    ≡  [Repoint] [Rename]                        [✕]      │
├──────────────────────────────────────────────────────────┤
│ ⭐ = primary root · Drag to reorder            [Done]     │
└──────────────────────────────────────────────────────────┘
```

**Mid-session "Add Root"**: This is the most common entry point. A user is
working in a single-root project and realizes they need files from another
directory. The flow:

1. User clicks gear icon on current project → Project Settings
2. The settings modal now has a "Roots" section (above Write Policy)
3. User clicks `[+ Add Root]` → path input + optional label
4. On confirm, the backend:
   - Promotes the project from single-root to multi-root (if first add)
   - Assigns the existing root a UUID (was `""`)
   - Rewrites bare-path context refs to qualified refs
   - Appends the new root with a new UUID
5. The file tree immediately rescans, showing both roots as top-level nodes
6. The user's existing conversations, contexts, and checked files are
   unaffected — they now resolve through the promoted primary root

This is a **permanent** project change (unlike session overrides). The
current `ProjectManagerModal` settings sub-view is the right place for this
— it already handles write policy and will gain the root management panel
as a new section above it.

### 4. Session override controls

When session overrides are active, each overridden root shows its status
in the root management panel AND in the file tree header. A small
`🔀 session override` label appears next to the resolved path. The root
panel shows:

```
│    API         /home/user/payments-api                    │
│                                                           │
│    📌 This tab: /home/user/payments-api-v2                │
│    [Reset to canonical]  [Make permanent (repoint)]       │
```

"Make permanent" does a repoint — it updates the canonical path in
project.json, affecting all tabs.

### 5. Merge UI

From project list, right-click a project:

```
┌────────────────────────────────────────────────────────────┐
│ Merge "Payments v2" into another project                    │
│                                                             │
│ Target project:  [Payments — Full Stack        ▼]           │
│                                                             │
│ How to merge:                                               │
│  ● Add as new root                                          │
│    Keeps both directories active in the target project      │
│                                                             │
│  ○ Replace existing root                                    │
│    Which root?  [API (/home/user/payments-api)  ▼]          │
│    Updates its path to: /home/user/payments-v2              │
│                                                             │
│ This will move:                                             │
│  • 12 conversations                                         │
│  • 3 contexts                                               │
│  • 1 custom skill                                           │
│                                                             │
│ "Payments v2" will be deleted after merge.                   │
│                                                             │
│                              [Cancel]  [Merge]              │
└────────────────────────────────────────────────────────────┘
```

---

## Rollout Plan

### Phase 1: Schema + auto-migration (backend, no UX change)
- Add `ProjectRoot` model with `id`, `label`, `path`
- Add `roots` field to `Project`
- Implement `_normalize_roots` in storage (auto-migration on read)
- Dual-write `path` + `roots` on every save
- Add `resolve_file_ref()` utility
- All existing behavior preserved; frontend sends same headers

### Phase 2: Launch resolution + stale detection
- `/resolve` endpoint with 3-step algorithm + neighborhood scan
- Stale path detection and "Repoint" prompt on open
- Project chooser dialog for ambiguous launches
- Frontend sends `X-Project-Id` alongside `X-Project-Root`

### Phase 3: Multi-root file tree + root management UX
- Multi-root file tree response from backend
- `FolderContext`: render multi-root tree, qualify keys with root ids
- Root management panel (add, repoint, rename, remove, reorder)
- "Add root" action directly in project settings (mid-session)
- Write policy checks all roots
- Context creation across roots
- Multi-root indicator in project header
- `X-Project-Roots` header: frontend sends resolved paths

### Phase 4: Session overrides
- `sessionStorage`-based root overrides per tab
- `resolveRootPaths()` applies overrides before sending headers
- Launch flow: "Open with this path for this session" option
- Override banner in sidebar + file tree root annotation
- Override management in root panel ("Use different path in this tab")
- Reset-to-canonical action

### Phase 5: Merge + polish
- Merge endpoint and UI (merge-as-new-root, merge-with-repoint)
- Conversation + context migration during merge
- Search across all roots
- File watcher covers all roots
- Bare-path → qualified-path rewrite when promoting to multi-root
- "You've been overriding for N sessions, permanently repoint?" nudge
- `pathHistory` per root for quick switching between known locations

---

## Appendix A: Future — Equivalence Detection

The revision-equivalence problem ("this is the same repo at a new path") is
solved in Phase 1-3 by manual repointing. Future work could automate this:

1. **Git remote URL matching**: When a root is added, capture `git remote
   get-url origin`. On launch in an unknown dir, check if its git remote
   matches a stored root's URL → offer to repoint.

2. **Package identity matching**: Match on `package.json` name,
   `pyproject.toml` name, `Cargo.toml` name, etc.

3. **Marker file**: A `.ziya-root-id` file in a directory declares "I am
   root r-001 of project abc-123", surviving moves and clones.

None of these are needed for multi-root to be useful. They optimize the
experience for users who frequently move between checkouts.

## Appendix B: Interaction with Delegates

Multi-root projects interact naturally with the delegate/TaskPlan system:

- A delegate can be scoped to a specific root (its Context references files
  qualified with that root's id)
- The orchestrator can decompose tasks across roots ("update the API proto
  AND the frontend types that consume it")
- MemoryCrystals reference files with qualified paths, surviving repoints

No delegate schema changes needed — the existing `contextId` mechanism
inherits multi-root support once contexts use qualified paths.
