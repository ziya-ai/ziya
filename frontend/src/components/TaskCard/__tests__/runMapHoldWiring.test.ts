/**
 * Run-map hold wiring — the seam between holdChain and the rendered map.
 *
 * holdChain.test.ts proves the DERIVATION is correct.  That is not the same
 * as the map using it: the earlier infra-hold bug was a wiring defect whose
 * every unit test passed, so these assert the pieces the component depends
 * on rather than the logic it delegates to.
 *
 * Two specific regressions are pinned:
 *
 * 1. STATUS_GLYPHS had no 'held' entry, so the component's `?? '○'`
 *    fallback painted the faulting block identically to a QUEUED one.  The
 *    backend gained a distinct 'held' block status and the map flattened it
 *    straight back into "hasn't started yet".
 *
 * 2. resolveBlockStatus's terminal backstop omitted 'held', so a block left
 *    'running' when a held run unwound stayed 'running' forever — a
 *    subagent visibly spinning on a run that had already stopped.
 */

import { resolveBlockStatus } from '../runMapModel';
import { deriveHoldChain, positionOf } from '../holdChain';
import type { TaskRun } from '../../../types/task_run';
import type { Block } from '../../../types/task_card';

/** Minimal run; only the fields the functions under test read. */
function run(over: Partial<TaskRun> = {}): TaskRun {
  return {
    id: 'r1', card_id: 'c1', status: 'running',
    block_states: {}, ...over,
  } as TaskRun;
}

function task(id: string, name = id): Block {
  return { id, name, block_type: 'task', instructions: 'go' } as Block;
}

function group(id: string, body: Block[]): Block {
  return { id, name: id, block_type: 'group', body } as Block;
}

function repeat(id: string, body: Block[]): Block {
  return {
    id, name: id, block_type: 'repeat', repeat_parallel: true, body,
  } as Block;
}

describe('resolveBlockStatus under a held run', () => {
  it('degrades a stale running block instead of leaving it spinning', () => {
    const r = run({
      status: 'held',
      held_at_block_id: 'auditor',
      block_states: {
        // Persisted as running: the executor never got to write a
        // terminal status because the coroutine unwound.
        sibling: { block_id: 'sibling', block_type: 'task', status: 'running',
                   iteration_summaries: [] },
      },
    });
    expect(resolveBlockStatus('sibling', {}, r)).not.toBe('running');
  });

  it('degrades it to cancelled, not held', () => {
    // Painting every stale-running block 'held' would claim N faults where
    // there was one, and make the actual fault location unfindable.
    const r = run({
      status: 'held',
      held_at_block_id: 'auditor',
      block_states: {
        sibling: { block_id: 'sibling', block_type: 'task', status: 'running',
                   iteration_summaries: [] },
      },
    });
    expect(resolveBlockStatus('sibling', {}, r)).toBe('cancelled');
  });

  it('leaves an explicitly held block held', () => {
    const r = run({
      status: 'held',
      held_at_block_id: 'auditor',
      block_states: {
        auditor: { block_id: 'auditor', block_type: 'task', status: 'held',
                   iteration_summaries: [] },
      },
    });
    expect(resolveBlockStatus('auditor', {}, r)).toBe('held');
  });

  it('does not disturb completed blocks', () => {
    const r = run({
      status: 'held',
      block_states: {
        recon: { block_id: 'recon', block_type: 'task', status: 'done',
                 iteration_summaries: [] },
      },
    });
    expect(resolveBlockStatus('recon', {}, r)).toBe('done');
  });
});

describe('every block gets a position, so no row is unlabelled', () => {
  /**
   * The requirement: the hold must be legible from wherever the user is
   * looking.  A derivation that labels only the faulting block satisfies
   * the type signature while completely failing that, and nothing else
   * would catch it.
   */
  const tree = group('root', [
    task('params'),
    group('phase1', [
      task('recon'),
      repeat('fanout', [task('auditor')]),
      task('merge'),
    ]),
    task('phase2'),
  ]);

  const held = run({
    status: 'held',
    held_reason: 'authentication_error',
    held_at_block_id: 'fanout',
    held_faults: {
      fault_count: 18, fanout_width: 20,
      primary_kind: 'authentication_error',
      kinds: { authentication_error: 18 },
      call_path: ['CL0', 'CL1', 'audit-mcp'],
      fleet_wide: true, block_ids: ['auditor'],
    },
  });

  const ids = ['root', 'params', 'phase1', 'recon', 'fanout', 'auditor',
               'merge', 'phase2'];

  it('labels the faulting block local', () => {
    const chain = deriveHoldChain(held, tree);
    expect(positionOf(chain, 'fanout')).toBe('local');
  });

  it('labels containers above the fault as descendant', () => {
    const chain = deriveHoldChain(held, tree);
    expect(positionOf(chain, 'phase1')).toBe('descendant');
    expect(positionOf(chain, 'root')).toBe('descendant');
  });

  it('labels blocks beneath the fault as ancestor', () => {
    const chain = deriveHoldChain(held, tree);
    expect(positionOf(chain, 'auditor')).toBe('ancestor');
  });

  it('leaves no block on the tree unlabelled', () => {
    const chain = deriveHoldChain(held, tree);
    const unlabelled = ids.filter(id => positionOf(chain, id) === 'none');
    expect(unlabelled).toEqual([]);
  });

  it('is inert for a run that did not hold', () => {
    const chain = deriveHoldChain(run({ status: 'done' }), tree);
    expect(chain.isHeld).toBe(false);
    for (const id of ids) expect(positionOf(chain, id)).toBe('none');
  });
});
