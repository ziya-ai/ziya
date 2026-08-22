/**
 * Regression test for Issue 44 (graphviz/mermaid/vega contrast enhancer):
 * extended CSS named fills (`lightgrey`, `lightblue`, `pink`, `orange`, ...)
 * were not resolvable by the color-parse path, so `calculateContrastRatio`
 * logged `COLOR-PARSE-FAIL`, forced contrast to a degenerate `1`, and
 * `getOptimalTextColor` fell through to white text — invisible/low-contrast on
 * a light fill.
 *
 * The fix adds `namedColorToHex` + `CSS_NAMED_COLORS` to `hexColor.ts` and wires
 * named resolution into `hexToRgbSafe`. Because `colorUtils.parseColor` and
 * `getOptimalTextColor` both resolve via `hexToRgb` -> `hexToRgbSafe`, fixing
 * that one helper repairs BOTH call sites with no edit to colorUtils.
 *
 * These tests import the REAL modules (no re-implementation) and pin BOTH
 * directions: known named colors resolve, and genuinely-unknown names are still
 * REJECTED (null / contrast stays 1) so the fix is not a catch-all.
 *
 * NON-VACUOUS: against the pre-fix code `namedColorToHex`/`CSS_NAMED_COLORS`
 * did not exist (import would fail to type-check) and, functionally,
 * `hexToRgbSafe('lightgrey')` returned null so
 * `calculateContrastRatio('#000000','lightgrey')` returned exactly 1 and
 * `getOptimalTextColor('lightgrey')` returned '#ffffff'. Every assertion below
 * that expects >1 / '#000000' / a resolved hex would fail on the old code.
 */
import { namedColorToHex, hexToRgbSafe, CSS_NAMED_COLORS } from '../../../utils/d3Plugins/hexColor';
import { calculateContrastRatio, getOptimalTextColor } from '../../../utils/colorUtils';

describe('namedColorToHex (Issue 44)', () => {
    it('resolves the exact adversarial fills lightgrey/lightblue', () => {
        expect(namedColorToHex('lightgrey')).toBe('#d3d3d3');
        expect(namedColorToHex('lightblue')).toBe('#add8e6');
    });

    it('resolves the extended named family (pink/orange/darkgray/rebeccapurple)', () => {
        expect(namedColorToHex('pink')).toBe('#ffc0cb');
        expect(namedColorToHex('orange')).toBe('#ffa500');
        expect(namedColorToHex('darkgray')).toBe('#a9a9a9');
        expect(namedColorToHex('rebeccapurple')).toBe('#663399');
    });

    it('is case-insensitive and whitespace-tolerant', () => {
        expect(namedColorToHex('LightGrey')).toBe('#d3d3d3');
        expect(namedColorToHex('  LIGHTBLUE  ')).toBe('#add8e6');
    });

    it('keeps the basic 12 colors the old inline map already knew', () => {
        expect(namedColorToHex('white')).toBe('#ffffff');
        expect(namedColorToHex('black')).toBe('#000000');
        expect(namedColorToHex('red')).toBe('#ff0000');
    });

    // ---- GUARD DIRECTION: unknown / non-named inputs still rejected ----
    it('rejects genuinely unknown color names (not a catch-all)', () => {
        expect(namedColorToHex('not-a-real-color')).toBeNull();
        expect(namedColorToHex('lightblueish')).toBeNull();
        expect(namedColorToHex('')).toBeNull();
    });

    it('rejects hex strings and rgb() (those are not named colors)', () => {
        // A leading '#' disqualifies; rgb() is not in the table.
        expect(namedColorToHex('#ffffff')).toBeNull();
        expect(namedColorToHex('rgb(1,2,3)')).toBeNull();
    });

    it('tolerates null/undefined/non-string without throwing', () => {
        // @ts-expect-error deliberate wrong type
        expect(namedColorToHex(null)).toBeNull();
        // @ts-expect-error deliberate wrong type
        expect(namedColorToHex(undefined)).toBeNull();
        // @ts-expect-error deliberate wrong type
        expect(namedColorToHex(42)).toBeNull();
    });

    it('does not disagree with isLightBackground: light names are in the table', () => {
        // The internal inconsistency this fix removes.
        for (const name of ['lightblue', 'lightgreen', 'lightyellow', 'lightgrey', 'lightgray', 'pink', 'yellow', 'white']) {
            expect(CSS_NAMED_COLORS[name]).toBeDefined();
        }
    });
});

describe('hexToRgbSafe named-color fallback (Issue 44)', () => {
    it('resolves named colors to RGB', () => {
        expect(hexToRgbSafe('lightgrey')).toEqual({ r: 0xd3, g: 0xd3, b: 0xd3 });
        expect(hexToRgbSafe('lightblue')).toEqual({ r: 0xad, g: 0xd8, b: 0xe6 });
    });

    it('still parses hex exactly as before (no regression)', () => {
        expect(hexToRgbSafe('#aabbcc')).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc });
        expect(hexToRgbSafe('#000')).toEqual({ r: 0, g: 0, b: 0 });      // 3-digit shorthand
        expect(hexToRgbSafe('#aabbccdd')).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc }); // 8-digit alpha dropped
    });

    it('still returns null for genuinely unparseable input', () => {
        expect(hexToRgbSafe('not-a-real-color')).toBeNull();
        expect(hexToRgbSafe('rgb(1,2,3)')).toBeNull();
    });
});

describe('colorUtils end-to-end with named colors (Issue 44 integration)', () => {
    it('calculateContrastRatio no longer collapses to 1 on lightgrey/lightblue', () => {
        // Pre-fix: exactly 1 (COLOR-PARSE-FAIL). Post-fix: a real ratio > 1.
        const cGrey = calculateContrastRatio('#000000', 'lightgrey');
        const cBlue = calculateContrastRatio('black', 'lightblue');
        expect(cGrey).toBeGreaterThan(1);
        expect(cBlue).toBeGreaterThan(1);
        // black-on-light should be a strong contrast, sanity-check magnitude.
        expect(cGrey).toBeGreaterThan(10);
    });

    it('calculateContrastRatio STILL returns 1 for a truly unknown name (guard)', () => {
        // The intentional reject-and-fallback path must be preserved.
        expect(calculateContrastRatio('#000000', 'not-a-real-color')).toBe(1);
    });

    it('getOptimalTextColor picks black text on light named fills (was white pre-fix)', () => {
        expect(getOptimalTextColor('lightgrey')).toBe('#000000');
        expect(getOptimalTextColor('lightblue')).toBe('#000000');
        expect(getOptimalTextColor('pink')).toBe('#000000');
    });

    it('getOptimalTextColor still returns white for unparseable input (guard)', () => {
        expect(getOptimalTextColor('not-a-real-color')).toBe('#ffffff');
    });
});
