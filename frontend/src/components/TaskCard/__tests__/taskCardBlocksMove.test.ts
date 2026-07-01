/**
 * Unit tests for the Block-tree move helpers backing drag-and-drop
 * composability in the Task Card editor.
 *
 * These cover the four operations the drag context relies on:
 *   - findBlockById      — locate a node anywhere in the tree
 *   - isSelfOrDescendant — cycle guard
 *   - canMoveBlock       — legality of a proposed move
 *   - moveBlock          — the actual detach-then-insert, including
 *                          same-parent reorder (index-drift immunity)
 *
 * No React/DOM — pure data transforms over Block trees.
 */
import {
  findBlockById, isSelfOrDescendant, canMoveBlock, moveBlock,
} from '../../../utils/taskCardBlocks';
import type { Block } from '../../../types/task_card';

const task = (id: string, name = id): Block => ({
  block_type: 'task', id, name, instructions: '', body: [],
});

const parallel = (id: string, body: Block[]): Block => ({
  block_type: 'parallel', id, name: id, body,
});

const repeat = (id: string, body: Block[]): Block => ({
  block_type: 'repeat', id, name: id, repeat_mode: 'count',
  repeat_count: 1, body,
});

/**
 * Build a fresh tree per test so mutations in one don't leak:
 *
 *   root (parallel)
 *   ├── t1 (task)
 *   ├── rep (repeat)
 *   │   └── t2 (task)
 *   └── par2 (parallel)
 *       ├── t3 (task)
 *       └── t4 (task)
 */
const makeTree = (): Block =>
  parallel('root', [
    task('t1'),
    repeat('rep', [task('t2')]),
    parallel('par2', [task('t3'), task('t4')]),
  ]);

describe('findBlockById', () => {
  it('finds the root', () => {
    const root = makeTree();
    expect(findBlockById(root, 'root')).toBe(root);
  });

  it('finds a deeply nested block', () => {
    const root = makeTree();
    expect(findBlockById(root, 't4')?.id).toBe('t4');
  });

  it('returns null for an absent id', () => {
    expect(findBlockById(makeTree(), 'nope')).toBeNull();
  });
});

describe('isSelfOrDescendant', () => {
  it('is true for the node itself', () => {
    expect(isSelfOrDescendant(makeTree(), 'rep', 'rep')).toBe(true);
  });

  it('is true for a descendant', () => {
    expect(isSelfOrDescendant(makeTree(), 'par2', 't3')).toBe(true);
  });

  it('is false for a non-descendant', () => {
    expect(isSelfOrDescendant(makeTree(), 'rep', 't3')).toBe(false);
  });
});

describe('canMoveBlock', () => {
  it('rejects moving the root', () => {
    expect(canMoveBlock(makeTree(), 'root', 'par2')).toBe(false);
  });

  it('rejects dropping a block into itself', () => {
    expect(canMoveBlock(makeTree(), 'par2', 'par2')).toBe(false);
  });

  it('rejects dropping into a Task (leaves have no body)', () => {
    expect(canMoveBlock(makeTree(), 't3', 't1')).toBe(false);
  });

  it('rejects a cycle (dropping a wrapper into its own descendant)', () => {
    // par2 cannot move into t3, which lives inside par2.
    expect(canMoveBlock(makeTree(), 'par2', 't3')).toBe(false);
  });

  it('allows a legal cross-container move', () => {
    expect(canMoveBlock(makeTree(), 't1', 'par2')).toBe(true);
  });

  it('returns false for an unknown target parent', () => {
    expect(canMoveBlock(makeTree(), 't1', 'ghost')).toBe(false);
  });
});

describe('moveBlock — cross-container', () => {
  it('moves a task into a parallel set and appends it', () => {
    const root = makeTree();
    const next = moveBlock(root, 't1', 'par2', null);
    expect(next).not.toBe(root); // changed
    // t1 left the root's direct children
    expect(next.body.find(b => b.id === 't1')).toBeUndefined();
    // t1 now last child of par2
    const par2 = findBlockById(next, 'par2')!;
    expect(par2.body.map(b => b.id)).toEqual(['t3', 't4', 't1']);
  });

  it('moves a wrapper (repeat) into another parallel set', () => {
    const root = makeTree();
    const next = moveBlock(root, 'rep', 'par2', 't4');
    const par2 = findBlockById(next, 'par2')!;
    // inserted before t4
    expect(par2.body.map(b => b.id)).toEqual(['t3', 'rep', 't4']);
    // and rep brought its child along
    expect(findBlockById(next, 't2')?.id).toBe('t2');
    // root no longer holds rep directly
    expect(root.body.map(b => b.id)).toContain('rep'); // original untouched
    expect(next.body.map(b => b.id)).not.toContain('rep');
  });
});

describe('moveBlock — same-parent reorder', () => {
  it('reorders within par2 (move t4 before t3) without index drift', () => {
    const root = makeTree();
    const next = moveBlock(root, 't4', 'par2', 't3');
    const par2 = findBlockById(next, 'par2')!;
    expect(par2.body.map(b => b.id)).toEqual(['t4', 't3']);
  });

  it('moving a block before itself is a no-op (same reference)', () => {
    const root = makeTree();
    expect(moveBlock(root, 't3', 'par2', 't3')).toBe(root);
  });

  it('appending a block already last is still applied but order is stable', () => {
    const root = makeTree();
    const next = moveBlock(root, 't4', 'par2', null);
    const par2 = findBlockById(next, 'par2')!;
    expect(par2.body.map(b => b.id)).toEqual(['t3', 't4']);
  });
});

describe('moveBlock — illegal moves return same reference', () => {
  it('no-op when moving the root', () => {
    const root = makeTree();
    expect(moveBlock(root, 'root', 'par2', null)).toBe(root);
  });

  it('no-op when target is a Task', () => {
    const root = makeTree();
    expect(moveBlock(root, 't1', 't3', null)).toBe(root);
  });

  it('no-op when the move would form a cycle', () => {
    const root = makeTree();
    expect(moveBlock(root, 'par2', 't3', null)).toBe(root);
  });

  it('no-op when source id is unknown', () => {
    const root = makeTree();
    expect(moveBlock(root, 'ghost', 'par2', null)).toBe(root);
  });
});

describe('moveBlock — immutability', () => {
  it('does not mutate the original tree', () => {
    const root = makeTree();
    const before = JSON.stringify(root);
    moveBlock(root, 't1', 'par2', null);
    expect(JSON.stringify(root)).toBe(before);
  });
});
