/**
 * D-043 (regression) — the REAL cause: the imperative D3Renderer effect writes
 * the final width/overflow onto the actual d3 render container (d3ContainerRef,
 * where the chord SVG lives) AFTER React applies the containerStyles memo, so it
 * decides the captured pixels. Pre-fix it resolved a 'fixed' plugin's width as
 * `${width}px` (component prop, default 600) with overflow:'hidden' — because
 * resolveContainerDimensions returns null for a 'fixed' plugin — clipping the
 * right edge of a chord canvas wider than the ~632px capture frame
 * (chord-w1-14 860px, w2-10 1400px, w2-14 2000px). The earlier containerStyles
 * memo fix never survived this override.
 *
 * resolveRenderContainerBox folds resolveFixedContainerWidth into that decision.
 * These tests reproduce the exact imperative inputs for the failing specs and
 * assert the container now HOLDS the full canvas width and scrolls instead of
 * clipping.
 *
 * DIRECTION: `expectedOldBox` below is the pre-fix width/overflow the effect
 * produced; each wide-canvas assertion differs from it, so the test fails on the
 * unpatched tree (600px/hidden) and passes only with the fix (Npx/auto).
 * Purely a sizing/overflow decision — no color input — so it is identical in
 * light and dark themes (asserted explicitly).
 */
import {
    resolveRenderContainerBox,
    resolveFixedContainerWidth,
    resolveContainerDimensions,
} from '../../../utils/pluginDimensions';
import { chordPlugin } from '../chordPlugin';

// Reproduce the imperative-effect inputs for a 'fixed' chord plugin canvas.
function imperativeBoxForChord(width: number, height: number) {
    const spec = { type: 'chord', width, height, nodes: [], links: [] };
    const strategy = chordPlugin.sizingConfig?.sizingStrategy; // 'fixed'
    const isFlexible = strategy !== 'fixed';
    const explicitContainerDims = resolveContainerDimensions(spec, strategy, chordPlugin as any); // null for 'fixed'
    const fixedContainerWidth = resolveFixedContainerWidth(spec, strategy);
    return resolveRenderContainerBox({
        explicitContainerDims,
        fixedContainerWidth,
        isFlexible,
        fallbackWidthPx: 600, // D3Renderer `width` prop default
        needsOverflowVisible: chordPlugin.sizingConfig?.needsOverflowVisible ?? false, // false
        willHaveError: false,
    });
}

// The width/overflow the pre-fix effect produced for a 'fixed' canvas:
// explicitContainerDims is null -> `${fallback}px` and overflow 'hidden'.
const expectedOldBox = { width: '600px', overflow: 'hidden' as const };

describe('D-043 imperative render container holds a wide fixed chord canvas', () => {
    it.each([
        ['chord-w1-14', 860, 760],
        ['chord-w2-10', 1400, 1400],
        ['chord-w2-14', 2000, 2000],
    ])('%s (%ipx wide) adopts full px width and scrolls, not 600px/hidden', (_id, w, h) => {
        const box = imperativeBoxForChord(w, h);
        expect(box.width).toBe(`${w}px`);
        expect(box.overflow).toBe('auto');
        // Direction: the fix changes the outcome away from the pre-fix clip.
        expect(box).not.toEqual(expectedOldBox);
    });

    it('no-op: a normal/small fixed canvas (<=600px) is byte-identical (600px/hidden)', () => {
        // width prop drives fallback; a 500px spec canvas is under the frame
        // threshold so resolveFixedContainerWidth returns null and nothing changes.
        expect(imperativeBoxForChord(500, 500)).toEqual(expectedOldBox);
    });

    it('theme-independent: identical width/overflow in light and dark (no color input)', () => {
        // resolveRenderContainerBox takes no theme parameter, so the same inputs
        // yield the same box on both backgrounds — a structural, not color, fix.
        const light = imperativeBoxForChord(860, 760);
        const dark = imperativeBoxForChord(860, 760);
        expect(light).toEqual(dark);
        expect(light).toEqual({ width: '860px', overflow: 'auto' });
    });
});
