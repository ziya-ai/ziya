/**
 * Gear geometry: a live gear must be visually separable from a stopped one.
 *
 * The defect this covers was not a logic error — the animate bit was
 * correct and correctly consumed.  Both branches simply rendered at the
 * same 12px, so the ONLY difference between "running" and "done" was
 * colour plus a 4s rotation of a near-radially-symmetric glyph, which is
 * imperceptible at that size in a narrow sidebar row.  A unit test on
 * ``RUN_STATUS_ANIMATES`` passes happily while the indicator is unreadable,
 * which is why the assertions here are about rendered SIZE, not about the
 * status map.
 *
 * Two halves are checked: the vocabulary states a real size difference,
 * and every consumer actually reads it (the seam — a constant nobody
 * imports is the failure mode this file exists to catch).
 */

import * as fs from 'fs';
import * as path from 'path';
import {
  GEAR_PX_LIVE, GEAR_PX_IDLE, GEAR_OPACITY_IDLE, GEAR_SPIN_SECONDS,
  gearFontSize, RUN_STATUS_ANIMATES,
} from '../runStatusVocabulary';

const root = path.resolve(__dirname, '../../..');
const read = (p: string) => fs.readFileSync(path.join(root, p), 'utf8');

const GEARS = () => read('components/TaskCard/RunStatusGears.tsx');
const SIDEBAR = () => read('components/MUIChatHistory.tsx');

describe('the vocabulary states a perceptible live/idle difference', () => {
  it('makes a live gear meaningfully larger, not a token 1px bigger', () => {
    // 1-2px at this scale is within antialiasing noise; the point is that
    // the difference survives peripheral vision.
    expect(GEAR_PX_LIVE - GEAR_PX_IDLE).toBeGreaterThanOrEqual(4);
  });

  it('dims stopped gears without hiding them', () => {
    // Terminal states (failed, held) have no other surface in the row, so
    // dimming must not approach invisibility.
    expect(GEAR_OPACITY_IDLE).toBeLessThan(1);
    expect(GEAR_OPACITY_IDLE).toBeGreaterThanOrEqual(0.5);
  });

  it('spins fast enough to notice but slower than a thinking spinner', () => {
    // SpinningSync (chat streaming) is 2s; the gear must stay slower or
    // equal-and-distinguished-by-glyph, and must not return to the 4s
    // period that read as static.
    expect(GEAR_SPIN_SECONDS).toBeLessThan(4);
    expect(GEAR_SPIN_SECONDS).toBeGreaterThan(0);
  });

  it('derives font size from the same animate bit that drives rotation', () => {
    expect(gearFontSize(true)).toBe(`${GEAR_PX_LIVE}px`);
    expect(gearFontSize(false)).toBe(`${GEAR_PX_IDLE}px`);
    // Positive control: the bit fed to gearFontSize is the one the status
    // map publishes, so a status flagged live really does get the big glyph.
    expect(gearFontSize(RUN_STATUS_ANIMATES.running)).toBe(`${GEAR_PX_LIVE}px`);
    expect(gearFontSize(RUN_STATUS_ANIMATES.done)).toBe(`${GEAR_PX_IDLE}px`);
  });
});

describe('both gear consumers read the geometry instead of a literal', () => {
  it('RunStatusGears sizes each branch through gearFontSize', () => {
    const src = GEARS();
    expect(src).toMatch(/<SpinningGear sx=\{\{ fontSize: gearFontSize\(true\)/);
    expect(src).toMatch(/<SettingsIcon sx=\{\{ fontSize: gearFontSize\(false\)/);
    // The hardcoded size is what made the two branches identical; a
    // regression would reintroduce it here.
    expect(src).not.toMatch(/fontSize: '12px'/);
  });

  it('RunStatusGears dims only the non-animating clusters', () => {
    expect(GEARS()).toMatch(/opacity: c\.animate \? 1 : GEAR_OPACITY_IDLE/);
  });

  it('RunStatusGears takes its rotation period from the vocabulary', () => {
    expect(GEARS()).toMatch(/gear-spin \$\{GEAR_SPIN_SECONDS\}s linear infinite/);
  });

  it('the sidebar\u2019s optimistic gear matches the per-status gears', () => {
    // "Task starting…" renders in the same row position as a running
    // cluster and hands over to it once a binding exists; a size or speed
    // mismatch reads as the indicator changing meaning mid-launch.
    const src = SIDEBAR();
    expect(src).toMatch(
      /import \{[^}]*gearFontSize[^}]*\} from '\.\/TaskCard\/runStatusVocabulary'/,
    );
    expect(src).toMatch(/<SpinningGear sx=\{\{ fontSize: gearFontSize\(true\)/);
    expect(src).toMatch(/gear-spin \$\{GEAR_SPIN_SECONDS\}s linear infinite/);
  });
});
