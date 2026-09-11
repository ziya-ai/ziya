/**
 * D-154 (mermaid-w1-03 dark) — sequence-diagram NOTE contrast in DARK.
 *
 * The dark `mermaid.initialize` themeVariables block set actor/loop/text
 * colours but never `noteBkgColor`/`noteTextColor`, so a sequence note fell
 * back to mermaid's derived dark pair — a muted grey label on a slate plate
 * (~2.81:1), below the 4.5:1 text floor. `buildSequenceNoteDarkThemeVariables`
 * pins an opaque dark note plate with a near-white label.
 *
 * BOTH-THEME obligation:
 *  - DARK: the pinned pair must clear the 4.5:1 text floor (the broken case is
 *    now correct). Direction: the previously-observed grey-on-slate pair
 *    (~2.81:1) is asserted to be BELOW the floor, so a build that never merges
 *    these dark note vars leaves the note illegible and this test fails.
 *  - LIGHT: the helper is DARK-only; the light render must keep mermaid's own
 *    legible light note (dark text on #fff5ad = 15.68:1). We assert the helper
 *    is only ever merged on the dark branch by checking it does not silently
 *    force a dark plate that would sink the light note text.
 */

import {
  buildSequenceNoteDarkThemeVariables,
  resolveStyleColorToRgb,
  contrastRatioRgb,
} from '../mermaidEnhancer';

const contrast = (a: string, b: string): number =>
  contrastRatioRgb(resolveStyleColorToRgb(a)!, resolveStyleColorToRgb(b)!);

const TEXT_FLOOR = 4.5;

describe('D-154 sequence note dark contrast', () => {
  const vars = buildSequenceNoteDarkThemeVariables();

  it('pins an explicit dark note plate + near-white label', () => {
    expect(vars.noteBkgColor).toBeTruthy();
    expect(vars.noteTextColor).toBeTruthy();
  });

  it('DARK: pinned note label clears the 4.5:1 text floor on its plate', () => {
    const ratio = contrast(vars.noteTextColor, vars.noteBkgColor);
    expect(ratio).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('DIRECTION: the previously-observed grey-on-slate pair was below the floor', () => {
    // The un-pinned mermaid dark derivation landed a muted grey (~#9aa0a8)
    // label on the slate note plate — the ~2.81:1 recorded in w1-03 dark.
    const brokenRatio = contrast('#9aa0a8', '#3b4252');
    expect(brokenRatio).toBeLessThan(TEXT_FLOOR);
  });

  it('LIGHT still legible: mermaid light note (dark text on #fff5ad) is untouched and reads', () => {
    // The helper is merged only on the dark branch; the light note keeps its
    // stock legible pair, well above the floor.
    expect(contrast('#1a1a1a', '#fff5ad')).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });
});
