/**
 * G-19 / D-021: the shared SVG contrast-remediation pass
 * (`colorUtils.enhanceSVGVisibility` -> `calculateContrastRatio` +
 * `getOptimalTextColor`) understood only hex + `rgb()` colours. Mermaid emits
 * its THEME palette (journey bands, gitGraph branch strokes, mindmap links, bar
 * fills, quadrant fills) as `hsl(h, s%, l%)` at render time, so an `hsl()`
 * backdrop failed to parse: `calculateContrastRatio` logged `COLOR-PARSE-FAIL`
 * and collapsed the ratio to a degenerate `1`, and `getOptimalTextColor` fell
 * through to its `#ffffff` default. The result was a near-white label on a
 * near-white theme fill in LIGHT mode, and unadapted foregrounds in DARK mode.
 *
 * The fix adds `hslStringToRgb` and wires `hsl()`/`hsla()` parsing into BOTH
 * `getOptimalTextColor` and the `parseColor` inside `calculateContrastRatio`,
 * right next to the existing `rgb()` handling. A malformed value (e.g. a `NaN`
 * component from an upstream mermaid theme bug) still parses to `null` so the
 * pass declines rather than fabricating a colour.
 *
 * These tests import the REAL module (no re-implementation) and pin BOTH
 * directions and BOTH themes:
 *   - NON-VACUOUS: pre-fix `hslStringToRgb` does not exist (undefined -> TypeError),
 *     `getOptimalTextColor('hsl(60,80%,85%)')` returned '#ffffff' (not '#000000'),
 *     and `calculateContrastRatio('#000000','hsl(60,80%,85%)')` returned exactly 1.
 *     Every flip/`> 4.5` assertion below fails on the old code.
 *   - LIGHT (previously broken): a pale-yellow hsl quadrant fill now yields BLACK
 *     optimal text (18.97:1) instead of the white default (1.11:1).
 *   - DARK (still correct): a dark-blue hsl node fill keeps WHITE optimal text
 *     (10.69:1), and the ratio is now computed for real (> 4.5) instead of 1.
 */
import {
  calculateContrastRatio,
  getOptimalTextColor,
  hslStringToRgb,
} from '../../../utils/colorUtils';

describe('G-19 / D-021: hsl() colour parsing in the shared contrast pass', () => {
  const PALE_YELLOW = 'hsl(60, 80%, 85%)'; // ~ rgb(247,247,186) — mermaid light quadrant/band
  const DARK_BLUE = 'hsl(210, 50%, 25%)'; // ~ rgb(32,64,96)   — mermaid dark node fill

  it('parses hsl() to the expected RGB triple', () => {
    expect(hslStringToRgb(PALE_YELLOW)).toEqual({ r: 247, g: 247, b: 186 });
    expect(hslStringToRgb(DARK_BLUE)).toEqual({ r: 32, g: 64, b: 96 });
    // space-separated CSS Level-4 syntax also parses
    expect(hslStringToRgb('hsl(210 50% 25%)')).toEqual({ r: 32, g: 64, b: 96 });
  });

  // ---- LIGHT theme: the previously-broken direction ----
  it('LIGHT: picks BLACK text on a pale-yellow hsl fill (was white default)', () => {
    // Pre-fix: getOptimalTextColor('hsl(...)') -> '#ffffff' (unparseable default).
    expect(getOptimalTextColor(PALE_YELLOW)).toBe('#000000');
    // The correct choice is legible; the old white default was 1.11:1 (invisible).
    expect(calculateContrastRatio('#000000', PALE_YELLOW)).toBeGreaterThan(4.5);
    expect(calculateContrastRatio('#ffffff', PALE_YELLOW)).toBeLessThan(1.5);
  });

  // ---- DARK theme: the paired "still correct" direction ----
  it('DARK: keeps WHITE text on a dark-blue hsl node fill, with a REAL ratio', () => {
    expect(getOptimalTextColor(DARK_BLUE)).toBe('#ffffff');
    // Pre-fix this returned exactly 1 (parse fail); now it is the true ~10.69:1.
    expect(calculateContrastRatio('#eceff4', DARK_BLUE)).toBeGreaterThan(4.5);
    // and black-on-dark-blue would (correctly) be poor
    expect(calculateContrastRatio('#000000', DARK_BLUE)).toBeLessThan(2.5);
  });

  it('computes a symmetric, real ratio for two hsl() colours', () => {
    const r = calculateContrastRatio(DARK_BLUE, PALE_YELLOW);
    expect(r).toBeGreaterThan(4.5); // dark-blue vs pale-yellow are clearly separable
  });

  // ---- residual: a malformed hsl (upstream NaN lightness) is declined, not faked ----
  it('rejects a malformed hsl() (NaN component) rather than fabricating a colour', () => {
    expect(hslStringToRgb('hsl(240, 100%, NaN%)')).toBeNull();
    // parse-fail still yields the degenerate 1 (documents the upstream-NaN residual)
    expect(calculateContrastRatio('#ffffff', 'hsl(240, 100%, NaN%)')).toBe(1);
  });

  // ---- regression: hex + rgb() paths untouched ----
  it('does not disturb the existing hex and rgb() paths', () => {
    expect(calculateContrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 1);
    expect(getOptimalTextColor('rgb(20, 20, 20)')).toBe('#ffffff');
    expect(getOptimalTextColor('#fafad2')).toBe('#000000'); // lightgoldenrodyellow hex
  });
});
