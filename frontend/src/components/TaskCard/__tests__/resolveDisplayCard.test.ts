/**
 * Unit tests for resolveDisplayCard — the launch-time snapshot vs
 * live-card selection that keeps a completed run's displayed
 * definition frozen against later card edits.
 */

// resolveDisplayCard is a pure function, but importing it from
// TaskCardInlineTile drags in the component's module-load chain
// (ProjectContext -> db.ts -> ESM `uuid`, which jest's default
// transform can't parse).  Mock the heavy leaves so the module loads;
// none are exercised by the pure helper under test.  Mirrors the
// mocks in TaskCardInlineTile.test.tsx.
jest.mock('../../MarkdownRenderer', () => ({
  __esModule: true,
  MarkdownRenderer: () => null,
}));
jest.mock('../../../context/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 'proj-1' } }),
}));

import { resolveDisplayCard } from '../TaskCardInlineTile';
import type { TaskCard, Block } from '../../../types/task_card';
import type { TaskRun } from '../../../types/task_run';

const leaf = (instructions: string): Block =>
  ({ id: 'b1', block_type: 'task', instructions } as unknown as Block);

const makeCard = (name: string, instructions: string): TaskCard =>
  ({
    id: 'card-1',
    name,
    description: 'live desc',
    root: leaf(instructions),
  } as unknown as TaskCard);

const makeRun = (snapshot?: TaskRun['card_snapshot']): TaskRun =>
  ({
    id: 'run-1',
    card_id: 'card-1',
    status: 'done',
    cancel_requested: false,
    block_states: {},
    total_tokens: 0,
    total_tool_calls: 0,
    created_at: 0,
    updated_at: 0,
    ...(snapshot !== undefined ? { card_snapshot: snapshot } : {}),
  } as unknown as TaskRun);

describe('resolveDisplayCard', () => {
  it('prefers the snapshot over the live (edited) card', () => {
    const live = makeCard('Edited name', 'edited instructions');
    const run = makeRun({
      name: 'Original name',
      description: 'original desc',
      root: leaf('original instructions'),
    });
    const result = resolveDisplayCard(run, live)!;
    expect(result.name).toBe('Original name');
    expect(result.description).toBe('original desc');
    expect((result.root as any).instructions).toBe('original instructions');
  });

  it('falls back to the live card when no snapshot exists (legacy run)', () => {
    const live = makeCard('Live name', 'live instructions');
    const run = makeRun(); // no card_snapshot
    const result = resolveDisplayCard(run, live)!;
    expect(result.name).toBe('Live name');
    expect((result.root as any).instructions).toBe('live instructions');
  });

  it('returns the snapshot even when the live card has not loaded yet', () => {
    const run = makeRun({
      name: 'Snap name',
      description: '',
      root: leaf('snap instructions'),
    });
    const result = resolveDisplayCard(run, null)!;
    expect(result.name).toBe('Snap name');
    expect((result.root as any).instructions).toBe('snap instructions');
  });

  it('returns null when neither snapshot nor live card is available', () => {
    expect(resolveDisplayCard(makeRun(), null)).toBeNull();
    expect(resolveDisplayCard(null, null)).toBeNull();
  });

  it('null card_snapshot is treated as absent (falls back to live)', () => {
    const live = makeCard('Live', 'live');
    const run = makeRun(null);
    expect(resolveDisplayCard(run, live)!.name).toBe('Live');
  });
});
