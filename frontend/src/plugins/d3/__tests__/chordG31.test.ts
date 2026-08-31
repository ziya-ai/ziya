/**
 * G-31 — chord plugin: categorical fill + label colour resolution
 * (shared file: chordPlugin.ts).
 *
 * Defects covered (all in chordPlugin.ts, all theme/recovery colour handling):
 *
 *   D-052 (theme, light)  DEFAULT_PALETTE is dark-tuned: 6/10 entries are below
 *         the 3:1 graphical floor on white (#edc948 1.61, #ff9da7 1.98,
 *         #bab0ac 2.12, #76b7b2 2.29, #f28e2b 2.42, #59a14f 3.16) while all 10
 *         clear it on the dark canvas. Arc fills are now contrast-reconciled to
 *         the effective canvas, so light-theme arcs are nudged readable and dark
 *         output is unchanged.
 *   D-054 (theme, dark)   a caller style.labelColor was passed through verbatim
 *         and never contrast-checked, so a light-tuned label under a dark canvas
 *         (#5a5a5a 2.47, dimgray #696969 3.11) ghosted out. Now reconciled to
 *         the 4.5 text floor against the effective canvas.
 *   D-069 (recovery)      node/matrix color:'transparent'/'none'/'' was honoured
 *         literally, filling the arc with the background and erasing it plus
 *         every ribbon targeting it. Now treated as absent -> palette.
 *   D-070 (recovery)      invalid colour tokens (var(--x), $blue-500,
 *         theme.accent, invalid names like 'primary') fell back to the SVG
 *         initial value BLACK. Now treated as absent -> palette (fills) or the
 *         theme default (labels), then contrast-reconciled.
 *
 * Direction: the helpers `resolveChordFill` / `normalizeChordColorToHex` and the
 * extended `resolveChordLabelColor` did not exist / did not reconcile before
 * this change; each assertion additionally pins the fixed value AGAINST the old
 * broken value (verbatim 'transparent', SVG black, the sub-floor raw palette
 * entry, the verbatim low-contrast caller label) so it cannot pass against the
 * pre-fix code. Every THEME assertion (D-052, D-054) checks BOTH themes: the
 * repaired theme is now correct and the theme that was already correct still is.
 */
import { contrastRatio } from '../chartTheme';
import {
  resolveChordFill,
  resolveChordLabelColor,
  normalizeChordColorToHex,
} from '../chordPlugin';

const LIGHT_BG = '#ffffff';
const DARK_BG = '#1a1a2e';        // chord's dark default (style.background || '#1a1a2e')
const GRAPHICAL_FLOOR = 3;        // WCAG non-text (arc fill vs canvas)
const TEXT_FLOOR = 4.5;           // WCAG normal text (labels)

// The dark-tuned default palette, mirrored from chordPlugin.ts.
const PALETTE = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];
// Palette entries that measure BELOW 3:1 on white (the D-052 failure set).
const SUB_FLOOR_ON_WHITE = ['#f28e2b', '#76b7b2', '#edc948', '#ff9da7', '#bab0ac'];

describe('D-052 default palette — arc fills reconciled to the graphical floor in BOTH themes', () => {
  it('every palette index clears 3:1 on the LIGHT canvas after resolution (was 6/10 below floor)', () => {
    for (let i = 0; i < PALETTE.length; i++) {
      const fill = resolveChordFill(undefined, i, LIGHT_BG);
      expect(contrastRatio(fill, LIGHT_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    }
  });

  it('every palette index still clears 3:1 on the DARK canvas (no regression)', () => {
    for (let i = 0; i < PALETTE.length; i++) {
      const fill = resolveChordFill(undefined, i, DARK_BG);
      expect(contrastRatio(fill, DARK_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    }
  });

  it('dark canvas returns the raw palette entry UNCHANGED (all already clear the floor there)', () => {
    for (let i = 0; i < PALETTE.length; i++) {
      expect(resolveChordFill(undefined, i, DARK_BG)).toBe(PALETTE[i]);
    }
  });

  it('light canvas does NOT return the sub-floor raw palette entry verbatim (it is nudged)', () => {
    // index 5 = #edc948, raw 1.61:1 on white — must be changed, not passed through.
    const fill5 = resolveChordFill(undefined, 5, LIGHT_BG);
    expect(fill5).not.toBe('#edc948');
    expect(contrastRatio(fill5, LIGHT_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    // Every known sub-floor-on-white entry, wherever it lands, is nudged readable.
    SUB_FLOOR_ON_WHITE.forEach((raw) => {
      const idx = PALETTE.indexOf(raw);
      expect(resolveChordFill(undefined, idx, LIGHT_BG)).not.toBe(raw);
    });
  });

  it('index wraps modulo the palette length (recycling groups still resolve)', () => {
    // index 15 -> palette[5] -> reconciled; still readable in both themes.
    expect(contrastRatio(resolveChordFill(undefined, 15, LIGHT_BG), LIGHT_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    expect(contrastRatio(resolveChordFill(undefined, 15, DARK_BG), DARK_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('a valid, already-readable caller hex is preserved verbatim (identity)', () => {
    expect(resolveChordFill('#4e79a7', 0, LIGHT_BG)).toBe('#4e79a7'); // 4.55:1
    expect(resolveChordFill('#e15759', 2, LIGHT_BG)).toBe('#e15759'); // 3.68:1
  });
});

describe('D-069 transparent fill — treated as absent, arc no longer erased', () => {
  it("'transparent' resolves to a readable palette fill (NOT the literal 'transparent') in BOTH themes", () => {
    for (const bg of [LIGHT_BG, DARK_BG]) {
      const fill = resolveChordFill('transparent', 1, bg);
      expect(fill).not.toBe('transparent');
      expect(fill.toLowerCase()).not.toBe(bg.toLowerCase()); // no longer == background
      expect(contrastRatio(fill, bg)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    }
  });

  it("'none', empty string and a zero-alpha rgba() are all treated as absent", () => {
    for (const raw of ['none', '', 'rgba(0,0,0,0)']) {
      const fill = resolveChordFill(raw, 3, LIGHT_BG);
      expect(contrastRatio(fill, LIGHT_BG)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
      expect(['none', '', 'rgba(0,0,0,0)']).not.toContain(fill);
    }
  });
});

describe('D-070 invalid colour token — no longer falls back to SVG black', () => {
  it('design-system tokens resolve to a readable palette fill, NOT #000000, in BOTH themes', () => {
    for (const token of ['var(--chart-1)', '$blue-500', 'theme.accent', 'primary']) {
      for (const bg of [LIGHT_BG, DARK_BG]) {
        const fill = resolveChordFill(token, 4, bg);
        expect(fill).not.toBe('#000000');
        expect(fill).not.toBe(token);
        expect(contrastRatio(fill, bg)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
      }
    }
  });

  it('normalizeChordColorToHex maps valid names to hex and rejects tokens/invalid names', () => {
    expect(normalizeChordColorToHex('dimgray')).toBe('#696969');
    expect(normalizeChordColorToHex('rebeccapurple')).toBe('#663399');
    expect(normalizeChordColorToHex('DarkOrange')).toBe('#ff8c00'); // case-insensitive
    expect(normalizeChordColorToHex('#abc')).toBe('#abc');           // hex preserved
    expect(normalizeChordColorToHex('primary')).toBeNull();          // not a CSS colour
    expect(normalizeChordColorToHex('var(--x)')).toBeNull();
    expect(normalizeChordColorToHex('transparent')).toBeNull();
    expect(normalizeChordColorToHex('theme.accent')).toBeNull();
  });
});

describe('D-054 / D-070 caller label colour — reconciled to the text floor, both themes', () => {
  it('a light-tuned hex label under a DARK canvas is nudged to the 4.5 text floor (was verbatim ghost text)', () => {
    // #5a5a5a on #1a1a2e = 2.47:1 verbatim (D-054 w1-11).
    const label = resolveChordLabelColor({ labelColor: '#5a5a5a' }, DARK_BG);
    expect(label).not.toBe('#5a5a5a');
    expect(contrastRatio(label, DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('a named light-tuned label (dimgray) under a DARK canvas is reconciled (was 3.11:1 verbatim, w4-12)', () => {
    const label = resolveChordLabelColor({ labelColor: 'dimgray' }, DARK_BG);
    expect(contrastRatio(label, DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('an invalid label token falls back to the theme default (was SVG black #000000 on #1a1a2e = 1.23:1)', () => {
    const label = resolveChordLabelColor({ labelColor: 'token.text.primary' }, DARK_BG);
    expect(label).toBe('#e0e0e0');
    expect(contrastRatio(label, DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('a label already readable on its canvas is preserved (identity), and defaults track the canvas', () => {
    // #333333 on white = 12.63:1 -> kept; light/dark defaults both readable.
    expect(resolveChordLabelColor({ labelColor: '#333333' }, LIGHT_BG)).toBe('#333333');
    expect(contrastRatio(resolveChordLabelColor({}, LIGHT_BG), LIGHT_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    expect(contrastRatio(resolveChordLabelColor({}, DARK_BG), DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });

  it('a caller-pinned LIGHT panel under dark theme drives a dark default label (D-053 parity still holds)', () => {
    // effectiveBg resolved by the caller; a light panel -> dark label default.
    const label = resolveChordLabelColor({}, '#f7f7f7');
    expect(contrastRatio(label, '#f7f7f7')).toBeGreaterThanOrEqual(TEXT_FLOOR);
  });
});
