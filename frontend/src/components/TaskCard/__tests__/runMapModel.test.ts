/**
 * Tests for runMapModel — the pure logic behind TaskRunMap.
 */

import {
  flattenBlocks, resolveBlockStatus, buildDots, blockLabel,
  blockConfigLines, findBlockById, MAX_DOTS,
} from '../runMapModel';
import type { Block } from '../../../types/task_card';
import type { TaskRun, IterationSummary } from '../../../types/task_run';

const task = (id: string, extra: Partial<Block> = {}): Block => ({
  block_type: 'task', id, name: '', body: [], ...extra,
});

const container = (
  type: Block['block_type'], id: string, body: Block[],
): Block => ({ block_type: type, id, name: '', body });

describe('flattenBlocks', () => {
  it('skips group wrappers, keeping children at the group depth', () => {
    const root = container('group', 'g', [
      task('t1'),
      container('repeat', 'r', [task('t2')]),
    ]);
    const rows = flattenBlocks(root);
    expect(rows.map(r => [r.block.id, r.depth])).toEqual([
      ['t1', 0], ['r', 0], ['t2', 1],
    ]);
  });

  it('increments depth for nested containers', () => {
    const root = container('repeat', 'r1', [
      container('parallel', 'p', [task('a'), task('b')]),
    ]);
    const rows = flattenBlocks(root);
    expect(rows.map(r => [r.block.id, r.depth])).toEqual([
      ['r1', 0], ['p', 1], ['a', 2], ['b', 2],
    ]);
  });

  it('returns empty for null root', () => {
    expect(flattenBlocks(null)).toEqual([]);
  });

  const call = (id: string, target: string): any => ({
    block_type: 'call', id, name: `call ${target}`,
    call_target: target, body: [],
  });
  const task = (id: string): any => ({
    block_type: 'task', id, name: id, instructions: 'x', body: [],
  });

  it('splices a resolved callee tree beneath its call row', () => {
    // Without this the callee's blocks stream status events that land on
    // no row, and the call row appears to produce output from nothing.
    const snaps = {
      'c1': { key: 'card:c2', target: 'Helper', root: task('callee-1') },
    };
    const rows = flattenBlocks(call('c1', 'Helper'), 0, snaps);
    expect(rows.map(r => r.block.id)).toEqual(['c1', 'callee-1']);
    expect(rows[1].depth).toBe(1);
  });

  it('marks callee rows with the call block they came through', () => {
    // Attribution matters: the block belongs to another card and runs
    // under that card's permissions.
    const snaps = {
      'c1': { key: 'card:c2', target: 'Helper', root: task('callee-1') },
    };
    const rows = flattenBlocks(call('c1', 'Helper'), 0, snaps);
    expect(rows[0].viaCall).toBeUndefined();
    expect(rows[1].viaCall).toBe('c1');
  });

  it('draws only the call row when no snapshot exists', () => {
    // A run that never reached the call, or predates call_snapshots.
    const rows = flattenBlocks(call('c1', 'Helper'), 0, {});
    expect(rows.map(r => r.block.id)).toEqual(['c1']);
  });

  it('omits callee rows when call_snapshots is absent entirely', () => {
    const rows = flattenBlocks(call('c1', 'Helper'));
    expect(rows.map(r => r.block.id)).toEqual(['c1']);
  });

  it('does not hang on a cyclic call record', () => {
    // The server rejects cycles, but a hand-edited or truncated run file
    // must not take the UI down with it.
    const inner = call('c-inner', 'Self');
    const snaps = {
      'c-outer': { key: 'card:self', target: 'Self', root: inner },
      'c-inner': { key: 'card:self', target: 'Self', root: inner },
    };
    const rows = flattenBlocks(call('c-outer', 'Self'), 0, snaps);
    expect(rows.length).toBeLessThan(10);
    expect(rows[0].block.id).toBe('c-outer');
  });

  it('propagates snapshots through nested containers', () => {
    const group: any = {
      block_type: 'group', id: 'g1', name: 'g1',
      body: [call('c1', 'Helper')],
    };
    const snaps = {
      'c1': { key: 'card:c2', target: 'Helper', root: task('callee-1') },
    };
    const rows = flattenBlocks(group, 0, snaps);
    expect(rows.map(r => r.block.id)).toEqual(['c1', 'callee-1']);
  });
});

const runWith = (
  status: TaskRun['status'],
  blockStates: TaskRun['block_states'] = {},
): TaskRun => ({
  id: 'run1', card_id: 'c1', status,
  cancel_requested: false, pause_requested: false, block_states: blockStates,
  total_tokens: 0, total_tool_calls: 0,
  created_at: 0, updated_at: 0,
});

const bs = (status: string) => ({
  block_id: 'x', block_type: 'task', status: status as any,
  iteration_summaries: [] as IterationSummary[],
});

describe('resolveBlockStatus', () => {
  it('prefers live status over persisted', () => {
    const run = runWith('running', { t1: bs('queued') });
    expect(resolveBlockStatus('t1', { t1: 'running' }, run)).toBe('running');
  });

  it('falls back to persisted block_states', () => {
    const run = runWith('running', { t1: bs('done') });
    expect(resolveBlockStatus('t1', {}, run)).toBe('done');
  });

  it('defaults to queued when nothing is known', () => {
    expect(resolveBlockStatus('t1', {}, runWith('running'))).toBe('queued');
  });

  it('degrades stale running to the terminal run status', () => {
    const run = runWith('failed', { t1: bs('running') });
    expect(resolveBlockStatus('t1', {}, run)).toBe('failed');
    const done = runWith('done', { t1: bs('running') });
    expect(resolveBlockStatus('t1', {}, done)).toBe('done');
  });

  it('leaves skipped/failed untouched on terminal runs', () => {
    const run = runWith('done', { t1: bs('skipped') });
    expect(resolveBlockStatus('t1', {}, run)).toBe('skipped');
  });
});

const summary = (
  index: number, status: 'passed' | 'failed' = 'passed',
): IterationSummary => ({
  index, status, duration_ms: 1, tokens: 0, has_artifact: true,
});

describe('buildDots', () => {
  it('maps summaries to dots with total', () => {
    const m = buildDots([summary(0), summary(1, 'failed')], false);
    expect(m.total).toBe(2);
    expect(m.overflow).toBe(0);
    expect(m.dots.map(d => d.status)).toEqual(['passed', 'failed']);
    expect(m.running).toBe(false);
  });

  it('caps at MAX_DOTS keeping the most recent, with overflow count', () => {
    const many = Array.from({ length: MAX_DOTS + 20 }, (_, i) => summary(i));
    const m = buildDots(many, true);
    expect(m.dots.length).toBe(MAX_DOTS);
    expect(m.overflow).toBe(20);
    expect(m.dots[0].index).toBe(20);   // oldest shown
    expect(m.running).toBe(true);
  });

  it('handles undefined summaries', () => {
    const m = buildDots(undefined, false);
    expect(m.total).toBe(0);
    expect(m.dots).toEqual([]);
  });
});

describe('blockLabel', () => {
  it('prefers explicit name', () => {
    expect(blockLabel(task('t', { name: 'Plan' }))).toBe('Plan');
  });

  it('falls back to first instructions line for tasks', () => {
    expect(blockLabel(task('t', { instructions: 'Do the thing\nmore' })))
      .toBe('Do the thing');
  });

  it('describes loop blocks by mode', () => {
    expect(blockLabel(container('repeat', 'r', []))).toBe('Repeat ×1');
    expect(blockLabel({
      ...container('repeat', 'r', []), repeat_mode: 'for_each',
    })).toBe('For each item');
  });
});

describe('blockConfigLines', () => {
  it('surfaces task instructions as a pre line', () => {
    const lines = blockConfigLines(task('t', { instructions: 'Do X' }));
    const instr = lines.find(l => l.label === 'Instructions');
    expect(instr).toEqual({ label: 'Instructions', value: 'Do X', pre: true });
  });

  it('describes a for_each repeat with source, propagate and on_failure', () => {
    const lines = blockConfigLines({
      ...container('repeat', 'r', []),
      repeat_mode: 'for_each',
      repeat_for_each_source: '{{sibling("plan")}}',
      repeat_propagate: 'none',
      on_failure: 'stop',
    });
    const byLabel = Object.fromEntries(lines.map(l => [l.label, l.value]));
    expect(byLabel['Mode']).toBe('for_each');
    expect(byLabel['For each']).toBe('{{sibling("plan")}}');
    expect(byLabel['Propagate']).toBe('none');
    expect(byLabel['On failure']).toBe('stop');
  });

  it('summarizes scope grants by count', () => {
    const lines = blockConfigLines(task('t', {
      scope: {
        paths: [{ path: 'a' }, { path: 'b' }],
        tools: ['x'], skills: [], shell_commands: ['pytest'],
        model_tier: 'small',
      } as any,
    }));
    const scope = lines.find(l => l.label === 'Scope')!.value;
    expect(scope).toContain('2 path(s)');
    expect(scope).toContain('1 tool(s)');
    expect(scope).toContain('1 shell grant(s)');
    expect(scope).toContain('tier: small');
  });

  it('shows state variables and context', () => {
    const lines = blockConfigLines({
      ...container('state', 's', []),
      state_context: 'assume prod',
      state_variables: { limit: 300 },
    });
    expect(lines.find(l => l.label === 'Context')?.value).toBe('assume prod');
    expect(lines.find(l => l.label === 'Variables')?.pre).toBe(true);
  });
});

describe('findBlockById', () => {
  it('finds a nested block', () => {
    const root = container('group', 'g', [
      task('t1'), container('repeat', 'r', [task('t2')]),
    ]);
    expect(findBlockById(root, 't2')?.id).toBe('t2');
    expect(findBlockById(root, 'nope')).toBeNull();
  });
});
