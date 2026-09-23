/**
 * Body blocks of a loop take their display status from the loop's latest
 * iteration digest when they have no status of their own.
 *
 * A loop body's blocks have no durable per-block state (the executor
 * writes block_states only for structural blocks), so after a reload the
 * map painted them 'queued' whatever had happened.  GFX Stage 2 run
 * 3068d3d0: a 'done' until-loop over four never-run children rendered as
 * four bare queued rows.  IterationSummary now carries a compact
 * ``stages`` digest (index = position in the loop body); flattenBlocks
 * records each body row's loop and position, and resolveBlockStatus
 * falls back to the digest.
 */

import { flattenBlocks, resolveBlockStatus } from '../runMapModel';
import type { Block } from '../../../types/task_card';
import type { TaskRun, IterationSummary } from '../../../types/task_run';

const task = (id: string, name = ''): Block => ({
  block_type: 'task', id, name, body: [],
});
const loop = (id: string, body: Block[]): Block => ({
  block_type: 'until', id, name: '', body,
});

const summary = (
  index: number, stages: IterationSummary['stages'], replayed = false,
): IterationSummary => ({
  index, status: 'passed', duration_ms: 1, tokens: 0, has_artifact: true,
  replayed, stages,
});

const runWith = (
  summaries: IterationSummary[], status: TaskRun['status'] = 'done',
): TaskRun => ({
  id: 'r', card_id: 'c', status, created_at: 1, updated_at: 1,
  block_states: {
    verify: {
      block_id: 'verify', block_type: 'until', status,
      iteration_summaries: summaries,
    },
  },
} as unknown as TaskRun);

const BODY = [
  task('b-build', 'Rebuild the frontend bundle'),
  task('b-test', 'Run the unit tests'),
  task('b-render', 'Re-render and judge'),
];

describe('flattenBlocks records loop membership', () => {
  it('tags each direct body child with its loop and position', () => {
    const rows = flattenBlocks(loop('verify', BODY));
    expect(rows.map(r => [r.block.id, r.loop?.id, r.loop?.index])).toEqual([
      ['verify', undefined, undefined],
      ['b-build', 'verify', 0],
      ['b-test', 'verify', 1],
      ['b-render', 'verify', 2],
    ]);
  });

  it('does not tag children of a non-loop container', () => {
    const rows = flattenBlocks({
      block_type: 'parallel', id: 'p', name: '', body: [task('a'), task('b')],
    });
    expect(rows.every(r => r.loop === undefined)).toBe(true);
  });
});

describe('resolveBlockStatus falls back to the iteration digest', () => {
  const digest = [
    { index: 0, label: 'Rebuild the frontend bundle', status: 'passed', tool_calls: 0 },
    { index: 1, label: 'Run the unit tests', status: 'failed', tool_calls: 3 },
    { index: 2, label: 'Re-render and judge', status: 'skipped' },
  ];
  const run = runWith([summary(0, digest)]);

  it('maps passed/failed/skipped onto the body rows', () => {
    expect(resolveBlockStatus('b-build', {}, run, { id: 'verify', index: 0 })).toBe('done');
    expect(resolveBlockStatus('b-test', {}, run, { id: 'verify', index: 1 })).toBe('failed');
    expect(resolveBlockStatus('b-render', {}, run, { id: 'verify', index: 2 })).toBe('skipped');
  });

  it('uses the LATEST iteration, by index, not array order', () => {
    const later = [{ index: 0, label: 'x', status: 'failed' }];
    const r = runWith([summary(2, later), summary(0, digest), summary(1, digest)]);
    expect(resolveBlockStatus('b-build', {}, r, { id: 'verify', index: 0 })).toBe('failed');
  });

  it('a live event still wins over the digest', () => {
    expect(resolveBlockStatus(
      'b-test', { 'b-test': 'running' }, runWith([summary(0, digest)], 'running'),
      { id: 'verify', index: 1 },
    )).toBe('running');
  });

  it('stays queued when there is no digest (pre-field record) or no loop', () => {
    const legacy = runWith([{ ...summary(0, undefined), stages: undefined }]);
    expect(resolveBlockStatus('b-build', {}, legacy, { id: 'verify', index: 0 })).toBe('queued');
    expect(resolveBlockStatus('b-build', {}, run)).toBe('queued');
    expect(resolveBlockStatus('b-build', {}, runWith([]), { id: 'verify', index: 0 })).toBe('queued');
  });

  it('stays queued for a position the digest does not cover', () => {
    expect(resolveBlockStatus('b-x', {}, run, { id: 'verify', index: 7 })).toBe('queued');
  });
});

describe('the 3068d3d0 shape end to end', () => {
  it('shows a done loop over never-run children as what they were', () => {
    // Every child "passed" with zero tool calls -- the run map cannot judge
    // that, but it must at least stop saying 'queued' under a 'done' loop.
    const digest = BODY.map((b, i) => ({
      index: i, label: b.name, status: 'passed', tool_calls: 0,
    }));
    const run = runWith([summary(0, digest), summary(1, digest, true)]);
    const rows = flattenBlocks(loop('verify', BODY));
    const statuses = rows.map(r => resolveBlockStatus(r.block.id, {}, run, r.loop));
    expect(statuses).toEqual(['done', 'done', 'done', 'done']);
  });
});
