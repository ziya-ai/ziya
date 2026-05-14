/**
 * Tests for useTaskRunStream.
 *
 * The React hook itself is verified structurally (import works); the
 * real logic lives in the pure dispatchTaskRunEvent function which we
 * unit-test exhaustively.
 */

import { dispatchTaskRunEvent } from '../useTaskRunStream';

describe('dispatchTaskRunEvent', () => {
  it('ignores non-object events', () => {
    expect(dispatchTaskRunEvent(null)).toEqual({ kind: 'ignore' });
    expect(dispatchTaskRunEvent(undefined)).toEqual({ kind: 'ignore' });
    expect(dispatchTaskRunEvent('string')).toEqual({ kind: 'ignore' });
    expect(dispatchTaskRunEvent(42)).toEqual({ kind: 'ignore' });
  });

  it('ignores events with non-string type', () => {
    expect(dispatchTaskRunEvent({ type: 42 })).toEqual({ kind: 'ignore' });
    expect(dispatchTaskRunEvent({})).toEqual({ kind: 'ignore' });
  });

  it('refetches on run_started', () => {
    expect(dispatchTaskRunEvent({ type: 'run_started', run_id: 'r1' }))
      .toEqual({ kind: 'refetch' });
  });

  it('refetches on iteration_completed', () => {
    expect(dispatchTaskRunEvent({
      type: 'iteration_completed',
      block_id: 'b1', index: 0, status: 'passed',
    })).toEqual({ kind: 'refetch' });
  });

  it('refetches on block_completed', () => {
    expect(dispatchTaskRunEvent({ type: 'block_completed', block_id: 'b1' }))
      .toEqual({ kind: 'refetch' });
  });

  it('refetches and closes on run_completed', () => {
    expect(dispatchTaskRunEvent({
      type: 'run_completed', status: 'done', run_id: 'r1',
    })).toEqual({ kind: 'refetch-and-close' });
  });

  it('closes on run_completed regardless of status', () => {
    // done | failed | cancelled all trigger the same action — the
    // hook doesn't distinguish; the refetched snapshot carries it.
    expect(dispatchTaskRunEvent({ type: 'run_completed', status: 'failed' }))
      .toEqual({ kind: 'refetch-and-close' });
    expect(dispatchTaskRunEvent({ type: 'run_completed', status: 'cancelled' }))
      .toEqual({ kind: 'refetch-and-close' });
  });

  it('ignores block_started (no persisted state change)', () => {
    expect(dispatchTaskRunEvent({ type: 'block_started', block_id: 'b1' }))
      .toEqual({ kind: 'ignore' });
  });

  it('ignores iteration_started', () => {
    expect(dispatchTaskRunEvent({
      type: 'iteration_started', block_id: 'b1', index: 2,
    })).toEqual({ kind: 'ignore' });
  });

  it('ignores whisper_received (feature not yet implemented)', () => {
    expect(dispatchTaskRunEvent({ type: 'whisper_received' }))
      .toEqual({ kind: 'ignore' });
  });

  it('ignores unknown event types', () => {
    expect(dispatchTaskRunEvent({ type: 'pizza_delivered' }))
      .toEqual({ kind: 'ignore' });
  });
});

describe('useTaskRunStream hook', () => {
  it('exports the hook', async () => {
    const mod = await import('../useTaskRunStream');
    expect(mod.useTaskRunStream).toBeDefined();
    expect(typeof mod.useTaskRunStream).toBe('function');
  });
});
