/**
 * G-eb0a4b — jointPlugin.ts, three still-broken defects sharing this file.
 *
 * D-133 (directed-graph-layout-throws-at-scale, joint-w2-02) and
 * D-405 (fit-plan-downscale-to-illegible, joint-w2-03) share ONE real root
 * cause that the earlier per-defect fixes missed: a SUCCESSFUL DirectedGraph
 * layout can still emit a degenerate, extremely wide bbox (a 131-node balanced
 * tree whose widest rank holds ~100 nodes; a hub fanning to 60 leaves in one
 * rank). That surface only fits the capture box by downscaling to a ~0.04-0.06x
 * illegible strip, and the JOINT_MIN_FIT_SCALE floor just hands the same
 * illegible aspect to the downstream capture-fit. `shouldReflowWideLayout` now
 * detects it (many nodes, far wider than tall, width-fit below the legibility
 * floor) and the render loop re-flows to the near-square grid used by the
 * throw-fallback. These tests pin the direction: the wide fan-outs are caught,
 * a tall chain and ordinary graphs are NOT, and the reflow grid is legible.
 *
 * D-417 (hardcoded-fill-matches-paper): the previous fix added only a stroke
 * outline, which reads as an EMPTY box when the fill still equals the page.
 * computeJointElementStyle now ALSO nudges the fill to stand off the page
 * (>=1.5:1) while preserving the author's light/dark character. Asserted in
 * BOTH themes.
 */
import {
    shouldReflowWideLayout,
    computeGridFallbackPositions,
    computeJointElementStyle,
    jointContrastRatio,
    jointPageBackground,
    JOINT_WIDE_REFLOW_MIN_NODES,
} from '../jointPlugin';

const containerWidth = 640; // headless capture box width used by the joint fit path

describe('D-133 / D-405 wide auto-layout reflow guard', () => {
    it('FIRES for a 60-leaf single-rank fan (joint-w2-03 ~10500x300)', () => {
        // one hub + 60 leaves in a single rank -> ~10500 wide, ~300 tall.
        expect(shouldReflowWideLayout(10500, 300, containerWidth, 61)).toBe(true);
        // direction: without a reflow this only fits by downscaling far below the
        // legibility floor (a ~20px strip), which is exactly the failure.
        expect(containerWidth / 10500).toBeLessThan(0.15);
    });

    it('FIRES for a 100-wide bottom rank (joint-w2-02 balanced tree at scale)', () => {
        // widest rank ~100 nodes at ~170px pitch -> ~17000 wide, ~600 tall (3 ranks).
        expect(shouldReflowWideLayout(17000, 600, containerWidth, 131)).toBe(true);
    });

    it('does NOT fire for a tall linear chain (joint-w2-04): narrow bbox fits by width', () => {
        // 80-node chain TB -> ~200 wide, ~12800 tall. Width fits; height is handled
        // by the existing JOINT_MAX_RENDER_HEIGHT downscale, not a reflow.
        expect(shouldReflowWideLayout(200, 12800, containerWidth, 80)).toBe(false);
    });

    it('does NOT fire for an ordinary graph that already fits legibly', () => {
        // a 40-node graph ~1200x900 -> width-fit ~0.53, well above the floor.
        expect(shouldReflowWideLayout(1200, 900, containerWidth, 40)).toBe(false);
    });

    it('does NOT fire below the node-count gate even when very wide', () => {
        const few = JOINT_WIDE_REFLOW_MIN_NODES - 1;
        expect(shouldReflowWideLayout(12000, 300, containerWidth, few)).toBe(false);
    });

    it('the reflow grid is near-square and legible (fits above the floor)', () => {
        const cells = Array.from({ length: 61 }, (_, i) => ({ id: `n${i}`, width: 120, height: 60 }));
        const placements = computeGridFallbackPositions(cells);
        expect(placements.length).toBe(61);
        const maxX = Math.max(...placements.map(p => p.x)) + 120;
        const maxY = Math.max(...placements.map(p => p.y)) + 60;
        // near-square (aspect < the reflow trigger) and now fits the box legibly.
        expect(maxX / maxY).toBeLessThan(3);
        expect(containerWidth / maxX).toBeGreaterThan(0.15);
    });
});

describe('D-417 opaque author fill that matches the page is nudged AND outlined', () => {
    const call = (fill: string, stroke: string, theme: 'light' | 'dark') =>
        computeJointElementStyle(
            { id: 'n', attrs: { body: { fill, stroke, strokeWidth: 1 } } },
            { theme, defaultBodyFill: fill, pageBg: jointPageBackground(theme), depth: 0, isContainer: false },
        );

    it('light: near-white fills on the white page get a visible card + >=3:1 border', () => {
        const page = jointPageBackground('light');
        for (const [fill, stroke] of [['#fafafa', '#dddddd'], ['#ffffff', '#eeeeee'], ['#f5f5f5', '#cccccc']]) {
            // direction: the author fill dissolves into the page (below the 1.35 gate).
            expect(jointContrastRatio(fill, page)).toBeLessThan(1.35);
            const patch: any = call(fill, stroke, 'light');
            expect(patch.body.fill).toBeTruthy();
            // fill now stands off the page as a subtle-but-visible card.
            expect(jointContrastRatio(patch.body.fill, page)).toBeGreaterThanOrEqual(1.5);
            // and the border clears the 3:1 graphical floor.
            expect(jointContrastRatio(patch.body.stroke, page)).toBeGreaterThanOrEqual(3);
        }
    });

    it('dark: near-black fills on the dark page get a visible card + >=3:1 border', () => {
        const page = jointPageBackground('dark');
        for (const [fill, stroke] of [['#1a1a1a', '#333333'], ['#000000', '#222222'], ['#0d1117', '#30363d']]) {
            expect(jointContrastRatio(fill, page)).toBeLessThan(1.35);
            const patch: any = call(fill, stroke, 'dark');
            expect(jointContrastRatio(patch.body.fill, page)).toBeGreaterThanOrEqual(1.5);
            expect(jointContrastRatio(patch.body.stroke, page)).toBeGreaterThanOrEqual(3);
        }
    });

    it('the OTHER theme is untouched — a light fill already stands off the dark page', () => {
        const patch: any = call('#fafafa', '#dddddd', 'dark');
        // #fafafa on #1e1e1e is ~16:1: the gate never fires, fill passes through.
        expect(patch.body.fill).toBe('#fafafa');
    });

    it('a vivid fill that already stands off the page keeps its author stroke', () => {
        const patch: any = call('#c0392b', '#7f0000', 'light');
        expect(jointContrastRatio('#c0392b', '#ffffff')).toBeGreaterThan(1.35);
        expect(patch.body.stroke).toBe('#7f0000');
        expect(patch.body.fill).toBe('#c0392b'); // author fill honoured verbatim, NOT nudged
    });
});
