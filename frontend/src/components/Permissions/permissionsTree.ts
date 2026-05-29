/**
 * permissionsTree — tri-state + cascade math for the permissions
 * dialog.  Pure functions, no React, no DOM, no I/O.
 *
 * Model
 * -----
 * The dialog tracks three independent path sets:
 *
 *   - ``scope``    — the path is in scope at all (the ``+`` column).
 *                    Implicit for in-project paths (read is free
 *                    inside the project root); explicit for
 *                    out-of-project paths.
 *   - ``writable`` — the model may write here (the ``W`` column).
 *                    Cascades to descendants when set on a directory.
 *   - ``context``  — file contents are preloaded into the system
 *                    prompt (the ``Ctx`` column).  File-only.
 *
 * Each set holds full path strings; cascade is *implicit* (a single
 * directory entry covers its whole subtree at storage time).  The
 * helpers below compute UI tri-state per (path, column) by walking
 * up to find an ancestor in the set, and walking down to count
 * affected descendants.
 *
 * Path conventions
 * ----------------
 * - Paths are forward-slash strings: ``"src/foo.py"``, ``"out"``, ``"~"``.
 * - Empty string and ``"/"`` are treated as the same root.
 * - The all-paths universe is supplied to the helper (the project
 *   tree from ``useFolderContext``).  Paths outside the universe
 *   (e.g. ``~/.config/...``) are still valid set members; they just
 *   contribute no descendants for tri-state computation.
 */

export type PermissionColumn = 'scope' | 'writable' | 'context';

/** Tri-state of a single (path, column) cell in the UI. */
export type CellState =
  | { kind: 'direct' }                 // this exact path is in the set
  | { kind: 'inherited'; from: string } // an ancestor is in the set
  | { kind: 'indeterminate' }          // some descendants are in the set
  | { kind: 'empty' }                  // none of the above
  | { kind: 'na' };                    // not applicable (e.g. context on a dir)

export interface PathInfo {
  path: string;
  is_dir: boolean;
}

// ── Path utilities ──────────────────────────────────────────

const norm = (p: string): string => {
  if (!p) return '';
  // Strip trailing slash (except for the lone root "/").
  return p.length > 1 && p.endsWith('/') ? p.slice(0, -1) : p;
};

/** True iff ``a`` is a strict ancestor of ``b`` (not equal). */
export function isAncestor(a: string, b: string): boolean {
  const A = norm(a);
  const B = norm(b);
  if (A === B) return false;
  // Root case: "" or "/" is an ancestor of everything else.
  if (A === '' || A === '/') return B !== '' && B !== '/' && B !== A;
  return B.startsWith(A + '/');
}

/** Return the closest ancestor of ``path`` that is in ``set``, or null. */
export function findAncestorInSet(
  path: string,
  set: ReadonlySet<string>,
): string | null {
  const p = norm(path);
  // Walk up segment-by-segment.
  let cur = p;
  while (cur.length > 0) {
    const slash = cur.lastIndexOf('/');
    if (slash <= 0) {
      cur = '';
    } else {
      cur = cur.slice(0, slash);
    }
    if (set.has(cur)) return cur;
  }
  return null;
}

// ── Tri-state ───────────────────────────────────────────────

/**
 * Compute the tri-state for a single cell.
 *
 * @param path        The row's path.
 * @param isDir       Whether the row is a directory.
 * @param column      Which column we're computing.
 * @param set         The current set of paths in this column (a
 *                    ``Set<string>`` of normalized path strings).
 * @param universe    Optional list of all known paths (project tree).
 *                    Used to detect ``indeterminate`` state for
 *                    directories whose descendants are partially in
 *                    the set.  Pass ``null`` to skip the descendant
 *                    walk (e.g. for unexpanded out-of-project dirs).
 */
export function cellState(
  path: string,
  isDir: boolean,
  column: PermissionColumn,
  set: ReadonlySet<string>,
  universe: ReadonlyArray<PathInfo> | null,
): CellState {
  // Context on directories cascades: a directory grant means "preload
  // every file under this subtree".  The executor expands the entry
  // at run time (see app/agents/task_executor.py) so the saved scope
  // stays compact even for large subtrees.  No ``na`` short-circuit:
  // the column-agnostic cascade math (direct / inherited / indeterminate
  // / empty) handles dirs the same as files.
  const p = norm(path);
  if (set.has(p)) return { kind: 'direct' };

  const ancestor = findAncestorInSet(p, set);
  if (ancestor !== null) return { kind: 'inherited', from: ancestor };

  if (isDir && universe) {
    // Look for any descendant in the set.
    for (const member of set) {
      if (isAncestor(p, member)) return { kind: 'indeterminate' };
    }
  }
  return { kind: 'empty' };
}

// ── Cascade (set mutation) ──────────────────────────────────

/**
 * Apply a checkbox click on (path, column).
 *
 * Rules:
 * - If the cell is currently ``direct``, the click removes ``path``
 *   from the set.  Any explicit descendants are removed too (they
 *   were redundant anyway under the parent grant).
 * - If the cell is currently ``empty`` or ``indeterminate``, the
 *   click adds ``path`` and removes any descendants of ``path`` that
 *   were in the set (now redundant under the new parent grant).
 * - If the cell is currently ``inherited``, the click is a no-op:
 *   you can't carve out a denial; remove the ancestor instead.
 * - If the cell is ``na``, the click is a no-op.
 *
 * Returns the new set (same reference if unchanged).
 */
export function toggleSet(
  path: string,
  state: CellState,
  set: ReadonlySet<string>,
): Set<string> {
  const p = norm(path);
  if (state.kind === 'na' || state.kind === 'inherited') {
    return new Set(set);
  }
  const next = new Set(set);
  if (state.kind === 'direct') {
    next.delete(p);
    // Also drop any redundant descendants — though the grant just
    // went away, so they're no longer redundant; keep them.
    // (Reversing the policy: only collapse on add, never on remove.)
    return next;
  }
  // empty or indeterminate → add and collapse descendants.
  next.add(p);
  for (const m of Array.from(next)) {
    if (m !== p && isAncestor(p, m)) next.delete(m);
  }
  return next;
}

// ── Cross-column auto-toggles ───────────────────────────────

/**
 * The ``+`` (scope) column is implied by W or Ctx: if a path is
 * writable or in context, it must be in scope.  This helper applies
 * that invariant after a column change.
 *
 * - When W or Ctx is added to a path, scope is added too.
 * - When scope is removed from a path, W and Ctx are also removed
 *   (and their descendants too, since the parent's grant is gone).
 */
export interface PermissionSets {
  scope: ReadonlySet<string>;
  writable: ReadonlySet<string>;
  context: ReadonlySet<string>;
}

export function reconcileScope(sets: PermissionSets): {
  scope: Set<string>;
  writable: Set<string>;
  context: Set<string>;
} {
  const scope = new Set(sets.scope);
  const writable = new Set(sets.writable);
  const context = new Set(sets.context);
  // W ∪ Ctx ⊆ scope
  for (const w of writable) scope.add(w);
  for (const c of context) scope.add(c);
  return { scope, writable, context };
}

/**
 * After a click on (path, column), reconcile the three sets.
 * Centralizes the auto-toggle rules so the React layer never has
 * to know about them.
 */
export function applyClick(
  path: string,
  column: PermissionColumn,
  isDir: boolean,
  sets: PermissionSets,
  universe: ReadonlyArray<PathInfo> | null,
): { scope: Set<string>; writable: Set<string>; context: Set<string> } {
  const p = norm(path);
  const stateBefore = cellState(p, isDir, column, sets[column], universe);

  // Mutate the clicked column first.
  let nextScope = new Set(sets.scope);
  let nextWritable = new Set(sets.writable);
  let nextContext = new Set(sets.context);
  if (column === 'scope') {
    nextScope = toggleSet(p, stateBefore, sets.scope);
    // If scope was just removed, drop W/Ctx from this path & descendants.
    if (stateBefore.kind === 'direct') {
      for (const m of Array.from(nextWritable)) {
        if (m === p || isAncestor(p, m)) nextWritable.delete(m);
      }
      for (const m of Array.from(nextContext)) {
        if (m === p || isAncestor(p, m)) nextContext.delete(m);
      }
    }
  } else if (column === 'writable') {
    nextWritable = toggleSet(p, stateBefore, sets.writable);
  } else {
    nextContext = toggleSet(p, stateBefore, sets.context);
  }

  // Re-establish the W ∪ Ctx ⊆ scope invariant.
  return reconcileScope({
    scope: nextScope,
    writable: nextWritable,
    context: nextContext,
  });
}

/**
 * True iff any of the three permission sets contains a strict
 * descendant of ``path``.  Used by the dialog to mark directory
 * rows whose subtree has user-configured grants — analogous to
 * MUIFileExplorer's "change at a lower level" indicator, so a user
 * scanning the top of the tree can tell something has been
 * configured below without expanding every directory.
 *
 * Direct grants on ``path`` itself are *not* counted here — those
 * are already visible in the row's own column checkboxes.
 */
export function hasDescendantInSets(path: string, sets: PermissionSets): boolean {
  for (const set of [sets.scope, sets.writable, sets.context]) {
    for (const m of set) {
      if (isAncestor(path, m)) return true;
    }
  }
  return false;
}
