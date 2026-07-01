/**
 * Pure helpers for constructing and transforming Block trees.
 *
 * Kept out of React components so they can be unit-tested.
 */

import type { Block, BlockType } from '../types/task_card';

const nextId = (prefix: string = 'b'): string =>
  `${prefix}-${Math.random().toString(16).slice(2, 10)}`;

export const makeTaskBlock = (name: string = 'New Task'): Block => ({
  block_type: 'task',
  id: nextId('t'),
  name,
  instructions: '',
  scope: { files: [], tools: [], skills: [] },
  emoji: '🔵',
  body: [],
});

export const makeRepeatBlock = (name: string = 'Repeat'): Block => ({
  block_type: 'repeat',
  id: nextId('r'),
  name,
  repeat_mode: 'count',
  repeat_count: 3,
  repeat_max: null,
  repeat_parallel: false,
  repeat_propagate: 'last',
  body: [makeTaskBlock('Iteration body')],
});

export const makeParallelBlock = (name: string = 'Parallel'): Block => ({
  block_type: 'parallel',
  id: nextId('p'),
  name,
  // Parallel blocks have no loop/scope fields — just a body of children
  // that execute concurrently.  See app/agents/block_executor.py
  // _execute_parallel.
  body: [makeTaskBlock('Parallel branch A'), makeTaskBlock('Parallel branch B')],
});

export const makeUntilBlock = (name: string = 'Until'): Block => ({
  block_type: 'until',
  id: nextId('u'),
  name,
  until_mode: 'model',
  until_condition: '',
  until_max: 5,
  body: [makeTaskBlock('Iteration body')],
});

/**
 * Schedule = the "outer-outer" trigger decorator.  Wraps any body
 * (Task / Repeat / Parallel / Until / nested Schedule) and fires
 * recurring TaskRuns when the in-process scheduler ticks.
 * See app/agents/task_scheduler.py.
 */
export const makeScheduleBlock = (name: string = 'Schedule'): Block => ({
  block_type: 'schedule',
  id: nextId('s'),
  name,
  schedule_mode: 'interval',
  schedule_interval_value: 1,
  schedule_interval_unit: 'hours',
  schedule_at_iso: null,
  schedule_daily_at: null,
  schedule_cron: null,
  schedule_enabled: true,
  schedule_catch_up: true,
  schedule_max_runs: null,
  body: [makeTaskBlock('Scheduled action')],
});

/**
 * State = a read-only declaration of run-scoped named variables.  A
 * leaf (no body), like Task.  Placement encodes the reset policy: at
 * the top of a once-running body it sets once per run; inside a
 * Repeat/Until body it re-applies its literals each iteration.  Tasks
 * read the values via {{var.NAME}}.  See app/agents/block_executor.py
 * ::_execute_state.
 */
export const makeStateBlock = (name: string = 'Initial state'): Block => ({
  block_type: 'state',
  id: nextId('st'),
  name,
  state_context: '',
  state_variables: {},
  body: [],
});

/**
 * Group = a neutral run-once sequential container.  No loop/trigger
 * semantics: it runs its body top-to-bottom exactly once (backend
 * dispatches it to _execute_sequence).  It is the invisible card-root
 * wrapper — rendered without chrome — that lets a State precede a loop
 * without entering the loop's scope, and lets operators follow a State.
 */
export const makeGroupBlock = (name: string = 'Steps'): Block => ({
  block_type: 'group',
  id: nextId('g'),
  name,
  body: [],
});

export const makeBlock = (type: BlockType, name?: string): Block => {
  if (type === 'repeat') return makeRepeatBlock(name);
  if (type === 'parallel') return makeParallelBlock(name);
  if (type === 'until') return makeUntilBlock(name);
  if (type === 'schedule') return makeScheduleBlock(name);
  if (type === 'state') return makeStateBlock(name);
  if (type === 'group') return makeGroupBlock(name);
  return makeTaskBlock(name);
};

/**
 * Immutably update a block by id anywhere in the tree.
 * Returns a new tree; unchanged branches are reference-equal.
 */
export const updateBlockById = (
  root: Block,
  id: string,
  updater: (block: Block) => Block,
): Block => {
  if (root.id === id) return updater(root);
  if (!root.body || root.body.length === 0) return root;
  let changed = false;
  const newBody = root.body.map(child => {
    const next = updateBlockById(child, id, updater);
    if (next !== child) changed = true;
    return next;
  });
  return changed ? { ...root, body: newBody } : root;
};

/**
 * Immutably remove a block by id. Returns null if the root itself was
 * targeted (caller must handle that case).
 *
 * Change-tracking is by child reference-inequality (the way
 * updateBlockById works), NOT by immediate-body length: a removal deep
 * in a subtree leaves the parent's direct child count unchanged, so a
 * length-only guard would return the stale original and silently drop
 * the rebuilt subtree.  moveBlock relies on this returning a tree that
 * actually no longer contains the source.
 */
export const removeBlockById = (root: Block, id: string): Block | null => {
  if (root.id === id) return null;
  if (!root.body || root.body.length === 0) return root;
  let changed = false;
  const newBody = root.body
    .map(child => {
      const next = removeBlockById(child, id);
      if (next !== child) changed = true;
      return next;
    })
    .filter((b): b is Block => b !== null);
  if (!changed) return root;
  return { ...root, body: newBody };
};

/**
 * Immutably append a new child block to a parent's body.
 */
export const appendChildBlock = (
  root: Block, parentId: string, child: Block,
): Block =>
  updateBlockById(root, parentId, parent => ({
    ...parent,
    body: [...(parent.body || []), child],
  }));

/**
 * Find a block anywhere in the tree by id. Returns null if absent.
 */
export const findBlockById = (root: Block, id: string): Block | null => {
  if (root.id === id) return root;
  for (const child of root.body ?? []) {
    const found = findBlockById(child, id);
    if (found) return found;
  }
  return null;
};

/**
 * True when `candidateId` is `id` itself or lives anywhere inside
 * `id`'s subtree. Used to reject moves that would create a cycle
 * (dropping a block into its own descendant).
 */
export const isSelfOrDescendant = (
  root: Block, id: string, candidateId: string,
): boolean => {
  const node = findBlockById(root, id);
  if (!node) return false;
  return findBlockById(node, candidateId) !== null;
};

/**
 * Whether `sourceId` may be moved into `targetParentId`'s body.
 * Rejects: moving the root, dropping into self, dropping into a Task
 * (leaves have no body), and any move that would form a cycle.
 */
export const canMoveBlock = (
  root: Block, sourceId: string, targetParentId: string,
): boolean => {
  if (sourceId === root.id) return false;        // root has no parent to move from
  if (sourceId === targetParentId) return false; // can't drop a block into itself
  const target = findBlockById(root, targetParentId);
  if (!target) return false;
  if (target.block_type === 'task') return false; // tasks are leaves, no body
  if (isSelfOrDescendant(root, sourceId, targetParentId)) return false; // cycle
  return true;
};

/**
 * Move `sourceId` into `targetParentId`'s body, positioned immediately
 * before `beforeId` (or appended when `beforeId` is null). Returns the
 * SAME root reference (no-op) when the move is illegal or a no-op, so
 * callers can cheaply detect "nothing changed".
 *
 * The detach-then-insert order, with the insertion point resolved by
 * child id (not raw index) AFTER removal, makes same-parent reordering
 * immune to index drift.
 */
export const moveBlock = (
  root: Block,
  sourceId: string,
  targetParentId: string,
  beforeId: string | null,
): Block => {
  // Dropping a block onto the gap immediately before itself is a no-op.
  if (beforeId === sourceId) return root;
  if (!canMoveBlock(root, sourceId, targetParentId)) return root;

  const source = findBlockById(root, sourceId);
  if (!source) return root;

  const without = removeBlockById(root, sourceId);
  if (!without) return root; // root removal — guarded above, defensive

  return updateBlockById(without, targetParentId, parent => {
    const body = parent.body ? parent.body.slice() : [];
    let idx = body.length;
    if (beforeId !== null) {
      const at = body.findIndex(b => b.id === beforeId);
      if (at >= 0) idx = at;
    }
    body.splice(idx, 0, source);
    return { ...parent, body };
  });
};
