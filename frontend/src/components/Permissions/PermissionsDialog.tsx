/**
* PermissionsDialog — single-pane file browser with permission
* checkboxes embedded directly on each row.
*
* The columns express three independent flags with implicit cascade:
*
*   +    scope: this path is in scope at all (the model is allowed
*               to touch it).  Implicit for in-project paths since
*               read inside the project root is free; explicit for
*               out-of-project paths.
*   W    writable: the model may write here.  Cascades to descendants
*                  when set on a directory.
*   Ctx  context: file contents are preloaded into the system prompt
*                 (file-only — directories show ``—``).
*
* MUIFileExplorer-style tri-state: a directory whose direct grant
* covers it shows a solid check; a directory whose ancestor is
* granted shows a faded check (inherited, click is a no-op); a
* directory whose only some descendants are granted shows the
* indeterminate dash.
*
* No nested modal.  All controls live on the rows you're looking at.
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
 Dialog, DialogTitle, DialogContent, DialogActions,
 Button, Checkbox, IconButton, Typography, Box, Tooltip,
 Tabs, Tab,
} from '@mui/material';
import FolderIcon from '@mui/icons-material/Folder';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import HomeIcon from '@mui/icons-material/Home';
import { useFolderContext } from '../../context/FolderContext';
import { useProject } from '../../context/ProjectContext';
import {
 applyClick, cellState, type CellState,
 type PermissionColumn, type PermissionSets, type PathInfo, hasDescendantInSets,
} from './permissionsTree';

export interface PermissionEntry {
  path: string;
  is_dir?: boolean;
  read?: boolean;
  write?: boolean;
  context?: boolean;
}

interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

type PermTab = 'files' | 'tools' | 'skills' | 'shell';

/**
 * Combined payload emitted by the dialog's single Save action.
 * Replaces the previous four-callback API which suffered from
 * stale-closure clobbering when the parent's state-update batched
 * the four sequential calls in one tick.
 */
export interface PermissionsSavePayload {
  entries: PermissionEntry[];
  tools: string[];
  skills: string[];
  shellCommands: string[];
}

interface Props {
  open: boolean;
  title?: string;
 /** Initial entry list (caller-owned; dialog edits a local copy until Save). */
  entries: PermissionEntry[];
  /** Initial tool allowlist.  Empty/undefined = all tools allowed (no filter). */
  tools?: string[];
  /** Initial skill allowlist.  Empty/undefined = all skills allowed (no filter). */
  skills?: string[];
  onClose: () => void;
  /** Initial shell-command grants.  Each entry is either a literal
   * first-token grant (e.g. "pytest") or, with "re:" prefix, a regex
   * against the full command line. */
  shellCommands?: string[];
  /** Single combined save: parent applies all four pieces in one
   *  state update so no field clobbers another via stale closures. */
  onSave: (payload: PermissionsSavePayload) => void;
}

const TOOLTIPS: Record<PermissionColumn, string> = {
scope:    'Read — task may read this path. Auto-granted (greyed out) for paths already in the project file tree.',
writable: 'Write — task may write here (additive over global write policy; cannot bypass always_blocked).',
context:  'Context — preload file contents into the system prompt (counts against context budget; files only).',
};

// ── Helpers ──────────────────────────────────────────────────

/**
* Convert the caller's ``PermissionEntry[]`` into the three internal
* sets used by the cascade engine.
*/
function entriesToSets(entries: PermissionEntry[]): PermissionSets {
 const scope = new Set<string>();
 const writable = new Set<string>();
 const context = new Set<string>();
 for (const e of entries) {
   // Any entry at all is "in scope" (legacy entries didn't carry a
   // separate scope flag; presence == in scope).
   scope.add(e.path);
   if (e.write) writable.add(e.path);
   if (e.context && !e.is_dir) context.add(e.path);
 }
 return { scope, writable, context };
}

/**
* Convert the internal sets back into ``PermissionEntry[]`` for save.
* The ``isDir`` lookup uses the in-memory tree first, falling back
* to the lazy browse cache, finally heuristic (no extension == dir).
*/
function setsToEntries(
 sets: PermissionSets,
 isDirOf: (path: string) => boolean,
): PermissionEntry[] {
 const out: PermissionEntry[] = [];
 for (const path of sets.scope) {
   const is_dir = isDirOf(path);
   out.push({
     path,
     is_dir,
     read: true,                        // any in-scope entry is readable
     write: sets.writable.has(path),
     // Context now cascades: a directory entry with context=true
     // tells the executor to preload every file under the subtree.
     // (Pre-Slice-1a behavior dropped dir-context entries here; we
     // keep them so the cascade survives a save / reload round-trip.)
     context: sets.context.has(path),
   });
 }
 return out.sort((a, b) => a.path.localeCompare(b.path));
}

/**
* Walk the project ``folders`` map (path → metadata) and produce a
* flat ``PathInfo[]``.  Used as the descendant-knowledge ``universe``
* for tri-state computation inside the project root.
 *
 * ``Folders`` is a *nested* tree: each entry's ``children`` is itself
 * a ``Folders``.  A node is a directory iff it has a ``children``
 * key (even when empty); otherwise it's a file.
*/
function projectUniverse(
  folders: Record<string, { token_count?: number; children?: any }> | undefined | null,
): PathInfo[] {
 const out: PathInfo[] = [];
  const walk = (
    node: Record<string, { token_count?: number; children?: any }>,
    prefix: string,
  ): void => {
    for (const [name, meta] of Object.entries(node)) {
      if (!name) continue;
      const path = prefix ? `${prefix}/${name}` : name;
      const hasChildren = meta && typeof meta === 'object' && 'children' in meta;
      const is_dir = !!hasChildren;
      out.push({ path, is_dir });
      if (is_dir && meta.children && typeof meta.children === 'object') {
        walk(meta.children as Record<string, { token_count?: number; children?: any }>, path);
      }
    }
  };
  if (folders) walk(folders as Record<string, { token_count?: number; children?: any }>, '');
 return out;
}

/**
* From a flat path universe, return entries whose parent equals
* ``parentPath`` ("" for project root).  Sorted: dirs first, then
* by name.
*/
function childrenOf(universe: PathInfo[], parentPath: string): BrowseEntry[] {
 const prefix = parentPath ? parentPath + '/' : '';
 const seen = new Set<string>();
 const out: BrowseEntry[] = [];
 for (const p of universe) {
   if (!p.path.startsWith(prefix)) continue;
   const rest = p.path.slice(prefix.length);
   if (!rest || rest.includes('/')) continue;
   if (seen.has(p.path)) continue;
   seen.add(p.path);
   out.push({ name: rest, path: p.path, is_dir: p.is_dir });
 }
 out.sort((a, b) => {
   if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
   return a.name.localeCompare(b.name);
 });
 return out;
}

/** Parent path of ``p`` ("" for top-level paths, null for empty). */
function parentOf(p: string): string | null {
 if (!p) return null;
 const i = p.lastIndexOf('/');
 return i < 0 ? '' : p.slice(0, i);
}

// ── Component ────────────────────────────────────────────────

export const PermissionsDialog: React.FC<Props> = ({
open, title = 'Permissions', entries, tools, skills, shellCommands,
onClose, onSave,
}) => {
const { folders, checkedKeys } = useFolderContext();
 const { skills: availableSkills, isLoadingSkills, currentProject } = useProject();

 // Working copy as three sets.  Reset on (re)open.
 const [sets, setSets] = useState<PermissionSets>({
   scope: new Set(), writable: new Set(), context: new Set(),
 });
 const [tab, setTab] = useState<PermTab>('files');
 // Tools tab state.  ``selectedTools`` is the working copy of the
 // allowlist; ``availableTools`` is the union of tools the server
 // reports plus any names already in ``tools`` (so a previously
 // saved grant for a now-disconnected tool still renders).
 const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set());
 const [availableTools, setAvailableTools] = useState<{ name: string; description?: string; server?: string }[] | null>(null);
 const [toolsLoadError, setToolsLoadError] = useState<string | null>(null);
 // Skills tab state — same shape as tools.
 const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());
 // Shell tab state.  Stored as a single textarea — one grant per
 // line — to keep the editing UX simple.  Persisted as ``string[]``.
 const [shellGrantsText, setShellGrantsText] = useState<string>('');

 // Currently displayed directory.  '' = project root; null = above
 // project root (filesystem-level browsing via lazy fetch).
 const [currentPath, setCurrentPath] = useState<string>('');

 // Lazy directory listings keyed by path (for out-of-project dirs).
 const [lazyDirs, setLazyDirs] = useState<Record<string, BrowseEntry[]>>({});
 const [loading, setLoading] = useState(false);

 useEffect(() => {
   if (!open) return;
   setTab('files');
   setSets(entriesToSets(entries));
   setCurrentPath('');
   setLazyDirs({});
   setSelectedTools(new Set(tools ?? []));
   setSelectedSkills(new Set(skills ?? []));
   setShellGrantsText((shellCommands ?? []).join('\n'));
 }, [open, entries, tools, skills, shellCommands]);

 // Lazy-load the MCP tool catalog when the Tools tab is first opened.
 useEffect(() => {
   if (!open || tab !== 'tools' || availableTools !== null) return;
   let cancelled = false;
   (async () => {
     try {
       const res = await fetch('/api/mcp/tools');
       if (!res.ok) throw new Error(`HTTP ${res.status}`);
       const data = await res.json();
       if (cancelled) return;
       setAvailableTools(Array.isArray(data?.tools) ? data.tools : []);
     } catch (e: any) {
       if (!cancelled) setToolsLoadError(String(e?.message || e));
     }
   })();
   return () => { cancelled = true; };
 }, [open, tab, availableTools]);

 // ── Project tree (eager, in-memory) ──────────────────────
 const universe = useMemo(() => projectUniverse(folders ?? {}), [folders]);
 const inProjectPaths = useMemo(() => {
   const s = new Set<string>();
   for (const u of universe) s.add(u.path);
   return s;
 }, [universe]);

 // Paths the user has selected in the project file tree.  These are
 // already in the model's read-context via the standard project
 // include path, so they should show as ``inherited`` in the ``+``
 // (scope) column rather than empty — visual reassurance that the
 // task will see them without the user adding an explicit grant.
 const projectContextSet = useMemo(
   () => new Set(checkedKeys.map(String)),
   [checkedKeys],
 );

 // Project-level writable grants: ``safe_write_paths`` (literal paths,
 // typically directories) and ``allowed_write_patterns`` (regex).
 // These come from the project's ``WritePolicy`` and represent grants
 // the user has already approved globally — surface them here as
 // inherited Write so the user can see the task already has write
 // access without adding a per-task grant.
 const writePolicy = currentProject?.settings?.writePolicy;
 const projectSafeWritePaths = useMemo(
   () => (writePolicy?.safe_write_paths ?? []).map(p => p.replace(/\/+$/, '')),
   [writePolicy?.safe_write_paths],
 );
 const projectWritePatterns = useMemo(() => {
   return (writePolicy?.allowed_write_patterns ?? [])
     .map(pat => {
       try { return new RegExp(pat); }
       catch {
         // Malformed regex in project settings — silently skip rather
         // than crash the dialog.  ProjectManagerModal validates at
         // entry time, so this only fires on legacy data.
         return null;
       }
     })
     .filter((r): r is RegExp => r !== null);
 }, [writePolicy?.allowed_write_patterns]);

 const isDirOf = useCallback((p: string): boolean => {
   const inProj = universe.find(u => u.path === p);
   if (inProj) return inProj.is_dir;
   for (const list of Object.values(lazyDirs)) {
     const hit = list.find(e => e.path === p);
     if (hit) return hit.is_dir;
   }
   // Heuristic: trailing slash or no extension → dir.
   return !p.includes('.') || p.endsWith('/');
 }, [universe, lazyDirs]);

 // ── Lazy fetch when navigating outside project ───────────
 const fetchLazy = useCallback(async (path: string) => {
   if (lazyDirs[path]) return;
   setLoading(true);
    try {
      const r = await fetch(`/api/browse-directory?path=${encodeURIComponent(path)}`);
      if (!r.ok) {
       console.error('browse-directory failed:', await r.text()); return;
     }
      const data = await r.json();
     const list: BrowseEntry[] = (data.entries ?? []).map((e: any) => ({
       name: e.name, path: e.path, is_dir: !!e.is_dir,
     }));
     list.sort((a, b) => {
       if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
       return a.name.localeCompare(b.name);
     });
     setLazyDirs(prev => ({ ...prev, [path]: list }));
    } catch (err) {
      console.error('browse-directory error:', err);
    } finally {
     setLoading(false);
    }
 }, [lazyDirs]);

 const isOutOfProject = currentPath !== '' && !inProjectPaths.has(currentPath);

 useEffect(() => {
   if (!open) return;
   if (isOutOfProject) fetchLazy(currentPath);
 }, [open, currentPath, isOutOfProject, fetchLazy]);

 // ── Children of currentPath ──────────────────────────────
 const rows: BrowseEntry[] = useMemo(() => {
   if (!isOutOfProject) {
     // In-project: derive from universe.
     return childrenOf(universe, currentPath);
   }
   return lazyDirs[currentPath] ?? [];
 }, [isOutOfProject, universe, currentPath, lazyDirs]);

 // ── Click handlers ───────────────────────────────────────
 const onCheck = (path: string, isDir: boolean, column: PermissionColumn) => {
   setSets(prev => applyClick(path, column, isDir, prev, universe));
 };

 const navigate = (target: string | null) => {
   if (target === null) return;
   setCurrentPath(target);
 };

 const computedState = (path: string, isDir: boolean, column: PermissionColumn): CellState => {
   const u = isOutOfProject ? null : universe;
   const base = cellState(path, isDir, column, sets[column], u);
   // Overlays for in-project paths.  Direct / indeterminate states
   // (the user has an explicit grant or a descendant grant) always
   // win over project-level inheritance — only ``empty`` (and ``na``
   // for context-on-dir) gets overlaid.
   if (!isOutOfProject) {
     // Read (scope) column: any in-project path is readable by virtue
     // of project membership — surface that as inherited so the user
     // sees Read is already granted without needing a per-task entry.
     // Out-of-project paths (isOutOfProject branch above) get no
     // overlay and require an explicit grant.
     if (column === 'scope' && base.kind === 'empty') {
       return { kind: 'inherited', from: '(project)' };
     }
     // Context column: same set of paths.  These files are literally
     // preloaded into the system prompt — that's what "checked in
     // project tree" means.  Override ``na`` for dirs too: the dir
     // itself isn't preloaded but its contents are, so the inherited
     // marker is the right visual.
     if (column === 'context' && (base.kind === 'empty' || base.kind === 'na')) {
       if (projectContextSet.has(path)) {
         return { kind: 'inherited', from: '(project context)' };
       }
       for (const m of projectContextSet) {
         if (isAncestorPath(m, path)) {
           return { kind: 'inherited', from: m };
         }
       }
     }
     // Write column: project's WritePolicy grants (safe_write_paths
     // + allowed_write_patterns).  An exact match or ancestor match
     // on safe_write_paths counts; pattern matches use the raw path.
     if (column === 'writable' && base.kind === 'empty') {
       const norm = path.replace(/\/+$/, '');
       for (const sp of projectSafeWritePaths) {
         if (!sp) continue;
         if (norm === sp || norm.startsWith(sp + '/')) {
           return { kind: 'inherited', from: sp || '(project policy)' };
         }
       }
       for (const re of projectWritePatterns) {
         if (re.test(path)) {
           return { kind: 'inherited', from: `(pattern: ${re.source})` };
         }
       }
     }
   }
   return base;
 };

 // Inline ancestor check (avoids importing the helper from
 // permissionsTree just for one call).
 const isAncestorPath = (a: string, b: string): boolean => {
   if (!a) return b !== '';
   return b === a || b.startsWith(a + '/');
 };

 const counts = {
   paths: sets.scope.size,
   writable: sets.writable.size,
   context: sets.context.size,
 };

 const handleSave = () => {
   // Split shell grants on newlines, trim each, drop empties and pure
   // comments. Comments (lines beginning with ``#``) are useful for
   // explaining why a grant exists when the card is shared.
   const shellGrants = shellGrantsText
     .split('\n')
     .map(l => l.trim())
     .filter(l => l && !l.startsWith('#'));
   onSave({
     entries: setsToEntries(sets, isDirOf),
     tools: Array.from(selectedTools).sort(),
     skills: Array.from(selectedSkills).sort(),
     shellCommands: shellGrants,
   });
   onClose();
 };

 // ── Render ───────────────────────────────────────────────
 return (
   <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
     <DialogTitle>{title}</DialogTitle>
     <DialogContent dividers sx={{ p: 0 }}>
       {/* Tab strip */}
       <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
         <Tabs
           value={tab}
           onChange={(_, v) => setTab(v as PermTab)}
           variant="standard"
           sx={{ minHeight: 36, '& .MuiTab-root': { minHeight: 36, py: 0.5 } }}
         >
           <Tab value="files" label="Files" />
           <Tab value="tools" label="Tools" />
           <Tab value="skills" label="Skills" />
           <Tab value="shell" label="Shell" />
         </Tabs>
       </Box>

       {tab === 'files' && <>
       {/* Explanatory banner — clarifies the auto-Read behaviour
          and points the user at the project tree if they need a
          path that isn't currently visible. */}
       <Box sx={{
         px: 2, py: 1.25,
         borderBottom: 1, borderColor: 'divider',
         bgcolor: 'action.selected',
         fontSize: 12,
         color: 'text.secondary',
         lineHeight: 1.5,
       }}>
         <strong>Read</strong> is inherited (greyed out) for everything in
         your project — project membership grants Read access automatically.
         <strong> Context</strong> shows as inherited for files you've ticked
         in the project file tree (those are preloaded into the prompt).
         <strong> Write</strong> shows as inherited for paths covered by the
         project's write policy (safe paths or allowed patterns).  Use this
         dialog to add explicit per-task grants on top, or to grant access
         to paths outside the project.  To add a project path that isn't
         shown, select it in the project file tree first.
       </Box>

       {/* Path bar */}
       <Box sx={{
         display: 'flex', alignItems: 'center', gap: 1,
         px: 2, py: 1, borderBottom: 1, borderColor: 'divider',
         bgcolor: 'action.hover',
       }}>
         <IconButton size="small" onClick={() => navigate('')} title="Project root">
           <HomeIcon fontSize="small" />
          </IconButton>
         <Typography
           variant="body2"
           sx={{ fontFamily: 'monospace', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}
           title={currentPath || '(project root)'}
         >
           {currentPath || '(project root)'}
         </Typography>
         {loading && <Typography variant="caption" color="text.secondary">loading…</Typography>}
        </Box>

       {/* Column header */}
       <Box sx={{
         display: 'grid',
         gridTemplateColumns: '1fr 56px 56px 56px',
         alignItems: 'center', gap: 1, px: 2, py: 0.5,
         borderBottom: 1, borderColor: 'divider',
         fontSize: 11, color: 'text.secondary',
         // Reserve the same scrollbar gutter the body below uses
         // (scrollbarGutter: 'stable') so column headers stay aligned
         // whether the rows scroll or not.
         pr: 'calc(16px + var(--scrollbar-gutter, 15px))',
        }}>
         <span>Path</span>
         <Tooltip title={TOOLTIPS.scope}>
           <span style={{ textAlign: 'center', fontWeight: 500, cursor: 'help' }}>Read</span>
         </Tooltip>
         <Tooltip title={TOOLTIPS.writable}>
           <span style={{ textAlign: 'center', fontWeight: 500, cursor: 'help' }}>Write</span>
         </Tooltip>
         <Tooltip title={TOOLTIPS.context}>
           <span style={{ textAlign: 'center', fontWeight: 500, cursor: 'help' }}>Context</span>
         </Tooltip>
       </Box>

       {/* Body — single scrollable list */}
        <Box sx={{ maxHeight: 480, overflow: 'auto', scrollbarGutter: 'stable' }}>
         {/* .. row */}
         {currentPath !== null && parentOf(currentPath) !== null && (
           <Row
             icon={<FolderIcon fontSize="small" />}
             name=".."
             onNameClick={() => navigate(parentOf(currentPath))}
           />
         )}

         {rows.length === 0 && !loading && (
           <Box sx={{ p: 2 }}>
             <Typography variant="body2" color="text.secondary">
               (empty)
             </Typography>
           </Box>
         )}

         {rows.map(row => (
           <Row
             key={row.path}
             icon={row.is_dir
               ? <FolderIcon fontSize="small" color={hasDescendantInSets(row.path, sets) ? 'primary' : 'action'} />
               : <InsertDriveFileIcon fontSize="small" color="action" />}
             name={row.name}
             /* Mirror MUIFileExplorer's behavior: when a configured
              * grant exists somewhere under this directory, mark the
              * folder so it's visible without expanding the subtree. */
             descendantConfigured={row.is_dir && hasDescendantInSets(row.path, sets)}
             onNameClick={row.is_dir ? () => navigate(row.path) : undefined}
             cells={(['scope', 'writable', 'context'] as PermissionColumn[]).map(col => ({
               column: col,
               state: computedState(row.path, row.is_dir, col),
               onClick: () => onCheck(row.path, row.is_dir, col),
             }))}
           />
         ))}
       </Box>

       {/* Live counters footer */}
       <Box sx={{
         display: 'flex', alignItems: 'center', justifyContent: 'space-between',
         px: 2, py: 1, borderTop: 1, borderColor: 'divider',
         bgcolor: 'action.hover', fontSize: 12,
       }}>
         <Typography variant="caption" color="text.secondary">
           {counts.paths} paths · {counts.writable} writable · {counts.context} in context
         </Typography>
        </Box>
       </>}

       {tab === 'tools' && (
         <Box sx={{ p: 2 }}>
           <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
             {selectedTools.size === 0
               ? 'No allowlist — task may use all available tools.'
               : `Allowlist active — task limited to ${selectedTools.size} tool${selectedTools.size === 1 ? '' : 's'}.`}
             {' '}
             <em>Empty selection = all allowed.</em>
           </Typography>
           {toolsLoadError && (
             <Typography variant="caption" color="error" sx={{ display: 'block', mb: 1 }}>
               Could not load tool catalog: {toolsLoadError}
             </Typography>
           )}
           {availableTools === null && !toolsLoadError && (
             <Typography variant="caption" color="text.secondary">Loading…</Typography>
           )}
           {availableTools !== null && (() => {
             // Merge server-reported tools with any saved grants for
             // tools that aren't currently exposed (renamed/disabled
             // server) so the user can still uncheck them.
             const serverNames = new Set(availableTools.map(t => t.name));
             const orphans = Array.from(selectedTools).filter(n => !serverNames.has(n));
             const all = [
               ...availableTools,
               ...orphans.map(n => ({ name: n, description: '(not currently available)', server: undefined })),
             ].sort((a, b) => a.name.localeCompare(b.name));
             if (all.length === 0) {
               return (
                 <Typography variant="body2" color="text.secondary">
                   No tools registered.
                 </Typography>
               );
             }
             const toggle = (name: string) => {
               setSelectedTools(prev => {
                 const next = new Set(prev);
                 if (next.has(name)) next.delete(name); else next.add(name);
                 return next;
               });
             };
             return (
               <Box sx={{ maxHeight: 360, overflowY: 'auto', border: 1, borderColor: 'divider', borderRadius: 1 }}>
                 {all.map(t => (
                   <Box
                     key={t.name}
                     sx={{
                       display: 'flex', alignItems: 'center', gap: 1,
                       px: 1.5, py: 0.5, borderBottom: 1, borderColor: 'divider',
                       '&:last-child': { borderBottom: 0 },
                       cursor: 'pointer',
                       '&:hover': { bgcolor: 'action.hover' },
                     }}
                     onClick={() => toggle(t.name)}
                   >
                     <Checkbox
                       size="small"
                       checked={selectedTools.has(t.name)}
                       onChange={() => toggle(t.name)}
                       onClick={e => e.stopPropagation()}
                     />
                     <Box sx={{ minWidth: 0, flex: 1 }}>
                       <Typography variant="body2" sx={{ fontFamily: 'ui-monospace, monospace' }}>
                         {t.name}
                         {t.server && <span style={{ color: 'var(--mui-palette-text-secondary)', fontSize: 11, marginLeft: 6 }}>({t.server})</span>}
                       </Typography>
                       {t.description && (
                         <Typography variant="caption" color="text.secondary" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                           {t.description}
                         </Typography>
                       )}
                     </Box>
                   </Box>
                 ))}
               </Box>
             );
           })()}
         </Box>
       )}

       {tab === 'skills' && (
         <Box sx={{ p: 2 }}>
           <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
             {selectedSkills.size === 0
               ? 'No allowlist — task may use all available skills.'
               : `Allowlist active — task limited to ${selectedSkills.size} skill${selectedSkills.size === 1 ? '' : 's'}.`}
             {' '}
             <em>Empty selection = all allowed. (Advisory until skill enforcement lands.)</em>
           </Typography>
           {isLoadingSkills && (
             <Typography variant="caption" color="text.secondary">Loading…</Typography>
           )}
           {!isLoadingSkills && (() => {
             const known = availableSkills ?? [];
             const knownIds = new Set(known.map(s => s.id));
             const orphans = Array.from(selectedSkills).filter(id => !knownIds.has(id));
             const all = [
               ...known.map(s => ({ id: s.id, name: s.name, description: s.description, builtin: s.isBuiltIn })),
               ...orphans.map(id => ({ id, name: id, description: '(not currently available)', builtin: false })),
             ].sort((a, b) => a.name.localeCompare(b.name));
             if (all.length === 0) {
               return (
                 <Typography variant="body2" color="text.secondary">
                   No skills registered.
                 </Typography>
               );
             }
             const toggle = (id: string) => {
               setSelectedSkills(prev => {
                 const next = new Set(prev);
                 if (next.has(id)) next.delete(id); else next.add(id);
                 return next;
               });
             };
             return (
               <Box sx={{ maxHeight: 360, overflowY: 'auto', border: 1, borderColor: 'divider', borderRadius: 1 }}>
                 {all.map(s => (
                   <Box
                     key={s.id}
                     sx={{
                       display: 'flex', alignItems: 'center', gap: 1,
                       px: 1.5, py: 0.5, borderBottom: 1, borderColor: 'divider',
                       '&:last-child': { borderBottom: 0 },
                       cursor: 'pointer',
                       '&:hover': { bgcolor: 'action.hover' },
                     }}
                     onClick={() => toggle(s.id)}
                   >
                     <Checkbox
                       size="small"
                       checked={selectedSkills.has(s.id)}
                       onChange={() => toggle(s.id)}
                       onClick={e => e.stopPropagation()}
                     />
                     <Box sx={{ minWidth: 0, flex: 1 }}>
                       <Typography variant="body2">
                         {s.name}
                         {s.builtin && <span style={{ color: 'var(--mui-palette-text-secondary)', fontSize: 11, marginLeft: 6 }}>(built-in)</span>}
                       </Typography>
                       {s.description && (
                         <Typography variant="caption" color="text.secondary" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                           {s.description}
                         </Typography>
                       )}
                     </Box>
                   </Box>
                 ))}
               </Box>
             );
           })()}
         </Box>
       )}

       {tab === 'shell' && (
         <Box sx={{ p: 2 }}>
           <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
             Per-task shell command grants.  Each non-blank, non-comment
             line is one grant.  Grants are <strong>additive</strong> over
             the global shell policy: they bypass the destructive-command
             block (rm/mv/cp/...) and the script-write heuristic for
             interpreter one-liners, but they cannot override
             <code style={{ margin: '0 4px' }}>always_blocked</code>
             (sudo, vi, …) or output-redirection rules.
           </Typography>
           <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
             Format:&nbsp;
             <code>pytest</code> &mdash; literal first-token match
             (any <code>pytest …</code> invocation).&nbsp;
             <code>re:^make\s+test(:\w+)?$</code> &mdash; regex against
             the full command line.&nbsp;
             Lines starting with <code>#</code> are comments.
           </Typography>
           <Box
             component="textarea"
             value={shellGrantsText}
             onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
               setShellGrantsText(e.target.value)
             }
             placeholder={
               '# One grant per line\n' +
               'pytest\n' +
               'make test\n' +
               're:^git\\s+(status|diff|log)\\b'
             }
             spellCheck={false}
             sx={{
               width: '100%',
               minHeight: 220,
               p: 1,
               border: 1,
               borderColor: 'divider',
               borderRadius: 1,
               fontFamily: 'ui-monospace, monospace',
               fontSize: 13,
               resize: 'vertical',
               bgcolor: 'background.paper',
               color: 'text.primary',
               outline: 'none',
               '&:focus': { borderColor: 'primary.main' },
             }}
           />
           <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
             {(() => {
               const live = shellGrantsText
                 .split('\n')
                 .map(l => l.trim())
                 .filter(l => l && !l.startsWith('#'));
               if (live.length === 0) return 'No grants — base shell policy applies unchanged.';
               return `${live.length} grant${live.length === 1 ? '' : 's'} active for this task.`;
             })()}
           </Typography>
         </Box>
       )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
       <Button variant="contained" onClick={handleSave}>Save</Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Single row ───────────────────────────────────────────────

interface RowProps {
 icon: React.ReactNode;
 name: string;
 descendantConfigured?: boolean;
 onNameClick?: () => void;
 cells?: Array<{
   column: PermissionColumn;
   state: CellState;
   onClick: () => void;
 }>;
}

const Row: React.FC<RowProps> = ({ icon, name, descendantConfigured, onNameClick, cells }) => (
 <Box sx={{
   display: 'grid',
   gridTemplateColumns: '1fr 56px 56px 56px',
   alignItems: 'center', gap: 1, px: 2, py: 0.25,
   borderBottom: 1, borderColor: 'divider',
   '&:hover': { bgcolor: 'action.hover' },
 }}>
   <Box
     onClick={onNameClick}
     sx={{
       display: 'flex', alignItems: 'center', gap: 1,
       cursor: onNameClick ? 'pointer' : 'default',
       overflow: 'hidden',
       '&:hover': onNameClick ? { textDecoration: 'underline' } : undefined,
     }}
   >
     {icon}
     <Typography
       variant="body2"
       sx={{
         fontFamily: 'monospace', overflow: 'hidden',
         textOverflow: 'ellipsis', whiteSpace: 'nowrap',
         fontWeight: descendantConfigured ? 600 : 400,
         color: descendantConfigured ? 'primary.main' : undefined,
       }}
     >
       {name}
     </Typography>
   </Box>
   {(cells ?? []).map(c => (
     <Tooltip key={c.column} title={TOOLTIPS[c.column]}>
       <span style={{ display: 'flex', justifyContent: 'center' }}>
         <PermCheckbox state={c.state} onClick={c.onClick} />
       </span>
     </Tooltip>
   ))}
   {/* Pad if the .. row has no cells */}
   {!cells && <><span /><span /><span /></>}
 </Box>
);

// ── Tri-state checkbox (renders the ``CellState``) ───────────

const PermCheckbox: React.FC<{ state: CellState; onClick: () => void }> = ({ state, onClick }) => {
 if (state.kind === 'na') {
   return <Typography component="span" variant="caption" color="text.disabled">—</Typography>;
 }
 const checked = state.kind === 'direct' || state.kind === 'inherited';
 const indeterminate = state.kind === 'indeterminate';
 // Inherited cells are visually faded and not interactive — the only
 // way to revoke is to remove the ancestor grant.
 const inherited = state.kind === 'inherited';
 return (
   <Checkbox
     size="small"
     checked={checked}
     indeterminate={indeterminate}
     onChange={onClick}
     disabled={inherited}
     sx={inherited ? { opacity: 0.45 } : undefined}
     title={inherited ? `inherited from ${state.from}` : undefined}
   />
 );
};
