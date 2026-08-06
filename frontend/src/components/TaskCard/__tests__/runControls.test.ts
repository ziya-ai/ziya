/**
 * Tests for runControls — which run controls apply, and whether a run
 * reads as held.
 *
 * The load-bearing case is the per-step status blip: a stepped run
 * goes paused → running → paused, so anything keyed on
 * ``status === 'paused'`` reports "not held" for the whole time the
 * stepped block executes.  Several tests below exist only to pin that.
 */

import { deriveRunControls, heldLabel } from '../runControls';
import type { TaskRun } from '../../../types/task_run';

const mkRun = (over: Partial<TaskRun> = {}): TaskRun => ({
  id: 'run-1', card_id: 'card-1', status: 'running',
  cancel_requested: false, pause_requested: false,
  block_states: {}, total_tokens: 0, total_tool_calls: 0,
  created_at: 0, updated_at: 0,
  ...over,
});

/** A terminal run that carries the card_snapshot resume requires. */
const mkSnapshotted = (over: Partial<TaskRun> = {}): TaskRun => mkRun({
  status: 'done',
  card_snapshot: { name: 'c', description: '', root: {} as any },
  ...over,
});

describe('deriveRunControls', () => {
  it('treats a null run as inert', () => {
    const c = deriveRunControls(null);
    expect(c).toMatchObject({
      isTerminal: false, isHeld: false,
      canPause: false, canStep: false, canResume: false, canCancel: false,
    });
  });

  it('offers pause / step / cancel but not resume on a free-running run', () => {
    const c = deriveRunControls(mkRun({ status: 'running' }));
    expect(c).toMatchObject({
      isHeld: false, canPause: true, canStep: true,
      canResume: false, canCancel: true,
    });
  });

  it('offers step on a queued run (it holds at the first boundary)', () => {
    expect(deriveRunControls(mkRun({ status: 'queued' })).canStep).toBe(true);
  });

  it('reports held and at-boundary for a paused run', () => {
    const c = deriveRunControls(
      mkRun({ status: 'paused', pause_requested: true }));
    expect(c).toMatchObject({
      isHeld: true, isAtBoundary: true, isSettling: false,
      canPause: false, canResume: true, canStep: true,
    });
  });

  // The regression this module exists for.
  it('stays held while a step is executing, though status reads running', () => {
    const c = deriveRunControls(
      mkRun({ status: 'running', pause_requested: true, step_budget: 0 }));
    expect(c.isHeld).toBe(true);
    expect(c.isAtBoundary).toBe(false);
    expect(c.isSettling).toBe(true);
    // Critically, Resume must remain reachable — otherwise the user is
    // stranded in step mode until the run happens to be observed at a
    // boundary.
    expect(c.canResume).toBe(true);
    // And Pause must not be offered: the run is already held.
    expect(c.canPause).toBe(false);
  });

  it('surfaces unspent step credits', () => {
    const c = deriveRunControls(
      mkRun({ status: 'paused', pause_requested: true, step_budget: 3 }));
    expect(c.stepCredits).toBe(3);
  });

  it('clamps a negative or absent budget to zero', () => {
    expect(deriveRunControls(mkRun({ step_budget: -2 })).stepCredits).toBe(0);
    expect(deriveRunControls(mkRun()).stepCredits).toBe(0);
  });

  it('reports a pause in flight as settling, not at a boundary', () => {
    const c = deriveRunControls(
      mkRun({ status: 'running', pause_requested: true }));
    expect(c.isSettling).toBe(true);
    expect(c.isAtBoundary).toBe(false);
  });

  it.each(['done', 'failed', 'cancelled'] as const)(
    'offers no controls on a %s run', (status) => {
      const c = deriveRunControls(mkRun({ status, pause_requested: true }));
      expect(c).toMatchObject({
        isTerminal: true, isHeld: false,
        canPause: false, canStep: false,
        canResume: false, canCancel: false,
      });
    });

  it('ignores a stale pause flag left on a terminal run', () => {
    // request_pause on a run that then completed leaves the flag set;
    // a finished run must never read as held.
    const c = deriveRunControls(
      mkRun({ status: 'done', pause_requested: true, step_budget: 5 }));
    expect(c.isHeld).toBe(false);
    expect(c.stepCredits).toBe(0);
  });
});

describe('canResumeFromBlock', () => {
  // Inverted relative to every other control: resume-from applies ONLY
  // to terminal runs, because it launches a new run rather than nudging
  // a live executor.
  // 'partial' is included because it is the state most likely to be
  // resumed — a run that got partway is exactly what these controls
  // exist for — and omitting it from TERMINAL would leave the tile
  // offering Pause/Step on an executor that has already unwound.
  it.each(['done', 'partial', 'failed', 'cancelled'] as const)(
    'is offered on a %s run that has a snapshot', (status) => {
      expect(deriveRunControls(mkSnapshotted({ status })).canResumeFromBlock)
        .toBe(true);
    });

  it('is withheld on a terminal run with no card_snapshot', () => {
    // The server 422s on these (the live card's block ids may no longer
    // match this run's block_states), so offering it would guarantee an
    // error rather than an action.
    expect(deriveRunControls(mkRun({ status: 'done' })).canResumeFromBlock)
      .toBe(false);
    expect(
      deriveRunControls(mkRun({ status: 'done', card_snapshot: null }))
        .canResumeFromBlock,
    ).toBe(false);
  });

  it.each(['queued', 'running', 'paused'] as const)(
    'is withheld on a live (%s) run even with a snapshot', (status) => {
      // The server 409s: resuming a live run would double-execute its
      // remaining blocks.
      expect(deriveRunControls(mkSnapshotted({ status })).canResumeFromBlock)
        .toBe(false);
    });

  it('is withheld with no run at all', () => {
    expect(deriveRunControls(null).canResumeFromBlock).toBe(false);
    expect(deriveRunControls(undefined).canResumeFromBlock).toBe(false);
  });
});

describe('canContinueFromBlock', () => {
  // Continue and retry are two modes of ONE endpoint, so they gate
  // identically today.  Asserted separately anyway: they are distinct
  // user-facing acts, and a future policy may withhold one (e.g. no
  // continue on a zero-progress run, where accepting a non-existent
  // outcome is meaningless) — at which point this test is the thing
  // that notices the divergence was deliberate.
  it.each(['done', 'partial', 'failed', 'cancelled'] as const)(
    'is offered on a %s run that has a snapshot', (status) => {
      expect(deriveRunControls(mkSnapshotted({ status })).canContinueFromBlock)
        .toBe(true);
    });

  it('is withheld on a terminal run with no card_snapshot', () => {
    expect(deriveRunControls(mkRun({ status: 'partial' })).canContinueFromBlock)
      .toBe(false);
  });

  it.each(['queued', 'running', 'paused'] as const)(
    'is withheld on a live (%s) run', (status) => {
      expect(deriveRunControls(mkSnapshotted({ status })).canContinueFromBlock)
        .toBe(false);
    });

  it('tracks canResumeFromBlock while they share one endpoint', () => {
    const c = deriveRunControls(mkSnapshotted({ status: 'partial' }));
    expect(c.canContinueFromBlock).toBe(c.canResumeFromBlock);
  });
});

describe('partial is terminal', () => {
  it('offers no live controls on a partial run', () => {
    // The executor has unwound; Pause/Step/Cancel would all be no-ops
    // against a run that no longer exists.
    const c = deriveRunControls(mkRun({ status: 'partial' }));
    expect(c.isTerminal).toBe(true);
    expect(c.canPause).toBe(false);
    expect(c.canStep).toBe(false);
    expect(c.canResume).toBe(false);
    expect(c.canCancel).toBe(false);
  });

  it('is disjoint from canResume, which targets a live executor', () => {
    const terminal = deriveRunControls(mkSnapshotted());
    expect(terminal.canResumeFromBlock).toBe(true);
    expect(terminal.canResume).toBe(false);
    const held = deriveRunControls(
      mkRun({ status: 'paused', pause_requested: true }));
    expect(held.canResume).toBe(true);
    expect(held.canResumeFromBlock).toBe(false);
  });
});

describe('heldLabel', () => {
  const at = (credits: number) => deriveRunControls(
    mkRun({ status: 'paused', pause_requested: true, step_budget: credits }));
  const settling = () => deriveRunControls(
    mkRun({ status: 'running', pause_requested: true }));

  it('invites step or resume when stopped with no credits', () => {
    expect(heldLabel(at(0), false)).toMatch(/step or resume/i);
  });

  it('reports queued credits when stopped with budget remaining', () => {
    expect(heldLabel(at(2), false)).toContain('2 steps queued');
    expect(heldLabel(at(1), false)).toContain('1 step queued');
  });

  it('distinguishes an advancing step from a landing pause', () => {
    expect(heldLabel(settling(), true)).toMatch(/advancing/i);
    expect(heldLabel(settling(), false)).toMatch(/pausing/i);
  });
});
