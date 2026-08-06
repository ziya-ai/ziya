/**
 * Tests for partialOutcome — the banner/rail derivations.
 *
 * The bug these back: a run that completed 4 of 7 stages (writing files
 * along the way) rendered identically to one that died on stage one
 * having touched nothing.  Both were flat red "Failed", which reads as
 * "nothing happened" and discourages the user from looking for changes
 * the run left in their workspace.
 */

import type { TaskRun, TaskRunBlockState } from '../../../types/task_run';
import {
  attemptSummary, firstFailedBlock, isPartial, progressCounts,
  progressPhrase, provenance, resumeKindLabel, sideEffectSummary, sideEffects,
} from '../partialOutcome';

function block(
  id: string,
  status: TaskRunBlockState['status'],
  extra: Partial<TaskRunBlockState> = {},
): TaskRunBlockState {
  return {
    block_id: id, block_type: 'task', status,
    iteration_summaries: [], ...extra,
  };
}

function run(over: Partial<TaskRun> = {}): TaskRun {
  return {
    id: 'r1', card_id: 'c1', status: 'partial',
    cancel_requested: false, pause_requested: false,
    block_states: {}, total_tokens: 0, total_tool_calls: 0,
    created_at: 0, updated_at: 0, ...over,
  } as TaskRun;
}

function states(...list: TaskRunBlockState[]) {
  return Object.fromEntries(list.map(s => [s.block_id, s]));
}

/**
 * The exact shape of a five-iteration Until card that passed every
 * iteration and then hit an infrastructure fault.
 *
 * ``_mark_block_status`` does not persist a block's status while it is
 * inside an active loop iteration, so the four body blocks keep the
 * ``queued`` status seeding gave them and only the container reaches a
 * terminal one.  That is what made a fully-productive run report "0 of
 * 5 stages completed".
 */
function loopRun(over: Partial<TaskRun> = {}): TaskRun {
  const iters = [0, 1, 2, 3, 4].map(i => ({
    index: i, status: 'passed' as const, duration_ms: 1, has_artifact: true,
  }));
  return run({
    status: 'partial',
    block_states: states(
      { block_id: 'root', block_type: 'until', status: 'failed',
        iteration_summaries: iters } as TaskRunBlockState,
      block('b1', 'queued'), block('b2', 'queued'),
      block('b3', 'queued'), block('b4', 'queued'),
    ),
    ...over,
  });
}

describe('progressCounts — loop iteration progress', () => {
  it('reproduces the zero-stage structural count', () => {
    // Pins the underlying data shape, so the fix below cannot be made
    // to pass by quietly changing what the executor persists.
    const p = progressCounts(loopRun());
    expect(p.completed).toBe(0);
    expect(p.total).toBe(5);
  });

  it('counts the passed iterations the stage figure misses', () => {
    expect(progressCounts(loopRun()).passedIterations).toBe(5);
  });

  it('counts failed iterations separately', () => {
    const p = progressCounts(run({
      block_states: states({
        block_id: 'r', block_type: 'repeat', status: 'failed',
        iteration_summaries: [
          { index: 0, status: 'passed', duration_ms: 1, has_artifact: true },
          { index: 1, status: 'failed', duration_ms: 1, has_artifact: true },
        ],
      } as TaskRunBlockState),
    }));
    expect(p.passedIterations).toBe(1);
    expect(p.failedIterations).toBe(1);
  });

  it('counts iterations owned by an invisible group wrapper', () => {
    // Groups are excluded from the STAGE count (they have no row), but
    // an iteration is work that happened regardless of whether its
    // owner is drawn.
    const p = progressCounts(run({
      block_states: states({
        block_id: 'g', block_type: 'group', status: 'failed',
        iteration_summaries: [
          { index: 0, status: 'passed', duration_ms: 1, has_artifact: true },
        ],
      } as TaskRunBlockState),
    }));
    expect(p.total).toBe(0);
    expect(p.passedIterations).toBe(1);
  });

  it('reports zero for a run with no iterations at all', () => {
    const p = progressCounts(run({
      block_states: states(block('a', 'done')),
    }));
    expect(p.passedIterations).toBe(0);
    expect(p.failedIterations).toBe(0);
  });

  it('tolerates a missing iteration_summaries array', () => {
    const p = progressCounts(run({
      block_states: { x: { block_id: 'x', block_type: 'task',
        status: 'done' } as TaskRunBlockState },
    }));
    expect(p.passedIterations).toBe(0);
  });
});

describe('progressPhrase', () => {
  it('reports both units so neither reads as no progress', () => {
    // The regression: a stage-only phrase said "0 of 5 stages
    // completed" beside a dot strip reading "5 passed".
    const phrase = progressPhrase(progressCounts(loopRun()));
    expect(phrase).toContain('0 of 5 stages completed');
    expect(phrase).toContain('5 loop iterations passed');
  });

  it('omits the iteration clause when there are none', () => {
    const phrase = progressPhrase(progressCounts(run({
      block_states: states(block('a', 'done'), block('b', 'failed')),
    })));
    expect(phrase).toBe('1 of 2 stages completed');
  });

  it('reports iterations alone when there are no visible stages', () => {
    const phrase = progressPhrase({
      completed: 0, total: 0, failed: 0, skipped: 0,
      passedIterations: 3, failedIterations: 0,
    });
    expect(phrase).toBe('3 loop iterations passed');
  });

  it('singularises one iteration', () => {
    expect(progressPhrase({
      completed: 0, total: 0, failed: 0, skipped: 0,
      passedIterations: 1, failedIterations: 0,
    })).toBe('1 loop iteration passed');
  });

  it('is empty when there is nothing to report', () => {
    expect(progressPhrase(progressCounts(run({})))).toBe('');
  });
});

describe('progressCounts', () => {
  it('counts each terminal category', () => {
    const p = progressCounts(run({
      block_states: states(
        block('a', 'done'), block('b', 'done'),
        block('c', 'failed'), block('d', 'skipped'),
      ),
    }));
    // Whole-object equality, deliberately: it is what caught the two
    // iteration counters being added, and a loosened toMatchObject here
    // would let a future field ship unexamined.  Both counters are zero
    // because none of these blocks is a loop.
    expect(p).toEqual({
      completed: 2, total: 4, failed: 1, skipped: 1,
      passedIterations: 0, failedIterations: 0,
    });
  });

  it('excludes group wrappers so N-of-M matches the visible rows', () => {
    // runMapModel.flattenBlocks renders groups chromeless, so counting
    // them yields a figure that disagrees with what is on screen.
    const p = progressCounts(run({
      block_states: states(
        block('g', 'done', { block_type: 'group' }),
        block('a', 'done'), block('b', 'failed'),
      ),
    }));
    expect(p.total).toBe(2);
    expect(p.completed).toBe(1);
  });

  it('tolerates a missing run or empty states', () => {
    expect(progressCounts(null).total).toBe(0);
    expect(progressCounts(run()).total).toBe(0);
  });
});

describe('firstFailedBlock', () => {
  it('picks the earliest failure, not a propagating container', () => {
    // Under on_failure=stop the first failure ended the run; later
    // 'failed' entries are ancestors carrying it upward, and naming one
    // of those would point the user at the wrong stage.
    const found = firstFailedBlock(run({
      block_states: states(
        block('outer', 'failed', { block_type: 'group', completed_at: 300 }),
        block('inner', 'failed', { completed_at: 100 }),
      ),
    }));
    expect(found?.block_id).toBe('inner');
  });

  it('returns null when nothing failed', () => {
    expect(firstFailedBlock(run({
      block_states: states(block('a', 'done')),
    }))).toBeNull();
  });

  it('treats a missing completed_at as latest rather than crashing', () => {
    const found = firstFailedBlock(run({
      block_states: states(
        block('no-ts', 'failed'),
        block('has-ts', 'failed', { completed_at: 50 }),
      ),
    }));
    expect(found?.block_id).toBe('has-ts');
  });
});

describe('sideEffects', () => {
  const snapshot = (scopes: Record<string, any>) =>
    ({ schema_version: 1, block_scopes: scopes });

  it('reports a completed block that held a write grant', () => {
    const effects = sideEffects(run({
      permissions_snapshot: snapshot({
        b1: {
          block_name: 'Rewrite adapter',
          paths: [{ path: 'app/', write: true }],
        },
      }),
      block_states: states(block('b1', 'done')),
    }));
    expect(effects).toHaveLength(1);
    expect(effects[0].blockName).toBe('Rewrite adapter');
    expect(effects[0].hadWriteGrant).toBe(true);
  });

  it('reports a shell grant as a hazard', () => {
    const effects = sideEffects(run({
      permissions_snapshot: snapshot({
        b1: { block_name: 'Migrate', shell_commands: ['git'] },
      }),
      block_states: states(block('b1', 'done')),
    }));
    expect(effects).toHaveLength(1);
  });

  it('ignores a read-only block', () => {
    expect(sideEffects(run({
      permissions_snapshot: snapshot({
        b1: { block_name: 'Inventory', paths: [{ path: 'app/', write: false }] },
      }),
      block_states: states(block('b1', 'done')),
    }))).toEqual([]);
  });

  it('excludes a block that never ran', () => {
    expect(sideEffects(run({
      permissions_snapshot: snapshot({
        b1: { block_name: 'Later', paths: [{ path: 'app/', write: true }] },
      }),
      block_states: states(block('b1', 'queued')),
    }))).toEqual([]);
  });

  it('includes a FAILED block with a write grant', () => {
    // It may have written before it crashed; excluding it understates
    // the hazard, which is the whole thing the banner exists to state.
    const effects = sideEffects(run({
      permissions_snapshot: snapshot({
        b1: { block_name: 'Half-written', paths: [{ path: 'app/', write: true }] },
      }),
      block_states: states(block('b1', 'failed')),
    }));
    expect(effects).toHaveLength(1);
    expect(effects[0].status).toBe('failed');
  });

  it('lists declared file artifacts by file_uri', () => {
    const effects = sideEffects(run({
      permissions_snapshot: snapshot({
        b1: { block_name: 'Rewrite', paths: [{ path: 'app/', write: true }] },
      }),
      block_states: states(block('b1', 'done', {
        artifact: {
          summary: 'wrote', decisions: [], tokens: 0, tool_calls: 0,
          duration_ms: 0, created_at: 0, failed: false,
          outputs: [{ part_type: 'file', name: 'adapter',
                      file_uri: '/proj/app/adapter.py' }],
        } as any,
      })),
    }));
    expect(effects[0].files).toEqual(['/proj/app/adapter.py']);
  });

  it('reports a declared file even with no recorded grant', () => {
    // An emitted file is direct evidence of a change even when the
    // snapshot shows no explicit grant (e.g. covered by base policy).
    const effects = sideEffects(run({
      block_states: states(block('b1', 'done', {
        artifact: {
          summary: '', decisions: [], tokens: 0, tool_calls: 0,
          duration_ms: 0, created_at: 0, failed: false,
          outputs: [{ part_type: 'file', file_uri: '.ziya/notes.md' }],
        } as any,
      })),
    }));
    expect(effects).toHaveLength(1);
  });

  it('tolerates a missing snapshot', () => {
    expect(sideEffects(run({ block_states: states(block('b1', 'done')) })))
      .toEqual([]);
    expect(sideEffects(null)).toEqual([]);
  });
});

describe('sideEffectSummary', () => {
  it('names the file count when files were declared', () => {
    const msg = sideEffectSummary(run({
      permissions_snapshot: { block_scopes: {
        b1: { block_name: 'W', paths: [{ path: 'a', write: true }] },
      } },
      block_states: states(block('b1', 'done', {
        artifact: {
          summary: '', decisions: [], tokens: 0, tool_calls: 0,
          duration_ms: 0, created_at: 0, failed: false,
          outputs: [
            { part_type: 'file', file_uri: 'a.py' },
            { part_type: 'file', file_uri: 'b.py' },
          ],
        } as any,
      })),
    }));
    expect(msg).toContain('2 changed files');
  });

  it('says "may have changed" when a grant was held but nothing declared', () => {
    // Must not claim nothing changed: an undeclared write is invisible
    // here, so over-confidence would be worse than the flat red status.
    const msg = sideEffectSummary(run({
      permissions_snapshot: { block_scopes: {
        b1: { block_name: 'W', shell_commands: ['git'] },
      } },
      block_states: states(block('b1', 'done')),
    }));
    expect(msg).toMatch(/may have changed/);
  });

  it('returns null when there is no hazard', () => {
    expect(sideEffectSummary(run({
      block_states: states(block('b1', 'done')),
    }))).toBeNull();
  });
});

describe('attemptSummary', () => {
  it('states how far a partial got', () => {
    expect(attemptSummary(run({
      status: 'partial',
      block_states: states(
        block('a', 'done'), block('b', 'done'),
        block('c', 'failed'), block('d', 'skipped'),
      ),
    }))).toBe('partial — 2 of 4 stages');
  });

  it('states completion for a done run', () => {
    expect(attemptSummary(run({
      status: 'done',
      block_states: states(block('a', 'done'), block('b', 'done')),
    }))).toBe('completed all 2 stages');
  });

  it('falls back to the bare status with no block states', () => {
    expect(attemptSummary(run({ status: 'failed' }))).toBe('failed');
  });
});

describe('resumeKindLabel', () => {
  it('labels each kind', () => {
    expect(resumeKindLabel(run({ resume_kind: 'retry_from' })))
      .toBe('manual retry');
    expect(resumeKindLabel(run({ resume_kind: 'continue_from' })))
      .toBe('manual continue');
    expect(resumeKindLabel(run({ resume_kind: 'rerun' }))).toBe('rerun');
  });

  it('treats a pre-lineage record as an initial run', () => {
    // Absent resume_kind is indistinguishable from 'initial', and that
    // is what those runs were.
    expect(resumeKindLabel(run({}))).toBe('initial run');
  });
});

describe('provenance', () => {
  it('splits replayed from executed stages', () => {
    // 'skipped' WITH an artifact is the resume gate's marker for
    // "inherited from an earlier attempt" — see _replay_artifact.
    const p = provenance(run({
      resume_kind: 'continue_from',
      resumed_from_block_id: 'b2',
      block_states: states(
        block('b1', 'skipped', { artifact: { summary: 'x' } as any }),
        block('b2', 'skipped', { artifact: { summary: 'y' } as any }),
        block('b3', 'done'), block('b4', 'done'),
      ),
    }));
    expect(p).toEqual({
      replayed: 2, executed: 2,
      resumedFromBlockId: 'b2', kind: 'continue_from',
    });
  });

  it('does not count a skipped block with no artifact as replayed', () => {
    // A block skipped by on_failure=stop never ran and inherited
    // nothing, so calling it replayed would overstate what was kept.
    const p = provenance(run({
      resume_kind: 'retry_from',
      block_states: states(block('b1', 'skipped'), block('b2', 'done')),
    }));
    expect(p?.replayed).toBe(0);
    expect(p?.executed).toBe(1);
  });

  it('returns null for an initial launch', () => {
    // Nothing was inherited, so there is no provenance story to tell.
    expect(provenance(run({ resume_kind: 'initial' }))).toBeNull();
    expect(provenance(run({}))).toBeNull();
  });
});

describe('isPartial', () => {
  it('is true only for the partial status', () => {
    expect(isPartial(run({ status: 'partial' }))).toBe(true);
    expect(isPartial(run({ status: 'failed' }))).toBe(false);
    expect(isPartial(null)).toBe(false);
  });
});
