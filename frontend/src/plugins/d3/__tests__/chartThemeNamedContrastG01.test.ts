/**
 * G-01 / D-001 — named + spaced-functional colours must not bypass the
 * per-theme contrast reconciliation.
 *
 * Root cause (confirmed against source): `classifyColor`
 *   (a) rejected any string containing whitespace BEFORE trying its rgb()/rgba()
 *       branch, so a valid `rgb(255, 0, 0)` (inter-channel spaces) was dropped as
 *       "absent" and the colour never reached ensureReadableFill/readableStroke;
 *   (b) returned `{ named }` for every bare keyword, and both ensureReadableFill
 *       and readableStroke short-circuited `if (c.named) return c.named;`, so a
 *       named colour below the WCAG graphical floor on the active surface (e.g.
 *       `navy` on the dark canvas at 1.01:1, `white` on the light canvas at
 *       1.00:1) was painted verbatim and vanished;
 *   (c) treated context-dependent keywords (`currentColor`, `inherit`) as named
 *       colours and passed them through as literal strings.
 *
 * The fix parses rgb() before the whitespace guard, blacklists the
 * context-dependent keywords, and resolves a named colour to a hex so its
 * contrast CAN be reasoned about — keeping it verbatim when it already clears
 * the floor (identity preserved) and reconciling it toward the surface-opposite
 * when it does not.
 *
 * BOTH themes are asserted for every theme case: one assertion that the broken
 * theme is now legible, paired with one that the other theme is still legible
 * (and readable colours are still returned verbatim there). DIRECTION is pinned
 * by resolving the returned colour to a hex and showing that the pre-fix
 * verbatim value would FAIL the same floor these now clear.
 */
import {
  classifyColor,
  contrastRatio,
  ensureReadableFill,
  namedColorToHex,
  CHART_DARK_BG,
  CHART_LIGHT_BG,
} from '../chartTheme';
import { readableStroke, FORCE_DARK_BG, FORCE_LIGHT_BG } from '../forceDirectedPlugin';

/** Resolve a returned colour (name or hex) to a #rrggbb hex for contrast math. */
function toHex(out: string): string | null {
  return classifyColor(out)?.hex ?? namedColorToHex(out) ?? null;
}

describe('classifyColor — D-001 parse ordering + keyword blacklist', () => {
  it('parses a spaced rgb()/rgba() that the whitespace guard used to drop', () => {
    // Pre-fix: the /\s/ token guard ran first and returned null for these.
    expect(classifyColor('rgb(255, 0, 0)')).toEqual({ hex: '#ff0000' });
    expect(classifyColor('rgba(0, 0, 255, 0.5)')).toEqual({ hex: '#0000ff' });
    // A zero-alpha rgba() is still "absent".
    expect(classifyColor('rgba(0, 0, 0, 0)')).toBeNull();
  });

  it('treats context-dependent keywords as absent (were mis-classified as named)', () => {
    // Pre-fix: each returned { named: <keyword> } and reached the canvas verbatim.
    expect(classifyColor('currentColor')).toBeNull();
    expect(classifyColor('inherit')).toBeNull();
    expect(classifyColor('initial')).toBeNull();
    expect(classifyColor('unset')).toBeNull();
  });

  it('does not regress the existing contract for hex / named / transparent', () => {
    expect(classifyColor('#abcdef')).toEqual({ hex: '#abcdef' });
    expect(classifyColor('rgb(1,2,3)')).toEqual({ hex: '#010203' }); // no-space still works
    expect(classifyColor('steelblue')).toEqual({ named: 'steelblue' });
    expect(classifyColor('transparent')).toBeNull();
    expect(classifyColor('theme.accent')).toBeNull(); // dotted design token still rejected
    expect(classifyColor('var(--x)')).toBeNull();
  });
});

describe('ensureReadableFill — named colour reconciled per theme (both themes)', () => {
  it('a below-floor named fill (navy) is reconciled in DARK, kept verbatim in LIGHT', () => {
    // Broken theme (dark): navy #000080 is 1.01:1 on #1e1e1e — must be nudged legible.
    const dark = ensureReadableFill('navy', CHART_DARK_BG, '#cccccc', 3);
    const darkHex = toHex(dark)!;
    expect(darkHex).toBeTruthy();
    expect(contrastRatio(darkHex, CHART_DARK_BG)).toBeGreaterThanOrEqual(3);
    // Direction: the pre-fix verbatim 'navy' resolves to #000080 and FAILS the floor.
    expect(contrastRatio(namedColorToHex('navy')!, CHART_DARK_BG)).toBeLessThan(3);

    // Other theme (light): navy is 16:1 on white — already fine and returned verbatim.
    const light = ensureReadableFill('navy', CHART_LIGHT_BG, '#333333', 3);
    expect(light).toBe('navy');
    expect(contrastRatio(namedColorToHex(light)!, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(3);
  });

  it("a near-surface named fill (white) is reconciled in LIGHT, kept in DARK", () => {
    // Broken theme (light): white is 1.00:1 on white — must be nudged legible.
    const light = ensureReadableFill('white', CHART_LIGHT_BG, '#333333', 3);
    const lightHex = toHex(light)!;
    expect(contrastRatio(lightHex, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(namedColorToHex('white')!, CHART_LIGHT_BG)).toBeLessThan(3); // direction

    // Other theme (dark): white is 16:1 on the dark canvas — kept verbatim.
    expect(ensureReadableFill('white', CHART_DARK_BG, '#cccccc', 3)).toBe('white');
  });

  it('an unknown named colour is still passed through unchanged (not a catch-all)', () => {
    // A bare alphabetic keyword not in the table classifies as { named } but is
    // unresolvable, so its contrast is uncomputable and it is passed through.
    expect(ensureReadableFill('notarealcolorname', CHART_DARK_BG, '#cccccc', 3)).toBe('notarealcolorname');
  });

  it('a spaced rgb() fill below the floor is now reconciled (was dropped to fallback)', () => {
    // rgb(20, 20, 90) ~ dark navy; below floor on the dark canvas.
    const out = ensureReadableFill('rgb(20, 20, 90)', CHART_DARK_BG, '#cccccc', 3);
    const outHex = toHex(out)!;
    expect(outHex).toBeTruthy();
    expect(contrastRatio(outHex, CHART_DARK_BG)).toBeGreaterThanOrEqual(3);
  });
});

describe('readableStroke — named stroke reconciled per theme (both themes)', () => {
  it('a below-floor named stroke (navy) is nudged in DARK, kept verbatim in LIGHT', () => {
    // Broken theme (dark): navy composited at 0.9 is ~1.03:1 — must be nudged.
    const dark = readableStroke('navy', FORCE_DARK_BG, 0.9, '#b0b0b0');
    const darkHex = toHex(dark)!;
    expect(darkHex).toBeTruthy();
    expect(contrastRatio(darkHex, FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(namedColorToHex('navy')!, FORCE_DARK_BG)).toBeLessThan(3); // direction

    // Other theme (light): navy is legible on white and returned verbatim.
    expect(readableStroke('navy', FORCE_LIGHT_BG, 0.9, '#6b6b6b')).toBe('navy');
  });

  it('a readable named stroke (crimson) is still passed through unchanged in light', () => {
    // Preserves the pre-existing pass-through contract for already-legible names.
    expect(readableStroke('crimson', FORCE_LIGHT_BG, 0.9, '#6b6b6b')).toBe('crimson');
  });

  it('an unknown named stroke is passed through unchanged', () => {
    expect(readableStroke('notarealcolorname', FORCE_DARK_BG, 0.9, '#b0b0b0')).toBe('notarealcolorname');
  });
});
