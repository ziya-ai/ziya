/**
 * Verifies the Parallel block editor is exported, the block factory
 * produces a valid Parallel block, and the BlockEditor dispatcher
 * routes 'parallel' to it (not to the unknown-type fallback).
 */

// ``uuid`` is ESM-only and the CRA jest transform won't process it.
// Stub at module scope so any transitive importer (db.ts pulled in by
// the BlockEditor dispatcher's context chain) gets a sync replacement.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import React from 'react';

describe('ParallelBlockEditor', () => {
  it('exports a named React component', async () => {
    const mod = await import('../ParallelBlockEditor');
    expect(mod.ParallelBlockEditor).toBeDefined();
    expect(typeof mod.ParallelBlockEditor).toBe('function');
  });
});

describe('makeParallelBlock factory', () => {
  it('produces a valid Parallel block with a body', async () => {
    const { makeParallelBlock } = await import('../../../utils/taskCardBlocks');
    const block = makeParallelBlock();
    expect(block.block_type).toBe('parallel');
    expect(block.id).toMatch(/^p-/);
    expect(block.name).toBe('Parallel');
    expect(Array.isArray(block.body)).toBe(true);
    expect(block.body.length).toBeGreaterThan(0);
    // No loop controls on Parallel blocks
    expect(block.repeat_mode).toBeUndefined();
    expect(block.repeat_count).toBeUndefined();
    expect(block.repeat_parallel).toBeUndefined();
  });

  it('accepts a custom name', async () => {
    const { makeParallelBlock } = await import('../../../utils/taskCardBlocks');
    const block = makeParallelBlock('Fan-out');
    expect(block.name).toBe('Fan-out');
  });
});

describe('makeBlock dispatch', () => {
  it('routes "parallel" to makeParallelBlock', async () => {
    const { makeBlock } = await import('../../../utils/taskCardBlocks');
    const block = makeBlock('parallel');
    expect(block.block_type).toBe('parallel');
    expect(block.id).toMatch(/^p-/);
  });

  it('still routes "task" and "repeat" correctly', async () => {
    const { makeBlock } = await import('../../../utils/taskCardBlocks');
    expect(makeBlock('task').block_type).toBe('task');
    expect(makeBlock('repeat').block_type).toBe('repeat');
  });
});

describe('BlockEditor dispatcher exports', () => {
  it('exports BlockEditor', async () => {
    const mod = await import('../BlockEditor');
    expect(mod.BlockEditor).toBeDefined();
  });
});
