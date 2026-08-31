/**
 * D-227 (G-81): a packet field/section fill of `transparent`/`none` lets the
 * themed canvas show through, but the theme-blind colour helpers assume a white
 * page (namedColorToHex: transparent -> #ffffff), so the label commits to black.
 * Correct on the light canvas (#ffffff), invisible on the dark canvas (#1e1e1e,
 * black-on-dark = 1.26:1).
 *
 * The fix resolves the label backdrop against the ACTUAL themed canvas via
 * effectiveCellBackdrop() before getOptimalTextColor(). This test asserts BOTH
 * themes: the broken (dark) theme is now correct AND the other (light) theme
 * still is. It also pins the direction — the OLD path (resolving the label
 * directly against the transparent fill) produces black in dark and thus fails
 * against unpatched code.
 */
import { effectiveCellBackdrop } from '../packetPlugin';
import { getOptimalTextColor } from '../../../utils/colorUtils';

// The canvas colours the plugin derives from isDarkMode (packetPlugin render()).
const DARK_CANVAS = '#1e1e1e';
const LIGHT_CANVAS = '#ffffff';

// WCAG relative-contrast, same formula the sweep uses.
function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const l1 = luminance(a);
  const l2 = luminance(b);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

describe('effectiveCellBackdrop — transparent fill resolves to the themed canvas (D-227)', () => {
  it('transparent fill -> dark canvas backdrop, giving a readable (white) label in DARK', () => {
    const backdrop = effectiveCellBackdrop('transparent', DARK_CANVAS);
    expect(backdrop).toBe(DARK_CANVAS);
    const label = getOptimalTextColor(backdrop);
    expect(label).toBe('#ffffff');
    // Fixed: white on #1e1e1e is far above the 4.5 text floor.
    expect(contrast(label, DARK_CANVAS)).toBeGreaterThanOrEqual(4.5);
  });

  it('transparent fill -> light canvas backdrop still gives a readable (black) label in LIGHT', () => {
    const backdrop = effectiveCellBackdrop('transparent', LIGHT_CANVAS);
    expect(backdrop).toBe(LIGHT_CANVAS);
    const label = getOptimalTextColor(backdrop);
    expect(label).toBe('#000000');
    expect(contrast(label, LIGHT_CANVAS)).toBeGreaterThanOrEqual(4.5);
  });

  it('DIRECTION: the pre-fix path (label resolved against the transparent fill) is broken in DARK', () => {
    // Unpatched code called getOptimalTextColor(c.bg) with c.bg === 'transparent'.
    // transparent is treated as white -> black label -> black on #1e1e1e.
    const oldLabel = getOptimalTextColor('transparent');
    expect(oldLabel).toBe('#000000');
    expect(contrast(oldLabel, DARK_CANVAS)).toBeLessThan(1.5); // ~1.26:1, invisible
  });

  it('none / empty / zero-alpha rgba are all treated as see-through', () => {
    expect(effectiveCellBackdrop('none', DARK_CANVAS)).toBe(DARK_CANVAS);
    expect(effectiveCellBackdrop('', DARK_CANVAS)).toBe(DARK_CANVAS);
    expect(effectiveCellBackdrop('  Transparent  ', DARK_CANVAS)).toBe(DARK_CANVAS);
    expect(effectiveCellBackdrop('rgba(0,0,0,0)', DARK_CANVAS)).toBe(DARK_CANVAS);
    expect(effectiveCellBackdrop('rgba(255, 255, 255, 0.0)', LIGHT_CANVAS)).toBe(LIGHT_CANVAS);
  });

  it('an opaque fill is returned unchanged (non-transparent output byte-identical)', () => {
    expect(effectiveCellBackdrop('#3366cc', DARK_CANVAS)).toBe('#3366cc');
    expect(effectiveCellBackdrop('#ffffff', DARK_CANVAS)).toBe('#ffffff');
    expect(effectiveCellBackdrop('rgba(0,0,0,0.8)', DARK_CANVAS)).toBe('rgba(0,0,0,0.8)');
    expect(effectiveCellBackdrop(undefined, LIGHT_CANVAS)).toBe(LIGHT_CANVAS);
  });
});
