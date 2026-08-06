/**
 * Tests for the auto-collapse policy (B6).
 *
 * A finished tile folds itself to a one-line receipt, which is right for
 * a run you glanced at and wrong for one you are reading: the whole
 * inspector gets yanked away mid-sentence and every section you expanded
 * has to be reopened.  The B5 completion footer made this worse — it is
 * precisely the cue that says "the trace is worth a last look", and it
 * lives inside the drawer the timer closes.
 *
 * Two rules, kept in a pure helper so they are testable without timers
 * or a rendered tile:
 *
 *   1. Engagement defers the collapse.
 *   2. A run awaiting the user never collapses at all.
 *
 * The second is why this file exists at all rather than a one-line
 * change: ``deriveRunControls`` returns ``isHeld: false`` for
 * status==='held' (the terminal branch spreads IDLE), so the obvious
 * guard — keying on ``controls.isHeld`` — silently never fires for the
 * one status it was meant to protect.  That trap is pinned below.
 */

import {
  AUTO_COLLAPSE_MS, ENGAGED_QUIET_MS, awaitsUser, decideAutoCollapse,
} from '../autoCollapse';
import { deriveRunControls } from '../runControls';
import type { RunStatus, TaskRun } from '../../../types/task_run';

const NOW = 1_700_000_000_000;

function run(status: RunStatus): TaskRun {
  return { id: 'run-1', status } as TaskRun;
}

// ── the untouched case: original behaviour preserved ─────────────────

describe('decideAutoCollapse — untouched tile', () => {
  it('arms on the original 8s delay', () => {
    const d = decideAutoCollapse(run('done'), true, true, null, NOW);
    expect(d).toEqual({ arm: true, delayMs: AUTO_COLLAPSE_MS });
  });

  it('does not arm before the run is terminal', () => {
    expect(decideAutoCollapse(run('running'), false, true, null, NOW).arm)
      .toBe(false);
  });

  it('does not arm when the tile is already collapsed', () => {
    // Nothing to fold away; arming would queue a pointless setState.
    expect(decideAutoCollapse(run('done'), true, false, null, NOW).arm)
      .toBe(false);
  });

  it('does not arm for a missing run', () => {
    // Unreachable from the tile (it early-returns a loading state), but
    // arming a timer for a run whose status cannot be read is a guess,
    // and the guess that HIDES UI is the wrong one to make.
    expect(decideAutoCollapse(null, true, true, null, NOW).arm).toBe(false);
  });
});

// ── engagement defers ───────────────────────────────────────────────

describe('decideAutoCollapse — engaged tile', () => {
  it('defers by the full quiet period on a fresh interaction', () => {
    const d = decideAutoCollapse(run('done'), true, true, NOW, NOW);
    expect(d).toEqual({ arm: true, delayMs: ENGAGED_QUIET_MS });
  });

  it('waits out only the REMAINDER of the quiet period', () => {
    const elapsed = 10_000;
    const d = decideAutoCollapse(
      run('done'), true, true, NOW - elapsed, NOW,
    );
    expect(d.delayMs).toBe(ENGAGED_QUIET_MS - elapsed);
  });

  it('collapses promptly once the quiet period has fully elapsed', () => {
    const d = decideAutoCollapse(
      run('done'), true, true, NOW - ENGAGED_QUIET_MS - 5_000, NOW,
    );
    expect(d).toEqual({ arm: true, delayMs: 0 });
  });

  it('never returns a negative delay', () => {
    // setTimeout treats a negative delay as 0, but a negative value here
    // would mean the arithmetic was wrong somewhere upstream.
    const d = decideAutoCollapse(
      run('done'), true, true, NOW - 10 * ENGAGED_QUIET_MS, NOW,
    );
    expect(d.delayMs).toBeGreaterThanOrEqual(0);
  });

  it('gives an engaged user strictly longer than an untouched tile', () => {
    const touched = decideAutoCollapse(run('done'), true, true, NOW, NOW);
    const untouched = decideAutoCollapse(run('done'), true, true, null, NOW);
    expect(touched.delayMs).toBeGreaterThan(untouched.delayMs);
  });
});

// ── awaitsUser, and the isHeld trap ─────────────────────────────────

describe('awaitsUser', () => {
  it('is true for a held run', () => {
    expect(awaitsUser(run('held'))).toBe(true);
  });

  it('is false for every genuinely-finished status', () => {
    for (const s of ['done', 'partial', 'failed', 'cancelled'] as RunStatus[]) {
      expect(awaitsUser(run(s))).toBe(false);
    }
  });

  it('is false for a live run and for no run', () => {
    expect(awaitsUser(run('running'))).toBe(false);
    expect(awaitsUser(null)).toBe(false);
    expect(awaitsUser(undefined)).toBe(false);
  });

  it('does NOT rely on controls.isHeld, which is false when held', () => {
    // The trap this helper exists to avoid.  deriveRunControls returns a
    // spread of IDLE for any terminal status, so ``isHeld`` describes
    // only a PAUSED/STEPPING (non-terminal) run.  A guard written
    // against it would never fire for status==='held' — which is exactly
    // what the receipt's held chip did before this change.
    const c = deriveRunControls(run('held'));
    expect(c.isTerminal).toBe(true);
    expect(c.isHeld).toBe(false);          // the trap, stated
    expect(awaitsUser(run('held'))).toBe(true);  // what we use instead
  });
});

describe('decideAutoCollapse — held run', () => {
  it('never arms, even untouched', () => {
    // The receipt carries no controls, so folding away would strand the
    // run behind an extra click with no hint that it needs one.
    expect(decideAutoCollapse(run('held'), true, true, null, NOW).arm)
      .toBe(false);
  });

  it('never arms when engaged either', () => {
    expect(decideAutoCollapse(run('held'), true, true, NOW, NOW).arm)
      .toBe(false);
  });

  it('still arms for a done run, so the suppression is status-specific', () => {
    // Guards against the suppression being accidentally universal.
    expect(decideAutoCollapse(run('done'), true, true, null, NOW).arm)
      .toBe(true);
  });
});

// ── rule 3: an expand by hand pins the tile open ─────────────────────

describe('decideAutoCollapse — manually expanded tile', () => {
  it('never arms, even untouched otherwise', () => {
    // The reported bug: opening a collapsed receipt stamped no
    // interaction, so the effect re-armed on the UNTOUCHED 8s delay and
    // the tile the user had just opened folded shut again.
    expect(
      decideAutoCollapse(run('done'), true, true, null, NOW, true).arm,
    ).toBe(false);
  });

  it('never arms even after the quiet period has long elapsed', () => {
    // Deferring alone was not enough: a reader still on the same trace
    // 30s later got it closed under them anyway.
    expect(
      decideAutoCollapse(
        run('done'), true, true, NOW - 10 * ENGAGED_QUIET_MS, NOW, true,
      ).arm,
    ).toBe(false);
  });

  it('reports a zero delay when it declines to arm', () => {
    // delayMs is documented as meaningless when !arm; keeping it 0
    // rather than leaving a stale computed value avoids a caller that
    // reads it anyway scheduling a surprise collapse.
    expect(
      decideAutoCollapse(run('done'), true, true, NOW, NOW, true),
    ).toEqual({ arm: false, delayMs: 0 });
  });

  it('still arms when the tile was NOT opened by hand', () => {
    // The suppression must be specific to a manual expand, or it would
    // disable auto-collapse for every tile that merely rendered open.
    expect(
      decideAutoCollapse(run('done'), true, true, null, NOW, false).arm,
    ).toBe(true);
  });

  it('defaults to not-pinned when the flag is omitted', () => {
    // Backward compatibility with the 5-argument call shape: omitting
    // the flag must preserve the pre-pin behaviour exactly, not
    // accidentally pin every tile.
    expect(decideAutoCollapse(run('done'), true, true, null, NOW).arm)
      .toBe(true);
  });

  it('is irrelevant while the tile is collapsed', () => {
    // A pin describes an OPEN tile; a collapsed one has nothing to fold.
    expect(
      decideAutoCollapse(run('done'), true, false, null, NOW, true).arm,
    ).toBe(false);
  });
});

describe('collapse timings', () => {
  it('keeps the untouched delay at the original 8s', () => {
    // Not an arbitrary constant: changing it silently alters behaviour
    // users are accustomed to.
    expect(AUTO_COLLAPSE_MS).toBe(8000);
  });

  it('makes the engaged quiet period longer than the untouched delay', () => {
    // The asymmetry is the point: collapsing too early on someone who is
    // reading costs far more than a tile that lingers.
    expect(ENGAGED_QUIET_MS).toBeGreaterThan(AUTO_COLLAPSE_MS);
  });
});
