/**
 * D-183 (G-PACKET-THEME) — packet AUTO_PALETTE same-hue collision.
 *
 * The 10-entry auto palettes are rotated by `autoIndex % len`, so a many-section
 * frame (packet-w2-02 / w2-04 / w2-13) lands multiple sections on the list. Two
 * slots per palette were same-hue near-duplicates of an EARLIER slot, so the
 * recycled bands were indistinguishable:
 *   - LIGHT  idx 5 (#D1F2EB teal-green) vs idx 2 (#D5F5E3 green): 21° hue gap
 *   - DARK   idx 7 (#1B4F72 blue)       vs idx 0 (#1A5276 blue):  ~1° hue gap
 *   - DARK   idx 9 (#196F3D green)      vs idx 2 (#1E8449 green): ~0° hue gap
 * (LIGHT idx 9 #A9DFBF was a THIRD green, ~2° from idx 2.)
 *
 * WCAG contrast is a poor distinctness metric for equal-lightness fills (blue vs
 * yellow reads ~1.14 yet is obviously distinct), so the fix restores HUE
 * separation on the later duplicate slots. This is a BOTH-THEME defect
 * (themes_affected: light, dark), so the light AND dark palettes are each
 * asserted. Direction: on the UNPATCHED tree those slots still hold the old
 * near-duplicate hexes (hue gap < 40°), so the `toBeGreaterThan(40)` assertions
 * fail — they certify the fix, not the bug. Label text is verified >=4.5:1 on
 * every changed fill in its own theme.
 */
import {
  AUTO_PALETTE_LIGHT,
  AUTO_PALETTE_DARK,
} from '../../../utils/d3Plugins/packetPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}
/** Hue in degrees [0,360). */
function hue(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => c / 255) as [number, number, number];
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  if (d === 0) return 0;
  let h: number;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  return h < 0 ? h + 360 : h;
}
function hueGap(a: string, b: string): number {
  const d = Math.abs(hue(a) - hue(b));
  return Math.min(d, 360 - d);
}

describe('D-183 packet auto-palette same-hue de-collision', () => {
  it('LIGHT: the recoloured slot 5 is now hue-distinct from the green at slot 2 (was 21°)', () => {
    // Pre-fix: AUTO_PALETTE_LIGHT[5] === '#D1F2EB', a teal-green 21° from slot 2's
    // green — this assertion fails on unpatched source.
    expect(hueGap(AUTO_PALETTE_LIGHT[5].bg, AUTO_PALETTE_LIGHT[2].bg)).toBeGreaterThan(40);
    // slot 9 was a third green; now periwinkle, far from every green.
    expect(hueGap(AUTO_PALETTE_LIGHT[9].bg, AUTO_PALETTE_LIGHT[2].bg)).toBeGreaterThan(40);
  });

  it('DARK: the recoloured slots 7 and 9 are now hue-distinct from their old twins', () => {
    // Pre-fix: DARK[7] '#1B4F72' ~1° from DARK[0] blue; DARK[9] '#196F3D' ~0° from
    // DARK[2] green — both assertions fail on unpatched source.
    expect(hueGap(AUTO_PALETTE_DARK[7].bg, AUTO_PALETTE_DARK[0].bg)).toBeGreaterThan(40);
    expect(hueGap(AUTO_PALETTE_DARK[9].bg, AUTO_PALETTE_DARK[2].bg)).toBeGreaterThan(40);
  });

  it('every changed fill keeps its label text >=4.5:1 in its own theme', () => {
    for (const i of [5, 9]) {
      const c = AUTO_PALETTE_LIGHT[i];
      expect(calculateContrastRatio(c.text, c.bg)).toBeGreaterThanOrEqual(4.5);
    }
    for (const i of [7, 9]) {
      const c = AUTO_PALETTE_DARK[i];
      expect(calculateContrastRatio(c.text, c.bg)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('the untouched slots are preserved (<=5-section light / <=7-section dark unchanged)', () => {
    // Guards that the fix is minimal: only the later duplicate slots moved.
    expect(AUTO_PALETTE_LIGHT[0].bg).toBe('#B2E0F0');
    expect(AUTO_PALETTE_LIGHT[2].bg).toBe('#D5F5E3');
    expect(AUTO_PALETTE_LIGHT[4].bg).toBe('#E8DAEF');
    expect(AUTO_PALETTE_DARK[0].bg).toBe('#1A5276');
    expect(AUTO_PALETTE_DARK[2].bg).toBe('#1E8449');
    expect(AUTO_PALETTE_DARK[6].bg).toBe('#935116');
  });

  it('no LIGHT slot collides with the green pair (slot 2) any more', () => {
    // The whole green cluster (was 2/5/9) is now a single green at slot 2.
    for (let i = 0; i < AUTO_PALETTE_LIGHT.length; i++) {
      if (i === 2) continue;
      const near = hueGap(AUTO_PALETTE_LIGHT[i].bg, AUTO_PALETTE_LIGHT[2].bg) < 15;
      const sameShade = calculateContrastRatio(AUTO_PALETTE_LIGHT[i].bg, AUTO_PALETTE_LIGHT[2].bg) < 1.15;
      expect(near && sameShade).toBe(false);
    }
  });
});
