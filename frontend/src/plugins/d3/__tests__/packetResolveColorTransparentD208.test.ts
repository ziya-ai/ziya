/**
 * D-208 (G-135467): packet spec `packet-w4-11` has a section with an explicit
 * ColorTriple `{ bg: 'transparent', border: '#cccccc' }` and no `text`. The old
 * resolveColor() computed the label as getOptimalTextColor(color.bg) with
 * color.bg === 'transparent'; the shared named-colour table maps transparent to
 * white (#ffffff), so the label committed to black. Correct on the light canvas
 * (#ffffff, 21:1) but invisible on the dark canvas (#1e1e1e, black-on-dark =
 * 1.26:1) — the two sibling sections render fine, so it shipped unnoticed.
 *
 * The fix resolves the label backdrop against the ACTUAL themed canvas via
 * effectiveCellBackdrop() / textColorForFill() before getOptimalTextColor().
 * This test asserts BOTH themes and pins the direction: with the pre-fix logic
 * the DARK assertion (label !== black / contrast >= 4.5) fails.
 */
import {
  resolveColor,
  effectiveCellBackdrop,
  PACKET_PAGE_BG_DARK,
  PACKET_PAGE_BG_LIGHT,
} from '../../../utils/d3Plugins/packetPlugin';
import { getOptimalTextColor } from '../../../utils/colorUtils';

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

// The exact shape from packet-w4-11's first section.
const TRANSPARENT_TRIPLE = { bg: 'transparent', border: '#cccccc' } as const;

describe('resolveColor — transparent explicit triple stays readable in BOTH themes (D-208)', () => {
  it('DARK: transparent fill label resolves against #1e1e1e and is readable', () => {
    const c = resolveColor(TRANSPARENT_TRIPLE, /* isDarkMode */ true, 0);
    // Fill stays see-through so the page shows through.
    expect(c.bg).toBe('transparent');
    // Label is chosen for the dark canvas, not the (white) transparent assumption.
    expect(c.text.toLowerCase()).toBe('#ffffff');
    expect(contrast(c.text, PACKET_PAGE_BG_DARK)).toBeGreaterThanOrEqual(4.5);
  });

  it('LIGHT: transparent fill label resolves against #ffffff and stays readable', () => {
    const c = resolveColor(TRANSPARENT_TRIPLE, /* isDarkMode */ false, 0);
    expect(c.bg).toBe('transparent');
    expect(c.text.toLowerCase()).toBe('#000000');
    expect(contrast(c.text, PACKET_PAGE_BG_LIGHT)).toBeGreaterThanOrEqual(4.5);
  });

  it('DIRECTION: the pre-fix path (label from the transparent keyword) is invisible in DARK', () => {
    // Old code: getOptimalTextColor(color.bg) with color.bg === 'transparent'.
    const oldLabel = getOptimalTextColor('transparent');
    expect(oldLabel.toLowerCase()).toBe('#000000');
    expect(contrast(oldLabel, PACKET_PAGE_BG_DARK)).toBeLessThan(1.5); // ~1.26:1
  });

  it('a bare "transparent" string color is see-through with a theme-resolved label', () => {
    const dark = resolveColor('transparent', true, 0);
    expect(dark.bg).toBe('transparent');
    expect(contrast(dark.text, PACKET_PAGE_BG_DARK)).toBeGreaterThanOrEqual(4.5);
    const light = resolveColor('transparent', false, 0);
    expect(contrast(light.text, PACKET_PAGE_BG_LIGHT)).toBeGreaterThanOrEqual(4.5);
  });

  it('an opaque hex fill is unaffected (effectiveCellBackdrop is a no-op)', () => {
    expect(effectiveCellBackdrop('#3366cc', PACKET_PAGE_BG_DARK)).toBe('#3366cc');
    const c = resolveColor('#B2E0F0', true, 0);
    expect(c.bg).toBe('#B2E0F0');
  });
});
