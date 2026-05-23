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

export const makeBlock = (type: BlockType, name?: string): Block => {
  if (type === 'repeat') return makeRepeatBlock(name);
  if (type === 'parallel') return makeParallelBlock(name);
  if (type === 'until') return makeUntilBlock(name);
  if (type === 'schedule') return makeScheduleBlock(name);
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
 */
export const removeBlockById = (root: Block, id: string): Block | null => {
  if (root.id === id) return null;
  if (!root.body || root.body.length === 0) return root;
  const newBody = root.body
    .map(child => removeBlockById(child, id))
    .filter((b): b is Block => b !== null);
  if (newBody.length === root.body.length) return root;
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
