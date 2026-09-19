/**
 * @jest-environment jsdom
 */

/**
 * Force-stop affordance for a run stuck inside a block.
 *
 * Soft-cancel lands only at a block boundary, so a run whose in-flight
 * invocation never returns keeps reading ``running`` with the cancel
 * button doing nothing.  The tile has to offer a stronger lever in
 * exactly two situations, and only those:
 *   - cancel was requested and the run is still live (the flag did not land)
 *   - the run has been silent long enough to look hung
 * Both are pure functions here so the rule is pinned without rendering.
 */

import { canForceAbort } from '../runControls';
import { HUNG_AFTER_S, STALE_AFTER_S, isHung } from '../liveActivity';
import { describeGate, deriveHoldChain } from '../holdChain';
import { cancelTaskRun } from '../../../services/taskRunApi';
import type { TaskRun } from '../../../types/task_run';

const mkRun = (over: Partial<TaskRun> = {}): TaskRun => ({
  id: 'run-1', card_id: 'card-1', status: 'running',
  cancel_requested: false, pause_requested: false,
  block_states: {}, total_tokens: 0, total_tool_calls: 0,
  created_at: 0, updated_at: 0,
  ...over,
});

describe('isHung', () => {
  const NOW = 1_800_000_000_000;
  const ago = (s: number) => NOW - s * 1000;

  it('is a stronger threshold than stale', () => {
    expect(HUNG_AFTER_S).toBeGreaterThan(STALE_AFTER_S);
  });

  it('flips exactly at the threshold', () => {
    expect(isHung(ago(HUNG_AFTER_S - 1), NOW)).toBe(false);
    expect(isHung(ago(HUNG_AFTER_S), NOW)).toBe(true);
  });

  it('is false for a run that is merely stale', () => {
    expect(isHung(ago(STALE_AFTER_S + 5), NOW)).toBe(false);
  });
});

describe('canForceAbort', () => {
  it('is false on a live, active run with no cancel pending', () => {
    expect(canForceAbort(mkRun(), false)).toBe(false);
  });

  it('is true once cancel was requested but the run is still live', () => {
    expect(canForceAbort(mkRun({ cancel_requested: true }), false)).toBe(true);
  });

  it('is true on a hung run even without a prior cancel', () => {
    expect(canForceAbort(mkRun(), true)).toBe(true);
  });

  it('is never offered on a terminal run — there is nothing to interrupt', () => {
    for (const status of ['done', 'partial', 'failed', 'cancelled', 'held'] as const) {
      expect(canForceAbort(mkRun({ status, cancel_requested: true }), true)).toBe(false);
    }
  });

  it('is inert on a null run', () => {
    expect(canForceAbort(null, true)).toBe(false);
  });
});

describe('user_abort hold vocabulary', () => {
  it('describes the gate as a redo of the interrupted block, not an infra fault', () => {
    const chain = deriveHoldChain(mkRun({
      status: 'held', held_reason: 'user_abort', held_at_block_id: 'b-leaf',
    }), null);
    const gate = describeGate(chain);
    expect(gate).toMatch(/force-stopped/i);
    expect(gate).toMatch(/resume/i);
    // The generic fallback would read "Stopped on user abort." — a
    // reason with no remedy, which is what this case exists to replace.
    expect(gate).not.toMatch(/^Stopped on/);
  });
});

describe('cancelTaskRun({ force })', () => {
  function setupFetchMock() {
    const fetchMock = jest.fn().mockResolvedValue({
      ok: true, json: async () => ({}), status: 200,
    });
    global.fetch = fetchMock;
    return fetchMock;
  }

  it('adds ?force=true only when asked', async () => {
    const fetchMock = setupFetchMock();
    await cancelTaskRun('p', 'r');
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/task-runs\/r\/cancel$/);
    await cancelTaskRun('p', 'r', { force: true });
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/task-runs\/r\/cancel\?force=true$/);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST' });
  });
});
