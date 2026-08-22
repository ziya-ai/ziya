/**
 * Wiring tests for follow-mode.
 *
 * The pure policy is covered in followMode.test.ts.  These assert the
 * TILE actually consults it, because every interesting failure here is a
 * wiring failure: a helper that is imported but never called, a click
 * handler that forgets to pin, or a mode reset that is missing on the
 * attempt-switch path.  Each of those leaves followMode.test.ts fully
 * green while the feature does nothing.
 *
 * Source assertions rather than a rendered tile: LaunchedCardTile needs a
 * ProjectContext, a WebSocket, three API modules and a live run before it
 * renders anything, and the earlier suites in this directory already use
 * this pattern for exactly that reason.
 */

import * as fs from 'fs';
import * as path from 'path';

const TILE = fs.readFileSync(
  path.resolve(__dirname, '../TaskCardInlineTile.tsx'), 'utf-8',
);
const CSS = fs.readFileSync(
  path.resolve(__dirname, '../task-card-inline-tile.css'), 'utf-8',
);

describe('the tile consults the follow policy', () => {
  it('imports the helper rather than re-deriving a target', () => {
    expect(TILE).toMatch(/import \{[^}]*followTarget[^}]*\} from '\.\/followMode'/);
  });

  it('tracks focus mode in state', () => {
    expect(TILE).toMatch(/useState<FocusMode>\('following'\)/);
  });

  it('defaults to following, so a live run shows work immediately', () => {
    // The whole point: an unfocused live tile showed the whole-run
    // artifact, which does not exist until the run ends.
    expect(TILE).toMatch(/useState<FocusMode>\('following'\)/);
    expect(TILE).not.toMatch(/useState<FocusMode>\('pinned'\)/);
  });

  it('auto-focuses from the derived target', () => {
    expect(TILE).toMatch(/followTarget\(run, live\)/);
  });

  it('gates the auto-focus effect on the mode', () => {
    expect(TILE).toMatch(/if \(focusMode !== 'following'\) return;/);
  });

  it('keys the effect on the target, not on the whole live object', () => {
    // ``live`` changes on every text delta (many per second); the target
    // changes at stage boundaries.  Keying on ``live`` would re-run the
    // effect continuously for a value that rarely differs.
    expect(TILE).toMatch(/\}, \[autoTarget, focusMode\]\);/);
  });

  it('skips the state write when the target has not changed', () => {
    // Returning ``prev`` unchanged is what keeps a per-delta re-render
    // from re-focusing (and re-rendering the detail subtree) for nothing.
    expect(TILE).toMatch(/\?\s*prev\s*$/m);
  });
});

describe('a manual click pins, so the run cannot drag the view away', () => {
  it('pins on row/dot focus', () => {
    const onFocus = TILE.slice(
      TILE.indexOf('const onFocus = useCallback'),
      TILE.indexOf('const clearFocus = useCallback'),
    );
    expect(onFocus).toMatch(/setFocusMode\('pinned'\)/);
  });

  it('pins on "Whole run" too', () => {
    // Otherwise follow-mode re-focuses a block immediately and the click
    // appears to do nothing.
    const clearFocus = TILE.slice(
      TILE.indexOf('const clearFocus = useCallback'),
      TILE.indexOf('const resumeFollowing = useCallback'),
    );
    expect(clearFocus).toMatch(/setFocusMode\('pinned'\)/);
  });
});

describe('the way back exists and is honest', () => {
  it('offers resume-following via the gated helper', () => {
    expect(TILE).toMatch(/canResumeFollowing\(focusMode, isLive\)/);
  });

  it('jumps to the active block at once rather than awaiting an event', () => {
    // On a slow-streaming run, waiting for the next delta would make the
    // click look inert.
    const resume = TILE.slice(
      TILE.indexOf('const resumeFollowing = useCallback'),
      TILE.indexOf('const autoTarget'),
    );
    expect(resume).toMatch(/setFocusMode\('following'\)/);
    expect(resume).toMatch(/followTarget\(run, live\)/);
  });

  it('shows a following indicator only while the run is live', () => {
    // A "following" badge on a finished run would claim to be tracking
    // something that has stopped.
    expect(TILE).toMatch(/focusMode === 'following' && isLive/);
  });
});

describe('focus mode is scoped to the attempt being viewed', () => {
  it('resets to following when switching attempts', () => {
    // A pin set while reading a finished attempt says nothing about how
    // the user wants to watch a different, possibly live, one.
    const selectAttempt = TILE.slice(
      TILE.indexOf('const selectAttempt = useCallback'),
      TILE.indexOf('}, [clearLive]);'),
    );
    expect(selectAttempt).toMatch(/setFocusMode\('following'\)/);
  });
});

describe('follow-mode styling', () => {
  it('defines the mode indicator', () => {
    expect(CSS).toMatch(/\.tc-focus__mode--following\s*\{/);
  });

  it('defines the resume control', () => {
    expect(CSS).toMatch(/\.tc-focus__follow\s*\{/);
  });

  it('is keyboard-reachable, not hover-only', () => {
    // The map's row controls are hover-revealed; this one must not be —
    // it appears exactly when the view is known to be stale, which is
    // when it matters most.
    expect(CSS).toMatch(/\.tc-focus__follow:focus-visible\s*\{/);
  });

  it('lays the crumb and mode out on one row', () => {
    const m = CSS.match(/\.tc-focus__bar\s*\{([^}]*)\}/);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/display:\s*flex/);
  });
});
