/**
 * Regression tests for `demoteWebglTracesForCapture` (Issue 7 of the
 * graphics-stress ledger).
 *
 * WebGL plotly traces (`scattergl`, `heatmapgl`, ...) render to a <canvas>
 * via a WebGL context.  The headless capture path reuses ONE persistent
 * Chromium (app/services/diagram_renderer.py), so those contexts accumulate
 * and hit Chromium's active-context ceiling ("Too many active WebGL
 * contexts"); the render page's completion handshake then never settles and
 * the capture times out even though plotly rendered correctly.
 *
 * The fix swaps `*gl` -> the SVG-backed trace type, but ONLY under headless
 * capture (`navigator.webdriver === true`).  The interactive UI must keep
 * WebGL for performance, so the guard is the load-bearing part of the
 * behaviour and is what these tests pin hardest.
 */

import {
    demoteWebglTracesForCapture,
    preprocessPlotlySpec,
} from '../plotlyPreprocessor';

/** Set/clear `navigator.webdriver` for the duration of one test. */
function withWebdriver<T>(value: boolean | undefined, fn: () => T): T {
    const nav = navigator as any;
    const had = Object.prototype.hasOwnProperty.call(nav, 'webdriver');
    const prior = nav.webdriver;
    try {
        Object.defineProperty(nav, 'webdriver', {
            value,
            configurable: true,
            writable: true,
        });
        return fn();
    } finally {
        if (had) {
            Object.defineProperty(nav, 'webdriver', {
                value: prior,
                configurable: true,
                writable: true,
            });
        } else {
            delete nav.webdriver;
        }
    }
}

describe('demoteWebglTracesForCapture', () => {
    describe('under headless capture (navigator.webdriver === true)', () => {
        it('demotes scattergl to scatter', () => {
            const out = withWebdriver(true, () =>
                demoteWebglTracesForCapture([{ type: 'scattergl', x: [1], y: [2] }]),
            );
            expect(out[0].type).toBe('scatter');
        });

        it('demotes the whole *gl family, not just scattergl', () => {
            const out = withWebdriver(true, () =>
                demoteWebglTracesForCapture([
                    { type: 'scattergl' },
                    { type: 'scatterpolargl' },
                    { type: 'heatmapgl' },
                    { type: 'parcoords' }, // no gl suffix — untouched
                ]),
            );
            expect(out.map((t: any) => t.type)).toEqual([
                'scatter',
                'scatterpolar',
                'heatmap',
                'parcoords',
            ]);
        });

        it('preserves every other property on a demoted trace', () => {
            const out = withWebdriver(true, () =>
                demoteWebglTracesForCapture([
                    { type: 'scattergl', x: [1, 2], y: [3, 4], name: 'series A', mode: 'markers' },
                ]),
            );
            expect(out[0]).toEqual({
                type: 'scatter',
                x: [1, 2],
                y: [3, 4],
                name: 'series A',
                mode: 'markers',
            });
        });

        it('does not mutate the input array or its traces', () => {
            const input = [{ type: 'scattergl', x: [1] }];
            const out = withWebdriver(true, () => demoteWebglTracesForCapture(input));
            expect(input[0].type).toBe('scattergl'); // original untouched
            expect(out[0]).not.toBe(input[0]);       // new object returned
        });

        it('only strips a TRAILING gl, not gl elsewhere in the name', () => {
            // A hypothetical future trace type containing "gl" mid-name must not be
            // corrupted — the regex is anchored to the end for exactly this reason.
            const out = withWebdriver(true, () =>
                demoteWebglTracesForCapture([{ type: 'glyphchart' }]),
            );
            expect(out[0].type).toBe('glyphchart');
        });
    });

    describe('in the interactive UI (webdriver falsy)', () => {
        // This is the guard that keeps WebGL performance for real users.  If
        // these fail, the fix has leaked into the interactive path.
        it('leaves scattergl alone when webdriver is false', () => {
            const out = withWebdriver(false, () =>
                demoteWebglTracesForCapture([{ type: 'scattergl' }]),
            );
            expect(out[0].type).toBe('scattergl');
        });

        it('leaves scattergl alone when webdriver is undefined', () => {
            const out = withWebdriver(undefined, () =>
                demoteWebglTracesForCapture([{ type: 'scattergl' }]),
            );
            expect(out[0].type).toBe('scattergl');
        });

        it('returns the input array itself when not capturing (no copy cost)', () => {
            const input = [{ type: 'scattergl' }];
            const out = withWebdriver(false, () => demoteWebglTracesForCapture(input));
            expect(out).toBe(input);
        });
    });

    describe('malformed input', () => {
        it('passes a non-array through unchanged rather than throwing', () => {
            const out = withWebdriver(true, () =>
                demoteWebglTracesForCapture(undefined as any),
            );
            expect(out).toBeUndefined();
        });

        it('tolerates null/typeless traces', () => {
            const out = withWebdriver(true, () =>
                demoteWebglTracesForCapture([null, {}, { type: 42 }] as any),
            );
            expect(out).toEqual([null, {}, { type: 42 }]);
        });

        it('handles an empty trace list', () => {
            const out = withWebdriver(true, () => demoteWebglTracesForCapture([]));
            expect(out).toEqual([]);
        });
    });

    describe('wired into the preprocessing pipeline', () => {
        // The unit above tests the helper; this pins that preprocessPlotlySpec
        // actually calls it, so the fix cannot be silently unwired.
        it('demotes through preprocessPlotlySpec under capture', () => {
            const out = withWebdriver(true, () =>
                preprocessPlotlySpec({
                    data: [{ type: 'scattergl', x: [1], y: [2] }],
                    layout: { title: 'x' },
                } as any),
            );
            // `PlotlySpec.data` is optional (`data?: any[]`), so indexing it
            // is an error under strictNullChecks.  Assert it survived the
            // round-trip first — a preprocessor that dropped `data` entirely
            // would otherwise fail with a confusing index error.
            expect(out.data).toBeDefined();
            expect(out.data![0].type).toBe('scatter');
        });

        it('does not demote through preprocessPlotlySpec in the UI', () => {
            const out = withWebdriver(false, () =>
                preprocessPlotlySpec({
                    data: [{ type: 'scattergl', x: [1], y: [2] }],
                    layout: { title: 'x' },
                } as any),
            );
            expect(out.data).toBeDefined();
            expect(out.data![0].type).toBe('scattergl');
        });
    });
});
