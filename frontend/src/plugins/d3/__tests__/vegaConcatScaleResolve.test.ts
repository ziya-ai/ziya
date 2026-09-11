/**
 * D-263 regression tests for resolveConcatScaleConflicts.
 *
 * A concat resolves every NON-POSITIONAL scale as "shared" by default, so an
 * explicit `scale.domain` in one panel silently becomes the domain for every
 * sibling panel too. Sibling categories outside that domain resolve to an
 * `undefined` fill and their marks are drawn with no paint — geometry present,
 * colour absent, bars invisible. Verified against the vega-lite compiler: the
 * shared form emits ONE colour scale carrying panel 1's pinned domain (plus a
 * "Conflicting scale property" warning), while `resolve.scale.color =
 * "independent"` emits TWO, the second deriving its own domain from its own
 * data, with zero warnings.
 *
 * The pass must be NARROW. Injecting `independent` where the panels genuinely
 * wanted one shared legend would be its own defect, so every negative case
 * below is as load-bearing as the positive ones.
 */

import { resolveConcatScaleConflicts, sanitizeResolveScale } from '../vegaRecovery';

const colorOf = (spec: any) => spec?.resolve?.scale?.color;

/** The spec as the model actually produced it (colour-relevant structure). */
const originalSpec = () => ({
    vconcat: [
        {
            data: {
                values: [
                    { term: 'DBF beam switch', ms: 0.1, c: 'deterministic' },
                    { term: 'MAC L2 handoff', ms: 0.4, c: 'deterministic' },
                    { term: '|Delta d| typical', ms: 10, c: 'operating range' },
                    { term: '|Delta d| worst', ms: 30, c: 'operating range' },
                    { term: 'observed handover gap', ms: 100, c: 'blind-switch artifact' },
                ],
            },
            mark: { type: 'bar' },
            encoding: {
                x: { field: 'ms', type: 'quantitative', scale: { type: 'log', domain: [0.05, 220] } },
                y: { field: 'term', type: 'nominal' },
                color: {
                    field: 'c',
                    type: 'nominal',
                    scale: {
                        domain: ['deterministic', 'operating range', 'blind-switch artifact'],
                        range: ['#2ca02c', '#1f77b4', '#d62728'],
                    },
                },
            },
        },
        {
            layer: [
                {
                    data: {
                        values: [
                            { m: '1 TTI  (334.8 us)', p: 0.8299 },
                            { m: '2 TTI  (669.6 us)', p: 0.1701 },
                        ],
                    },
                    mark: { type: 'bar' },
                    encoding: {
                        x: { field: 'm', type: 'nominal' },
                        y: { field: 'p', type: 'quantitative', scale: { domain: [0, 1] } },
                        color: {
                            field: 'm',
                            type: 'nominal',
                            scale: { range: ['#1f77b4', '#d62728'] },
                            legend: null,
                        },
                    },
                },
                {
                    data: { values: [{ m: '1 TTI  (334.8 us)', p: 0.8299, l: '1-p = 0.8299' }] },
                    mark: { type: 'text' },
                    encoding: { x: { field: 'm' }, y: { field: 'p' }, text: { field: 'l' } },
                },
            ],
        },
    ],
});

// ── fires on a provable conflict ─────────────────────────────────────────────

describe('resolveConcatScaleConflicts — injects independent on a provable conflict', () => {
    test('the spec as produced: pinned categorical domain + alien sibling values', () => {
        const spec: any = originalSpec();
        expect(colorOf(spec)).toBeUndefined(); // no resolve authored — that IS the defect
        resolveConcatScaleConflicts(spec);
        expect(colorOf(spec)).toBe('independent');
    });

    test('the conflicting colour encoding is found INSIDE a panel\'s layer[]', () => {
        // Panel 2's colour lives in layer[0].encoding, not panel.encoding. A pass
        // that only looked at the panel's own encoding would find one use, see no
        // sibling, and silently do nothing.
        const spec: any = originalSpec();
        expect(spec.vconcat[1].encoding).toBeUndefined();
        expect(spec.vconcat[1].layer[0].encoding.color.field).toBe('m');
        resolveConcatScaleConflicts(spec);
        expect(colorOf(spec)).toBe('independent');
    });

    test('two panels pinning DIFFERENT explicit ranges (no domains at all)', () => {
        const spec: any = {
            hconcat: [
                { encoding: { color: { field: 'k', scale: { range: ['#111', '#222'] } } } },
                { encoding: { color: { field: 'k', scale: { range: ['#333', '#444'] } } } },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(colorOf(spec)).toBe('independent');
    });

    test('data inherited from the top level is in scope for a panel with none', () => {
        const spec: any = {
            data: { values: [{ k: 'zzz' }] },
            vconcat: [
                { encoding: { color: { field: 'k', scale: { domain: ['a', 'b'] } } } },
                { encoding: { color: { field: 'k' } } },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(colorOf(spec)).toBe('independent');
    });

    test('a non-colour shared channel (opacity) is corrected the same way', () => {
        const spec: any = {
            vconcat: [
                {
                    data: { values: [{ g: 'x' }] },
                    encoding: { opacity: { field: 'g', scale: { domain: ['x', 'y'] } } },
                },
                {
                    data: { values: [{ g: 'NOT-IN-DOMAIN' }] },
                    encoding: { opacity: { field: 'g' } },
                },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve.scale.opacity).toBe('independent');
        expect(colorOf(spec)).toBeUndefined(); // only the offending channel
    });

    test('a nested concat gets its own resolve', () => {
        const spec: any = {
            vconcat: [
                {
                    hconcat: [
                        {
                            data: { values: [{ k: 'a' }] },
                            encoding: { color: { field: 'k', scale: { domain: ['a'] } } },
                        },
                        {
                            data: { values: [{ k: 'OTHER' }] },
                            encoding: { color: { field: 'k' } },
                        },
                    ],
                },
                { mark: 'bar' },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(colorOf(spec.vconcat[0])).toBe('independent');
    });
});

// ── must NOT fire ────────────────────────────────────────────────────────────

describe('resolveConcatScaleConflicts — leaves a legitimately shared scale alone', () => {
    test('same field, same domain in both panels (a real shared legend)', () => {
        const spec: any = {
            vconcat: [
                {
                    data: { values: [{ k: 'a', v: 1 }, { k: 'b', v: 2 }] },
                    encoding: { color: { field: 'k', scale: { domain: ['a', 'b'] } } },
                },
                {
                    data: { values: [{ k: 'a', v: 3 }, { k: 'b', v: 4 }] },
                    encoding: { color: { field: 'k', scale: { domain: ['a', 'b'] } } },
                },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('an author-supplied resolve.scale.color is never overridden', () => {
        const spec: any = originalSpec();
        spec.resolve = { scale: { color: 'shared' } };
        resolveConcatScaleConflicts(spec);
        expect(colorOf(spec)).toBe('shared');
    });

    test('a QUANTITATIVE domain is a min/max, not a member list', () => {
        // [0,100] vs a value of 50: set membership would call this a conflict and
        // split every binned/continuous colour legend in the product.
        const spec: any = {
            vconcat: [
                {
                    data: { values: [{ n: 10 }, { n: 90 }] },
                    encoding: { color: { field: 'n', type: 'quantitative', scale: { domain: [0, 100] } } },
                },
                { data: { values: [{ n: 50 }] }, encoding: { color: { field: 'n', type: 'quantitative' } } },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('no inline data and no pinned range: nothing is provable', () => {
        const spec: any = {
            vconcat: [
                { data: { url: 'a.csv' }, encoding: { color: { field: 'k', scale: { domain: ['a'] } } } },
                { data: { url: 'b.csv' }, encoding: { color: { field: 'k' } } },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('a single-panel concat has no sibling to conflict with', () => {
        const spec: any = { vconcat: [originalSpec().vconcat[0]] };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('a top-level layered (non-concat) spec is untouched', () => {
        const spec: any = { layer: [{ encoding: { color: { field: 'k', scale: { domain: ['a'] } } } }] };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('identical ranges in both panels are not a range conflict', () => {
        const spec: any = {
            vconcat: [
                { encoding: { color: { field: 'k', scale: { range: ['#111', '#222'] } } } },
                { encoding: { color: { field: 'k', scale: { range: ['#111', '#222'] } } } },
            ],
        };
        resolveConcatScaleConflicts(spec);
        expect(spec.resolve).toBeUndefined();
    });

    test('non-spec inputs are returned unchanged', () => {
        expect(resolveConcatScaleConflicts(null)).toBeNull();
        expect(resolveConcatScaleConflicts(undefined)).toBeUndefined();
        expect(resolveConcatScaleConflicts('nope' as any)).toBe('nope');
    });
});

// ── the seam: injection has to survive the very next pass ────────────────────

describe('resolveConcatScaleConflicts + sanitizeResolveScale (D-263 x D-262)', () => {
    test('the injected resolve survives sanitizeResolveScale', () => {
        // These two run back to back in vegaLitePlugin. Before D-262, sanitize
        // deleted resolve for every concat, so injecting would have been a no-op
        // and the bars would still have vanished.
        const spec: any = resolveConcatScaleConflicts(originalSpec());
        expect(colorOf(spec)).toBe('independent');
        sanitizeResolveScale(spec);
        expect(colorOf(spec)).toBe('independent');
    });

    test('a nested faceted spec.resolve is still stripped after the new pass', () => {
        const spec: any = {
            facet: { field: 'g', type: 'nominal' },
            spec: { layer: [], resolve: { scale: { y: 'independent' } } },
        };
        resolveConcatScaleConflicts(spec);
        sanitizeResolveScale(spec);
        expect(spec.spec.resolve).toBeUndefined();
    });
});
