import { hexToRgbSafe } from '../../../utils/d3Plugins/hexColor';

describe('hexToRgbSafe — shorthand/RGBA hex parsing (Issue 20 COLOR-PARSE-FAIL)', () => {
    // ---- The regression: 3-digit shorthand that the OLD 6-digit-only regex
    //      rejected. Mermaid emits `#000`/`#fff`; these MUST now parse.
    it('parses 3-digit shorthand #000 -> rgb(0,0,0) (was COLOR-PARSE-FAIL)', () => {
        expect(hexToRgbSafe('#000')).toEqual({ r: 0, g: 0, b: 0 });
    });

    it('parses 3-digit shorthand #fff -> rgb(255,255,255)', () => {
        expect(hexToRgbSafe('#fff')).toEqual({ r: 255, g: 255, b: 255 });
    });

    it('parses 3-digit shorthand #abc by doubling each nibble', () => {
        // #abc -> #aabbcc
        expect(hexToRgbSafe('#abc')).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc });
    });

    it('parses shorthand without a leading # (000)', () => {
        expect(hexToRgbSafe('000')).toEqual({ r: 0, g: 0, b: 0 });
    });

    // ---- 4-digit shorthand with alpha: expand and drop alpha.
    it('parses 4-digit shorthand #abcd, dropping the alpha nibble', () => {
        // #abcd -> #aabbccdd, alpha dd dropped
        expect(hexToRgbSafe('#abcd')).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc });
    });

    // ---- 8-digit RGBA: parse rgb, discard alpha. (#12345678 from the spec.)
    it('parses 8-digit RGBA #12345678, dropping the alpha byte', () => {
        expect(hexToRgbSafe('#12345678')).toEqual({ r: 0x12, g: 0x34, b: 0x56 });
    });

    // ---- Happy path preserved: 6-digit still works exactly as before.
    it('still parses 6-digit hex #ffffff', () => {
        expect(hexToRgbSafe('#ffffff')).toEqual({ r: 255, g: 255, b: 255 });
    });

    it('still parses 6-digit hex without # and case-insensitively', () => {
        expect(hexToRgbSafe('AABBCC')).toEqual({ r: 0xaa, g: 0xbb, b: 0xcc });
    });

    // ---- GUARD CASES: the widened parser must STILL reject everything that is
    //      not well-formed hex, so callers keep falling back to named/rgb parse.
    it('rejects named colors (returns null so caller can fall back)', () => {
        expect(hexToRgbSafe('black')).toBeNull();
        expect(hexToRgbSafe('white')).toBeNull();
    });

    it('rejects rgb() strings (returns null so caller can fall back)', () => {
        expect(hexToRgbSafe('rgb(0,0,0)')).toBeNull();
    });

    it('rejects invalid hex lengths (1,2,5,7 digits)', () => {
        expect(hexToRgbSafe('#f')).toBeNull();
        expect(hexToRgbSafe('#ff')).toBeNull();
        expect(hexToRgbSafe('#fffff')).toBeNull();
        expect(hexToRgbSafe('#fffffff')).toBeNull();
    });

    it('rejects non-hex characters', () => {
        expect(hexToRgbSafe('#gggggg')).toBeNull();
        expect(hexToRgbSafe('#12345g')).toBeNull();
        expect(hexToRgbSafe('not-a-real-color-name')).toBeNull();
    });

    it('rejects empty / non-string input', () => {
        expect(hexToRgbSafe('')).toBeNull();
        // @ts-expect-error deliberately passing non-string to exercise the guard
        expect(hexToRgbSafe(null)).toBeNull();
        // @ts-expect-error deliberately passing non-string to exercise the guard
        expect(hexToRgbSafe(undefined)).toBeNull();
    });
});
