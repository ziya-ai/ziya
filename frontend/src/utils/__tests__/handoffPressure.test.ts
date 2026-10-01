/**
 * The token bar's "⇢ Hand off…" affordance must key off whichever pressure
 * figure is HIGHER: the client's estimate of the next prompt, or the server's
 * record of what the provider actually billed on the last turn.  The two can
 * legitimately diverge — a turn's tool results grow the submitted context
 * mid-turn, which only the provider figure reflects.  Observed failure:
 * client 517k/1M (52%), provider 798k/1M (79%), no button.
 */
import { handoffPressureTier } from '../handoffPressure';

describe('handoffPressureTier', () => {
  it('hides when both figures are below the warning threshold', () => {
    expect(handoffPressureTier(100_000, 1_000_000, 0.4)).toBe('none');
    expect(handoffPressureTier(100_000, 1_000_000, null)).toBe('none');
  });

  it('shows the warning tier from the client estimate alone', () => {
    expect(handoffPressureTier(750_000, 1_000_000, null)).toBe('warning');
  });

  it('shows from the server figure when the client estimate is low (the reported case)', () => {
    expect(handoffPressureTier(517_051, 1_000_000, 0.798)).toBe('warning');
  });

  it('escalates to danger from either source', () => {
    expect(handoffPressureTier(950_000, 1_000_000, null)).toBe('danger');
    expect(handoffPressureTier(517_051, 1_000_000, 0.95)).toBe('danger');
  });

  it('never lets a lower server figure suppress a high client estimate', () => {
    expect(handoffPressureTier(950_000, 1_000_000, 0.2)).toBe('danger');
  });

  it('tolerates a missing or zero limit', () => {
    expect(handoffPressureTier(500, 0, null)).toBe('none');
    expect(handoffPressureTier(500, 0, 0.8)).toBe('warning');
  });
});
