import {
  makeTaskBlock, makeRepeatBlock, makeBlock,
  updateBlockById, removeBlockById, appendChildBlock,
} from './taskCardBlocks';
import type { Block } from '../types/task_card';

describe('block factories', () => {
  it('task blocks have default scope and empty body', () => {
    const t = makeTaskBlock('Spec Gen');
    expect(t.block_type).toBe('task');
    expect(t.name).toBe('Spec Gen');
    expect(t.scope).toEqual({ files: [], tools: [], skills: [] });
    expect(t.body).toEqual([]);
    expect(t.id).toMatch(/^t-/);
  });

  it('repeat blocks have a default task child and count mode', () => {
    const r = makeRepeatBlock();
    expect(r.block_type).toBe('repeat');
    expect(r.repeat_mode).toBe('count');
    expect(r.repeat_count).toBe(3);
    expect(r.body).toHaveLength(1);
    expect(r.body[0].block_type).toBe('task');
  });

  it('makeBlock dispatches by type', () => {
    expect(makeBlock('task').block_type).toBe('task');
    expect(makeBlock('repeat').block_type).toBe('repeat');
  });
});

describe('updateBlockById', () => {
  const tree: Block = {
    block_type: 'repeat',
    id: 'root',
    name: 'outer',
    repeat_mode: 'count',
    repeat_count: 5,
    repeat_parallel: true,
    repeat_propagate: 'none',
    body: [
      makeTaskBlock('a'),
      { ...makeRepeatBlock('inner'), id: 'inner' },
    ],
  };

  it('updates the root', () => {
    const next = updateBlockById(tree, 'root', b => ({ ...b, name: 'renamed' }));
    expect(next.name).toBe('renamed');
    expect(next).not.toBe(tree);
  });

  it('updates a nested block', () => {
    const next = updateBlockById(tree, 'inner', b => ({ ...b, repeat_count: 10 }));
    expect(next.body[1].repeat_count).toBe(10);
    // Unchanged sibling should be reference-equal
    expect(next.body[0]).toBe(tree.body[0]);
  });

  it('returns the same tree when id is not found', () => {
    const next = updateBlockById(tree, 'missing', b => ({ ...b, name: 'x' }));
    expect(next).toBe(tree);
  });
});

describe('removeBlockById', () => {
  const tree: Block = {
    ...makeRepeatBlock('outer'),
    id: 'root',
    body: [
      { ...makeTaskBlock('a'), id: 'a' },
      { ...makeTaskBlock('b'), id: 'b' },
    ],
  };

  it('removes a child block', () => {
    const next = removeBlockById(tree, 'a')!;
    expect(next.body).toHaveLength(1);
    expect(next.body[0].id).toBe('b');
  });

  it('returns null when removing the root itself', () => {
    expect(removeBlockById(tree, 'root')).toBeNull();
  });

  it('returns the same tree when id not found', () => {
    expect(removeBlockById(tree, 'missing')).toBe(tree);
  });
});

describe('appendChildBlock', () => {
  it('adds a child to a specified parent', () => {
    const tree = { ...makeRepeatBlock('outer'), id: 'root' };
    const next = appendChildBlock(tree, 'root', makeTaskBlock('new'));
    expect(next.body).toHaveLength(2);
    expect(next.body[1].name).toBe('new');
  });
});
