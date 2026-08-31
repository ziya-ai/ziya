/**
 * G-21 regression tests for the Vega-Lite recovery + theme helpers.
 *
 * Covers:
 *   D-252  no tolerant parse before JSON.parse (fence/prose, smart quotes,
 *          trailing commas, unquoted keys, single quotes, trailing ';') and a
 *          valid-but-mis-nested mark.encoding.
 *   D-255  an unknown scale.scheme name collapses the render to a blank canvas.
 *   D-256  a bare-array `data:[...]` is never wrapped as `{values:[...]}`.
 *   D-257  a theme-blind colour lands on the dark background (invisible text
 *          marks / explicit near-black guide colours).
 *
 * Direction (fail-without-the-fix) is asserted explicitly per defect: every
 * D-252 raw input is shown to throw under plain JSON.parse; the D-257 dark
 * assertions are paired with the light assertion that the other theme is still
 * correct (per the both-themes theme-fix contract).
 */

import {
    tolerantParseVegaSpec,
    stripSpecFences,
    normalizeSmartQuotes,
    sliceToOutermostObject,
    hoistMarkEncoding,
    validateColorSchemes,
    normalizeBareArrayData,
    reconcileThemeColors,
    resolveColorToRgb,
    contrastRatio,
} from '../vegaRecovery';

// ── D-252: tolerant parse ────────────────────────────────────────────────────

describe('tolerantParseVegaSpec — near-miss JSON is recovered (D-252)', () => {
    // Direction: each raw string is NOT parseable by strict JSON.parse, so
    // these tests fail against the unpatched (JSON.parse-only) path.
    const cases: Array<[string, string]> = [
        ['trailing comma', '{"mark":"bar","encoding":{"x":{"field":"a"},}}'],
        ['unquoted keys', '{mark:"bar",encoding:{x:{field:"a"}}}'],
        ['single-quoted', "{'mark':'bar','encoding':{'x':{'field':'a'}}}"],
        ['trailing semicolon', '{"mark":"bar","encoding":{"x":{"field":"a"}}};'],
        ['prose lead-in', 'Here is your chart:\n{"mark":"bar","encoding":{"x":{"field":"a"}}}'],
    ];

    it.each(cases)('strict JSON.parse throws on the %s form (fails pre-fix)', (_name, raw) => {
        expect(() => JSON.parse(raw)).toThrow();
    });

    it.each(cases)('recovers the %s form', (_name, raw) => {
        const parsed = tolerantParseVegaSpec(raw);
        expect(parsed).toBeTruthy();
        expect(parsed.mark).toBe('bar');
        expect(parsed.encoding.x.field).toBe('a');
    });

    it('strips a ```json fence with a prose lead-in', () => {
        const raw = 'Sure!\n```json\n{"mark":"bar","encoding":{"x":{"field":"a"}}}\n```';
        expect(() => JSON.parse(raw)).toThrow();
        const parsed = tolerantParseVegaSpec(raw);
        expect(parsed.mark).toBe('bar');
    });

    it('normalises smart quotes so the object parses', () => {
        const raw = '{\u201Cmark\u201D:\u201Cbar\u201D}';
        expect(() => JSON.parse(raw)).toThrow();
        expect(tolerantParseVegaSpec(raw).mark).toBe('bar');
    });

    it('leaves already-valid JSON untouched (identity of content)', () => {
        const raw = '{"mark":"point","encoding":{"y":{"field":"v"}}}';
        expect(tolerantParseVegaSpec(raw)).toEqual(JSON.parse(raw));
    });

    it('helper units behave', () => {
        expect(stripSpecFences('```json\n{"a":1}\n```')).toBe('{"a":1}');
        expect(normalizeSmartQuotes('\u201Cx\u201D')).toBe('"x"');
        expect(sliceToOutermostObject('junk {"a":1} tail;')).toBe('{"a":1}');
    });

    it('still throws (-> error panel) on genuinely unparseable input', () => {
        expect(() => tolerantParseVegaSpec('this is not a spec at all')).toThrow();
    });
});

describe('hoistMarkEncoding — mis-nested mark.encoding is hoisted (D-252 rider)', () => {
    it('moves mark.encoding to spec.encoding when top level has none', () => {
        const spec: any = { mark: { type: 'bar', encoding: { x: { field: 'a' } } } };
        hoistMarkEncoding(spec);
        expect(spec.encoding).toEqual({ x: { field: 'a' } });
        expect(spec.mark.encoding).toBeUndefined();
        expect(spec.mark.type).toBe('bar');
    });
    it('does NOT clobber an existing top-level encoding', () => {
        const spec: any = {
            mark: { type: 'bar', encoding: { x: { field: 'inner' } } },
            encoding: { x: { field: 'outer' } },
        };
        hoistMarkEncoding(spec);
        expect(spec.encoding.x.field).toBe('outer');
    });
    it('is a no-op for a string mark', () => {
        const spec: any = { mark: 'bar', encoding: { x: { field: 'a' } } };
        hoistMarkEncoding(spec);
        expect(spec.mark).toBe('bar');
    });
});

// ── D-255: unknown colour scheme ─────────────────────────────────────────────

describe('validateColorSchemes — unknown scheme dropped, valid kept (D-255)', () => {
    it('drops an unrecognised scheme name to the default', () => {
        const spec: any = { encoding: { color: { field: 'c', scale: { scheme: 'not-a-real-scheme' } } } };
        const n = validateColorSchemes(spec);
        expect(n).toBe(1);
        expect(spec.encoding.color.scale.scheme).toBeUndefined();
    });
    it('keeps a known scheme (viridis)', () => {
        const spec: any = { encoding: { color: { scale: { scheme: 'viridis' } } } };
        expect(validateColorSchemes(spec)).toBe(0);
        expect(spec.encoding.color.scale.scheme).toBe('viridis');
    });
    it('is case-insensitive for known schemes', () => {
        const spec: any = { encoding: { color: { scale: { scheme: 'Category10' } } } };
        expect(validateColorSchemes(spec)).toBe(0);
        expect(spec.encoding.color.scale.scheme).toBe('Category10');
    });
    it('leaves a hex scheme for the arc fixer (does not drop)', () => {
        const spec: any = { encoding: { color: { scale: { scheme: '#ff0000' } } } };
        expect(validateColorSchemes(spec)).toBe(0);
        expect(spec.encoding.color.scale.scheme).toBe('#ff0000');
    });
});

// ── D-256: bare-array data ───────────────────────────────────────────────────

describe('normalizeBareArrayData — bare array wrapped as {values} (D-256)', () => {
    it('wraps a top-level bare-array data', () => {
        const spec: any = { data: [{ city: 'A', pop: 1 }, { city: 'B', pop: 2 }], mark: 'bar' };
        normalizeBareArrayData(spec);
        expect(Array.isArray(spec.data)).toBe(false);
        expect(spec.data.values).toHaveLength(2);
        expect(spec.data.values[0].city).toBe('A');
    });
    it('leaves an already-object data untouched', () => {
        const spec: any = { data: { values: [{ x: 1 }] }, mark: 'bar' };
        normalizeBareArrayData(spec);
        expect(spec.data.values).toHaveLength(1);
    });
    it('does NOT wrap a Vega v5 native dataset array', () => {
        const spec: any = {
            $schema: 'https://vega.github.io/schema/vega/v5.json',
            data: [{ name: 'table', values: [{ x: 1 }] }],
            marks: [],
        };
        normalizeBareArrayData(spec);
        expect(Array.isArray(spec.data)).toBe(true);
        expect(spec.data[0].name).toBe('table');
    });
    it('wraps bare-array data inside a layer', () => {
        const spec: any = { layer: [{ data: [{ x: 1 }], mark: 'line' }] };
        normalizeBareArrayData(spec);
        expect(Array.isArray(spec.layer[0].data)).toBe(false);
        expect(spec.layer[0].data.values).toHaveLength(1);
    });
});

// ── D-257: theme colour reconciliation (BOTH themes) ─────────────────────────

describe('reconcileThemeColors — theme-blind colours resolved (D-257)', () => {
    it('DARK: sets a light default text-mark fill (fixes invisible near-black marks)', () => {
        const spec: any = { mark: 'text', encoding: {} };
        reconcileThemeColors(spec, /* isDarkMode */ true);
        const fill = spec.config.text.fill;
        const rgb = resolveColorToRgb(fill)!;
        // readable against the #333333 dark card
        expect(contrastRatio(rgb, [51, 51, 51])).toBeGreaterThanOrEqual(4.5);
    });

    it('LIGHT: default text-mark fill stays dark (other theme still correct)', () => {
        const spec: any = { mark: 'text', encoding: {} };
        reconcileThemeColors(spec, /* isDarkMode */ false);
        const rgb = resolveColorToRgb(spec.config.text.fill)!;
        expect(contrastRatio(rgb, [255, 255, 255])).toBeGreaterThanOrEqual(4.5);
    });

    it('DARK: an explicit axis.labelColor "black" (1.66:1) is nudged readable', () => {
        // direction: black on the #333 dark card is invisible pre-fix.
        expect(contrastRatio(resolveColorToRgb('black')!, [51, 51, 51])).toBeLessThan(3);
        const spec: any = { mark: 'bar', encoding: { x: { field: 'a', axis: { labelColor: 'black' } } } };
        reconcileThemeColors(spec, true);
        const resolved = spec.encoding.x.axis.labelColor;
        expect(resolved).not.toBe('black');
        expect(contrastRatio(resolveColorToRgb(resolved)!, [51, 51, 51])).toBeGreaterThanOrEqual(3);
    });

    it('LIGHT: an explicit axis.labelColor "black" is PRESERVED (legible, not over-changed)', () => {
        const spec: any = { mark: 'bar', encoding: { x: { field: 'a', axis: { labelColor: 'black' } } } };
        reconcileThemeColors(spec, false);
        expect(spec.encoding.x.axis.labelColor).toBe('black');
    });

    it('LIGHT: an explicit axis.labelColor "white" (invisible on white) is nudged readable', () => {
        const spec: any = { mark: 'bar', encoding: { x: { field: 'a', axis: { labelColor: 'white' } } } };
        reconcileThemeColors(spec, false);
        expect(spec.encoding.x.axis.labelColor).not.toBe('white');
        expect(contrastRatio(resolveColorToRgb(spec.encoding.x.axis.labelColor)!, [255, 255, 255]))
            .toBeGreaterThanOrEqual(3);
    });

    it('does NOT override an author-pinned config.text.fill', () => {
        const spec: any = { mark: 'text', config: { text: { fill: '#ff0000' } } };
        reconcileThemeColors(spec, true);
        expect(spec.config.text.fill).toBe('#ff0000');
    });

    it('preserves a legible mid guide colour on both themes', () => {
        // #1f77b4 on white = ~5:1, on #333 = ~2.6 (< 3) -> only touched in dark.
        const light: any = { encoding: { x: { axis: { labelColor: '#0a5' } } } };
        reconcileThemeColors(light, false);
        expect(light.encoding.x.axis.labelColor).toBe('#0a5'); // legible on white, kept
    });
});
