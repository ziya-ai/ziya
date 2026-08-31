/**
 * G-10 — chord plugin: theme-aware boundary/label colours + N-aware geometry
 * (shared file: chordPlugin.ts).
 *
 * Defects covered:
 *   D-051 (theme, both)  the arc/ribbon boundary stroke default was the
 *         BACKGROUND colour (#ffffff light / #0d0d1a dark), so it never
 *         delimited an arc; now a neutral stroke that clears the 3:1 graphical
 *         floor against the effective canvas (#555555 light / #cfcfcf dark).
 *   D-053 (theme, dark)  the label colour default was derived from isDarkMode,
 *         not the RESOLVED background, so a light style.background under dark
 *         theme flipped labels to #e0e0e0 and erased them; now resolved from the
 *         effective canvas luminance.
 *   D-056 (structural)   padAngle was a fixed 0.05 per group, summing past the
 *         2*pi circle at N>=126 and starving every arc to 0px; now capped so
 *         total padding stays ~20% of the circle.
 *   D-057 (structural)   the 0.5px ribbon stroke dominated sub-pixel ribbons at
 *         high edge count (erased them in light / solid disc in dark); now the
 *         ribbon stroke drops to 0 past the sub-pixel onset.
 *
 * Direction: every THEME assertion (D-051, D-053) pairs the newly-fixed theme
 * against the theme that was already correct AND asserts the fixed value is NOT
 * the old bg-derived constant that measured below floor. Structural assertions
 * (D-056, D-057) check a value the pre-fix constant could not produce.
 */
import { contrastRatio } from '../chartTheme';
import {
  resolveChordArcStroke,
  resolveChordLabelColor,
  chordPadAngle,
  chordRibbonStrokeWidth,
} from '../chordPlugin';

const LIGHT_BG = '#ffffff';
const DARK_BG = '#1a1a2e';
const GRAPHICAL_FLOOR = 3;   // WCAG non-text (arc/stroke boundary)
const TEXT_FLOOR = 4.5;      // WCAG normal text (labels)
const TWO_PI = 2 * Math.PI;

describe('D-051 arc/ribbon boundary stroke — theme-resolved, clears the graphical floor in BOTH themes', () => {
  it('light default is a contrasting stroke, NOT the old #ffffff (== bg, 1.00:1)', () => {
    const s = resolveChordArcStroke({}, LIGHT_BG);
    expect(s).not.toBe('#ffffff');
    expect(contrastRatio(s, LIGHT_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('dark default is a contrasting stroke, NOT the old #0d0d1a (== bg, 1.13:1)', () => {
    const s = resolveChordArcStroke({}, DARK_BG);
    expect(s).not.toBe('#0d0d1a');
    expect(contrastRatio(s, DARK_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('both-theme parity: the light-fixed stroke is readable in light AND the dark stroke stays readable in dark', () => {
    expect(contrastRatio(resolveChordArcStroke({}, LIGHT_BG), LIGHT_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    expect(contrastRatio(resolveChordArcStroke({}, DARK_BG), DARK_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('an explicit caller arcStroke is honoured verbatim (w1-13 #222222, w4-11 rgba)', () => {
    expect(resolveChordArcStroke({ arcStroke: '#222222' }, LIGHT_BG)).toBe('#222222');
    expect(resolveChordArcStroke({ arcStroke: 'rgba(0,0,0,0.6)' }, DARK_BG)).toBe('rgba(0,0,0,0.6)');
  });
});

describe('D-053 label colour — resolved from the effective background, not isDarkMode', () => {
  it('a light style.background under dark theme yields a DARK label (readable), not the old #e0e0e0 (1.23:1)', () => {
    // Under dark theme the OLD code returned #e0e0e0 regardless of the pinned
    // light panel. The effective canvas here is the pinned '#f7f7f7'.
    const label = resolveChordLabelColor({}, '#f7f7f7');
    expect(label).not.toBe('#e0e0e0');
    expect(contrastRatio(label, '#f7f7f7')).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('both-theme parity: dark canvas still gets the light label at >=4.5:1', () => {
    const darkLabel = resolveChordLabelColor({}, DARK_BG);
    expect(darkLabel).toBe('#e0e0e0');
    expect(contrastRatio(darkLabel, DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    // …and a plain light canvas gets a dark label at >=4.5:1.
    const lightLabel = resolveChordLabelColor({}, LIGHT_BG);
    expect(contrastRatio(lightLabel, LIGHT_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('cross-pairings confirm per-theme resolution is required (why a constant swap is wrong)', () => {
    // The dark label on a light panel and the light label on a dark panel both
    // FAIL — which is exactly why the default is resolved per-canvas.
    expect(contrastRatio('#e0e0e0', '#f7f7f7')).toBeLessThan(TEXT_FLOOR);
    expect(contrastRatio('#333333', DARK_BG)).toBeLessThan(TEXT_FLOOR);
  });

  it('a caller labelColor that is already readable on its canvas is honoured verbatim', () => {
    // D-053 only resolves the DEFAULT; a caller colour that already clears the
    // text floor is preserved. Reconciliation of a SUB-floor caller label is
    // now handled by D-054 (see chordG31.test.ts).
    expect(resolveChordLabelColor({ labelColor: '#e0e0e0' }, DARK_BG)).toBe('#e0e0e0'); // 12.92:1
    expect(resolveChordLabelColor({ labelColor: '#333333' }, LIGHT_BG)).toBe('#333333'); // 12.63:1
  });
});

describe('D-056 padAngle — N-aware, keeps arcs from starving to zero width', () => {
  it('the OLD fixed 0.05 would over-fill the circle at N>=126 (regression baseline)', () => {
    expect(0.05 * 126).toBeGreaterThan(TWO_PI); // 6.30 > 6.283 -> zero arc width
  });

  it('total inter-arc padding never exceeds ~20% of the circle (arcs keep >=80%)', () => {
    for (const n of [126, 150, 300, 500]) {
      const total = chordPadAngle(n) * n;
      expect(total).toBeLessThanOrEqual(0.2 * TWO_PI + 1e-9);
      expect(total).toBeLessThan(TWO_PI); // arcs get a non-zero remainder
    }
  });

  it('small/normal diagrams (N<=25) are unchanged at 0.05', () => {
    expect(chordPadAngle(2)).toBe(0.05);
    expect(chordPadAngle(8)).toBe(0.05);
    expect(chordPadAngle(25)).toBe(0.05);
  });

  it('padAngle shrinks monotonically for large N and guards N=0', () => {
    expect(chordPadAngle(126)).toBeLessThan(0.05);
    expect(chordPadAngle(300)).toBeLessThan(chordPadAngle(126));
    expect(chordPadAngle(0)).toBe(0.05);
  });
});

describe('D-057 ribbon stroke width — dropped past the sub-pixel onset', () => {
  it('low edge count keeps the thin delimiting stroke (w2-01, N=40)', () => {
    expect(chordRibbonStrokeWidth(40)).toBe(0.5);
    expect(chordRibbonStrokeWidth(50)).toBe(0.5);
  });

  it('high edge count drops the stroke to 0 so it cannot erase/smear sub-pixel ribbons (w2-02 N=80, w2-08 N=2450)', () => {
    // Pre-fix this was an unconditional 0.5 for every edge count.
    expect(chordRibbonStrokeWidth(80)).toBe(0);
    expect(chordRibbonStrokeWidth(2450)).toBe(0);
  });
});
