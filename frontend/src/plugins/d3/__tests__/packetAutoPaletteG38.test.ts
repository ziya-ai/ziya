/**
 * G-38 / D-041 — packet AUTO_PALETTE de-collision beyond 10 sections.
 *
 * Before this fix, `resolveColor` auto-assigned section colours with
 * `palette[autoIndex % palette.length]` against a fixed 10-entry palette, so
 * section 10 was an EXACT clone of section 0, section 11 of section 1, … In a
 * 15/30/50-section frame (packet-w2-02 / w2-04 / w2-13) adjacent recycled
 * sections became indistinguishable. The fix keeps the first 10 entries verbatim
 * (so ≤10-section packets are byte-identical) and synthesises a fresh, hue-
 * separated, THEME-RESOLVED colour past the base palette.
 *
 * This is a theme defect, so every generated colour is asserted in BOTH themes:
 * the previously-broken direction (recycled clone → now distinct + legible) is
 * paired with the guard that the ≤10 prefix and label contrast still hold on the
 * other surface. Contrast is measured with the same WCAG helper the renderer uses.
 *
 * Direction check: `autoPaletteColor` does not exist on the unpatched tree
 * (import is `undefined`), and pre-fix `resolveColor(undefined, dark, 10)` equals
 * `AUTO_PALETTE_DARK[0]` (an exact recycle) — the `.not.toEqual` assertions below
 * fail against unpatched source, so they certify the fix, not the bug.
 */
import {
  autoPaletteColor,
  resolveColor,
  AUTO_PALETTE_DARK,
  AUTO_PALETTE_LIGHT,
  ColorTriple,
} from '../../../utils/d3Plugins/packetPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

const HEX = /^#[0-9a-f]{6}$/i;

describe('G-38/D-041 packet auto-palette de-collision', () => {
  it('is byte-identical to the base palette for the first 10 sections (both themes)', () => {
    for (let i = 0; i < 10; i++) {
      expect(autoPaletteColor(i, true)).toEqual(AUTO_PALETTE_DARK[i]);
      expect(autoPaletteColor(i, false)).toEqual(AUTO_PALETTE_LIGHT[i]);
      // and resolveColor's auto path (undefined colour) matches the base too
      expect(resolveColor(undefined, true, i)).toEqual(AUTO_PALETTE_DARK[i]);
      expect(resolveColor(undefined, false, i)).toEqual(AUTO_PALETTE_LIGHT[i]);
    }
  });

  it('does NOT recycle section 10 onto section 0 in DARK (previously broken)', () => {
    // pre-fix: resolveColor(undefined, true, 10) === AUTO_PALETTE_DARK[0]
    expect(resolveColor(undefined, true, 10)).not.toEqual(AUTO_PALETTE_DARK[0]);
    expect(resolveColor(undefined, true, 11)).not.toEqual(AUTO_PALETTE_DARK[1]);
  });

  it('does NOT recycle section 10 onto section 0 in LIGHT (paired other-theme guard)', () => {
    expect(resolveColor(undefined, false, 10)).not.toEqual(AUTO_PALETTE_LIGHT[0]);
    expect(resolveColor(undefined, false, 11)).not.toEqual(AUTO_PALETTE_LIGHT[1]);
  });

  it('keeps consecutive generated sections distinct (well-separated hues)', () => {
    for (const dark of [true, false]) {
      for (let i = 10; i < 50; i++) {
        expect(autoPaletteColor(i, dark).bg).not.toEqual(autoPaletteColor(i + 1, dark).bg);
      }
    }
  });

  it('emits well-formed hex triples with a getOptimalTextColor-chosen label', () => {
    for (const dark of [true, false]) {
      for (let i = 10; i < 60; i++) {
        const c: ColorTriple = autoPaletteColor(i, dark);
        expect(c.bg).toMatch(HEX);
        expect(c.border).toMatch(HEX);
        expect(c.text).toMatch(HEX);
      }
    }
  });

  it('generated label text clears WCAG AA (>=4.5:1) against its fill in BOTH themes', () => {
    // DARK (previously the illegible surface for see-through/black-on-dark) and
    // LIGHT are both checked so a fix that satisfied one surface only would fail.
    for (const dark of [true, false]) {
      for (let i = 10; i < 60; i++) {
        const c = autoPaletteColor(i, dark);
        const ratio = calculateContrastRatio(c.text, c.bg);
        expect(ratio).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it('picks theme-appropriate fills — dark fills are deep, light fills are pale', () => {
    // A per-theme RESOLUTION, not a constant swap: the same index yields a
    // dark-surface fill in dark mode and a pale fill in light mode.
    const dark = autoPaletteColor(12, true).bg;
    const light = autoPaletteColor(12, false).bg;
    expect(dark).not.toEqual(light);
    // white text wins on the deep dark fill; black text wins on the pale light fill
    expect(autoPaletteColor(12, true).text.toLowerCase()).toBe('#ffffff');
    expect(autoPaletteColor(12, false).text.toLowerCase()).toBe('#000000');
  });
});
