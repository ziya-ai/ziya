/**
 * deckRunPaneModel — which runs a card has in the dialog, and the run as
 * a thread.
 *
 * The cases that matter:
 *  - a card that only runs as a callee still lists the caller's run;
 *  - a hold on a loop BODY block opens the loop, because output is
 *    recorded against the loop and an iteration, never the body block;
 *  - a hold in another card of the same study opens nothing here.
 */

import type { Block } from '../../../types/task_card';
import type { CalleeContext, TaskRun } from '../../../types/task_run';
import {
  buildRunEntries, buildThread, defaultEntryId, defaultFocus,
  entriesWantEyes, focusFor, formatElapsed, outlineMarkers, pickIteration,
  sameFocus,
} from '../deckRunPaneModel';
import { deriveHoldChain } from '../holdChain';

const task = (id: string, name = id): Block =>
  ({ id, block_type: 'task', name, instructions: '', body: [] } as Block);

const tree = (): Block => ({
  id: 'root', block_type: 'group', name: 'CL1', body: [
    task('recon'),
    { id: 'fan', block_type: 'repeat', name: 'fan-out', body: [task('aud', 'auditor')] } as Block,
    task('merge'),
  ],
} as Block);

const mkRun = (over: Partial<TaskRun> = {}): TaskRun => ({
  id: 'run-1', card_id: 'card-1', status: 'done',
  cancel_requested: false, pause_requested: false,
  block_states: {}, total_tokens: 0, total_tool_calls: 0,
  created_at: 0, updated_at: 0,
  ...over,
} as TaskRun);

const heldRun = (heldAt = 'aud'): TaskRun => mkRun({
  status: 'held', held_at_block_id: heldAt,
  block_states: {
    recon: {
      block_id: 'recon', block_type: 'task', status: 'done',
      iteration_summaries: [], artifact: { duration_ms: 14000 } as any,
    },
    fan: {
      block_id: 'fan', block_type: 'repeat', status: 'failed',
      iteration_summaries: [
        { index: 17, status: 'passed', duration_ms: 1, tokens: 1, has_artifact: true },
        { index: 18, status: 'failed', duration_ms: 1, tokens: 1, has_artifact: true },
        { index: 19, status: 'cancelled', duration_ms: 1, tokens: 1, has_artifact: false },
      ],
    },
  } as any,
});

const ctx = (over: Partial<CalleeContext> = {}): CalleeContext => ({
  run_id: 'caller-run', run_status: 'held', caller_card_id: 'card-CL0',
  call_block_id: 'call-1', held_in_callee: true, updated_at: 7,
  callee_root: tree(), source_conversation_id: 'conv-9',
  ...over,
} as CalleeContext);

describe('run entries', () => {
  it('lists a callee-only card through its caller', () => {
    const e = buildRunEntries([], [ctx()]);
    expect(e).toHaveLength(1);
    expect(e[0]).toMatchObject({
      runId: 'caller-run', status: 'held', callerCardId: 'card-CL0',
      conversationId: 'conv-9',
    });
    expect(e[0].calleeRoot?.id).toBe('root');
  });

  it('own runs come first and win a duplicate id', () => {
    const e = buildRunEntries(
      [mkRun({ id: 'a', created_at: 5 }), mkRun({ id: 'b', status: 'failed', created_at: 9 })],
      [ctx({ run_id: 'x' }), ctx({ run_id: 'a', run_status: 'running' })],
    );
    expect(e.map(x => x.runId)).toEqual(['a', 'b', 'x']);
    expect(e[0].callerCardId).toBeUndefined();
  });

  it('opens on a live run over one that needs attention', () => {
    const e = buildRunEntries(
      [mkRun({ id: 'old', status: 'failed' }), mkRun({ id: 'now', status: 'running' })], []);
    expect(defaultEntryId(e)).toBe('now');
  });

  it('opens on the run needing attention over a finished one', () => {
    const e = buildRunEntries(
      [mkRun({ id: 'fine' }), mkRun({ id: 'bad', status: 'held' })], []);
    expect(defaultEntryId(e)).toBe('bad');
  });

  it('falls back to the first listed, and to null when empty', () => {
    expect(defaultEntryId(buildRunEntries([mkRun({ id: 'only' })], []))).toBe('only');
    expect(defaultEntryId([])).toBeNull();
  });

  it('wants eyes for held, failed and live; not for done or cancelled', () => {
    const one = (status: string) =>
      entriesWantEyes([{ runId: 'r', status, at: 0 }]);
    expect(one('held')).toBe(true);
    expect(one('failed')).toBe(true);
    expect(one('running')).toBe(true);
    expect(one('paused')).toBe(true);
    expect(one('done')).toBe(false);
    expect(one('cancelled')).toBe(false);
    expect(entriesWantEyes([])).toBe(false);
  });
});

describe('thread', () => {
  it('has one section per visible block, in order, skipping the group', () => {
    const s = buildThread(tree(), heldRun());
    expect(s.map(x => x.block.id)).toEqual(['recon', 'fan', 'aud', 'merge']);
    expect(s.map(x => x.depth)).toEqual([0, 0, 1, 0]);
    expect(s[1].isLoop).toBe(true);
    expect(s[2].loop).toEqual({ id: 'fan', index: 0 });
  });

  it('carries status and duration from the run record', () => {
    const s = buildThread(tree(), heldRun());
    expect(s[0].status).toBe('done');
    expect(s[0].durationMs).toBe(14000);
    expect(s[3].status).toBe('queued');
    expect(s[3].durationMs).toBeNull();
  });

  it('is empty without a tree and tolerates a missing run', () => {
    expect(buildThread(null, heldRun())).toEqual([]);
    expect(buildThread(tree(), null).map(x => x.status))
      .toEqual(['queued', 'queued', 'queued', 'queued']);
  });
});

describe('which section is open', () => {
  it('a hold on a loop body block opens the loop at its failed iteration', () => {
    const run = heldRun('aud');
    expect(defaultFocus(buildThread(tree(), run), run))
      .toEqual({ blockId: 'fan', index: 18, passKey: null });
  });

  it('a hold on the loop itself opens the same place', () => {
    const run = heldRun('fan');
    expect(defaultFocus(buildThread(tree(), run), run))
      .toEqual({ blockId: 'fan', index: 18, passKey: null });
  });

  it('a plain block focuses itself with no iteration', () => {
    const run = heldRun();
    expect(focusFor(buildThread(tree(), run)[0], run))
      .toEqual({ blockId: 'recon', index: null, passKey: null });
  });

  it('a running iteration outranks a recorded failure', () => {
    expect(pickIteration(
      [{ index: 2, status: 'failed', duration_ms: 0, tokens: 0, has_artifact: true }],
      [4, 7],
    )).toEqual({ index: 7, passKey: null });
  });

  it('with no failure, the last recorded iteration', () => {
    expect(pickIteration([
      { index: 0, status: 'passed', duration_ms: 0, tokens: 0, has_artifact: true },
      { index: 1, status: 'passed', duration_ms: 0, tokens: 0, has_artifact: true, pass_key: '2' },
    ])).toEqual({ index: 1, passKey: '2' });
    expect(pickIteration([])).toBeNull();
  });

  it('follows what is running when nothing stopped', () => {
    const run = mkRun({ status: 'running' });
    const s = buildThread(tree(), run, { merge: 'running' });
    expect(defaultFocus(s, run)?.blockId).toBe('merge');
  });

  it('opens nothing when the run never reached this tree', () => {
    const run = mkRun({ status: 'held', held_at_block_id: 'in-another-card' });
    expect(defaultFocus(buildThread(tree(), run), run)).toBeNull();
    expect(defaultFocus([], run)).toBeNull();
    expect(defaultFocus(buildThread(tree(), null), null)).toBeNull();
  });

  it('compares focus by block, iteration and pass', () => {
    const a = { blockId: 'fan', index: 3, passKey: null };
    expect(sameFocus(a, { ...a })).toBe(true);
    expect(sameFocus(a, { ...a, index: 4 })).toBe(false);
    expect(sameFocus(a, { ...a, passKey: '1' })).toBe(false);
    expect(sameFocus(a, null)).toBe(false);
    expect(sameFocus(null, null)).toBe(false);
  });
});

describe('elapsed label', () => {
  it('scales its unit', () => {
    expect(formatElapsed(14000)).toBe('14s');
    expect(formatElapsed(108000)).toBe('1m 48s');
    expect(formatElapsed(6480000)).toBe('1h 48m');
  });
  it('is empty for unknown', () => {
    expect(formatElapsed(null)).toBe('');
    expect(formatElapsed(-1)).toBe('');
  });
});

describe('outline markers', () => {
  const markersFor = (run: TaskRun) =>
    outlineMarkers(buildThread(tree(), run), deriveHoldChain(run, tree()));

  it('marks the held block, the loop holding it, and what never ran', () => {
    const m = markersFor(heldRun('aud'));
    expect(m.aud).toMatchObject({ status: 'held', badge: 'held' });
    expect(m.fan).toMatchObject({ status: 'held', badge: 'holding' });
    expect(m.merge).toMatchObject({ status: 'queued', badge: 'blocked' });
  });

  it('does not call a block that finished before the hold blocked', () => {
    // The derivation files every block off the hold path as an ancestor.
    const m = markersFor(heldRun('aud'));
    expect(m.recon).toEqual({ status: 'done' });
  });

  it('a hold on the loop itself marks the loop and blocks its body', () => {
    const m = markersFor(heldRun('fan'));
    expect(m.fan).toMatchObject({ status: 'held', badge: 'held' });
    expect(m.aud).toMatchObject({ badge: 'blocked' });
  });

  it('a hold in another card marks nothing here as held', () => {
    const m = markersFor(heldRun('in-another-card'));
    expect(Object.values(m).some(x => x.badge)).toBe(false);
    expect(m.recon).toEqual({ status: 'done' });
  });

  it('a run that is not held carries statuses only', () => {
    const run = mkRun({ status: 'done', block_states: heldRun().block_states });
    const m = markersFor(run);
    expect(Object.values(m).some(x => x.badge)).toBe(false);
    expect(m.recon.status).toBe('done');
    expect(m.merge.status).toBe('queued');
  });
});
