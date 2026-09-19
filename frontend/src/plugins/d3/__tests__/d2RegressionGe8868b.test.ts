/**
 * Regression guard for fix group G-e8868b (d2 engine).
 *
 * Defects D-071, D-072, D-073, D-075, D-076, D-077 were each verified fixed in
 * frontend/src/plugins/d3/d2Plugin.ts and later re-appeared as `regression`
 * when a fresh render (evidence run 2f254c3a) was taken against a stale build
 * bundle. The source-level fixes are intact; this canary pins the six root-cause
 * invariants through the plugin's exported pure helpers so that a future REVERT
 * of any one of them fails in CI (unit level) rather than silently only in a
 * headless render.
 *
 * Each assertion below fails if the corresponding fix is removed:
 *   D-071  explicit requested size honoured (viewBox left at content bounds)
 *   D-072  dark node fill darkened so white label clears contrast
 *   D-073  dark edge desaturated so it neither dominates nor vanishes
 *   D-075/D-077  alien-dialect detectors present (JSON / mermaid fast-fail)
 *   D-076  container bounds expand for off-origin (negative) container rects
 */
import {
    d2ThemeColors,
    d2ResolveSvgSize,
    d2CanvasBounds,
    d2CanvasSize,
    looksLikeJson,
    looksLikeMermaid,
    D2_DARK_BG,
} from '../d2Plugin';

// WCAG relative-luminance contrast ratio between two #rrggbb colours.
function contrast(a: string, b: string): number {
    const relLum = (hex: string): number => {
        const h = hex.replace('#', '');
        const ch = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
        const lin = ch.map(c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
    };
    const l1 = relLum(a);
    const l2 = relLum(b);
    const hi = Math.max(l1, l2);
    const lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
}

describe('G-e8868b d2 regression guards', () => {
    test('D-071: an explicit requested size overrides content size but keeps the content viewBox', () => {
        const canvas = { width: 620, height: 480, viewBox: '0 0 620 480' };
        // Oversize (3000x300) and undersize (260x220) requests are honoured on the
        // outer <svg> while the viewBox stays at the content bounds, so the browser
        // scales the whole graph instead of dropping/clipping nodes.
        const big = d2ResolveSvgSize(canvas, 3000, 300);
        expect(big.width).toBe(3000);
        expect(big.height).toBe(300);
        expect(big.viewBox).toBe('0 0 620 480');

        const small = d2ResolveSvgSize(canvas, 260, 220);
        expect(small.width).toBe(260);
        expect(small.height).toBe(220);
        expect(small.viewBox).toBe('0 0 620 480');

        // No / invalid request falls back to natural content size (never 0 or NaN).
        const none = d2ResolveSvgSize(canvas, undefined, undefined);
        expect(none.width).toBe(620);
        expect(none.height).toBe(480);
        expect(d2ResolveSvgSize(canvas, 0, -5).width).toBe(620);
        expect(d2ResolveSvgSize(canvas, NaN as any, undefined).width).toBe(620);
    });

    test('D-072: dark node fill lets white label text clear AA (>= 4.5:1)', () => {
        const dark = d2ThemeColors(true);
        const light = d2ThemeColors(false);
        // white on dark node fill
        const whiteOnDarkFill = contrast('#ffffff', dark.node);
        expect(whiteOnDarkFill).toBeGreaterThanOrEqual(4.5);
        // must be the darkened indigo, not the pre-fix #4361ee (which was 5.02:1 only)
        expect(dark.node.toLowerCase()).toBe('#303f9f');
        // light theme must NOT regress: black text on light fill stays very high
        expect(contrast('#000000', light.node)).toBeGreaterThanOrEqual(4.5);
    });

    test('D-073: dark edge is recessive on the page yet visible crossing the node fill', () => {
        const dark = d2ThemeColors(true);
        // desaturated grey-blue, not the pre-fix magenta #f72585
        expect(dark.edge.toLowerCase()).toBe('#9aa4b2');
        // visible on the #1f1f1f page (was 4.36:1 magenta -> now ~6.5:1)
        expect(contrast(dark.edge, D2_DARK_BG)).toBeGreaterThanOrEqual(4.5);
        // and no longer invisible where it crosses the node fill (was 1.33:1)
        expect(contrast(dark.edge, dark.node)).toBeGreaterThanOrEqual(3.0);
        // light edge unchanged
        expect(d2ThemeColors(false).edge.toLowerCase()).toBe('#666666');
    });

    test('D-075/D-077: alien dialects are detected before parsing (fast honest failure, no hang)', () => {
        // JSON graph payload
        expect(looksLikeJson('{ "nodes": [{"id": "a"}], "edges": [] }')).toBe(true);
        // mermaid flowchart source
        expect(looksLikeMermaid('flowchart TD\n  A --> B')).toBe(true);
        expect(looksLikeMermaid('graph LR; a-->b')).toBe(true);
        // genuine d2 must NOT be misclassified as either
        const realD2 = 'a: Node A\nb: Node B\na -> b: link';
        expect(looksLikeJson(realD2)).toBe(false);
        expect(looksLikeMermaid(realD2)).toBe(false);
    });

    test('D-076: container bounds expand to enclose an off-origin (negative-coord) container rect', () => {
        // A container whose rect extends to negative coordinates must not be
        // clipped off the top-left: the canvas origin shifts negative and the
        // dimensions grow to include it.
        const nodes = [
            { id: 'region.az1', x: -60, y: -40, width: 120, height: 60, parent: 'region' },
            { id: 'region.az2', x: 200, y: 200, width: 120, height: 60, parent: 'region' },
        ];
        const containers = [{ id: 'region', parent: null, children: ['region.az1', 'region.az2'] }];
        const bounds = d2CanvasBounds(nodes, containers);
        const [ox, oy] = bounds.viewBox.split(' ').map(Number);
        expect(ox).toBeLessThan(0);
        expect(oy).toBeLessThan(0);
        expect(bounds.width).toBeGreaterThan(0);
        expect(bounds.height).toBeGreaterThan(0);

        // With NO containers the origin stays pinned at 0,0 (byte-identical to the
        // pre-D-076 d2CanvasSize path — the fix is additive, not a behaviour change).
        const flat = d2CanvasBounds(nodes, []);
        expect(flat).toEqual(d2CanvasSize(nodes));
        expect(flat.viewBox.startsWith('0 0 ')).toBe(true);
    });
});
