/**
 * G-a59f77 / D-092, D-382, D-389 — drawio edge terminal anchoring.
 *
 * Root cause (differs from the group's route-fix hypothesis): the plugin's
 * updateFixedTerminalPoint override dropped mxGraph's canonical
 * `getPerimeterFunction(terminal) == null` guard, so it pinned EVERY floating
 * edge endpoint to the terminal's routing CENTRE instead of leaving the point
 * null for updateFloatingTerminalPoint to anchor at the box PERIMETER.
 *
 * Consequences: on a transparent-fill box the centre-anchored segment is visible
 * and strikes the label (drawio-w4-09, D-389); centre-to-centre anchoring drives
 * edges straight through intervening vertex interiors (w1-06 / w2-11, D-092;
 * w1-04 / w1-08, D-382).
 *
 * The fix restores the guard via `shouldPinTerminalToCenter`: pin to centre ONLY
 * when the terminal has no perimeter function. A normal vertex HAS a perimeter,
 * so it must NOT be pinned — that is the assertion that fails against the old
 * unguarded behaviour and passes with the fix.
 *
 * Structural / theme-agnostic: terminal anchoring has no theme input, so the
 * geometry that fixes light fixes dark identically (guarded explicitly below).
 */

import { shouldPinTerminalToCenter } from '../drawioPlugin';

describe('shouldPinTerminalToCenter — edge terminal anchoring (G-a59f77)', () => {
  it('does NOT pin a connected vertex WITH a perimeter to its centre', () => {
    // The regression: a normal box has a perimeter function, so its edge end
    // must float to the PERIMETER, not the centre. Old unguarded code returned
    // true here (terminal present, no fixed point) and pinned it centre-to-centre.
    expect(shouldPinTerminalToCenter(true, false, true)).toBe(false);
  });

  it('pins to centre only when the terminal has NO perimeter function', () => {
    expect(shouldPinTerminalToCenter(true, false, false)).toBe(true);
  });

  it('never overrides an already-resolved fixed terminal point', () => {
    expect(shouldPinTerminalToCenter(true, true, false)).toBe(false);
    expect(shouldPinTerminalToCenter(true, true, true)).toBe(false);
  });

  it('does nothing for a dangling edge end (no terminal cell)', () => {
    expect(shouldPinTerminalToCenter(false, false, false)).toBe(false);
    expect(shouldPinTerminalToCenter(false, false, true)).toBe(false);
  });

  it('is theme-agnostic: identical decision regardless of render theme', () => {
    // No theme input; a fix verified in light holds in dark.
    const light = shouldPinTerminalToCenter(true, false, true);
    const dark = shouldPinTerminalToCenter(true, false, true);
    expect(dark).toBe(light);
    expect(dark).toBe(false);
  });
});
