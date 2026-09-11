/**
 * G-793d89 / D-090 — auto-layout-geometry-label-desync (drawio wave 1,
 * specs drawio-w1-02, -03, -04, -05, -07, -08, -09).
 *
 * RECORDED FAILURE (legacy): "Node fills are drawn at auto-laid-out, inflated
 * geometry while the LABELS are painted at unrelated positions ... Author
 * mxGeometry (140x50 at fixed x/y) is not honoured." In dark the stranded dark
 * labels land on the bare canvas and collapse to ~1.02:1.
 *
 * TRIAGE said the specs "lack explicit per-cell x/y" so hasExplicitLayout is
 * false and the auto-layout / placement optimizer runs. Reading the specs, that
 * is WRONG: every one of the seven carries explicit author mxGeometry (x/y on
 * each vertex). The operative fix is the explicit-layout GATE — it must classify
 * these diagrams as explicit so the ELK auto-layout, placement optimizer and
 * custom router are all skipped and the author geometry (fill AND label) is
 * honoured. `detectExplicitLayout` is the extracted predicate that gate uses.
 *
 * DIRECTION: `detectExplicitLayout` does not exist on the unpatched tree, so
 * importing it makes this suite RED before the fix and GREEN after. The gate is
 * geometry-based and theme-independent, but it is what prevents BOTH the light
 * overflow and the dark-canvas contrast collapse, so the origins below stand in
 * for the failure in both themes.
 */
import { detectExplicitLayout, shouldAutoSizeCells } from '../drawioPlugin';

// Representative non-root vertex origins taken verbatim from each failing spec's
// mxGeometry. Every spec hand-places its boxes, so the gate MUST return true.
const SPEC_ORIGINS: Record<string, Array<{ x: number; y: number }>> = {
    // five filled boxes at author x/y
    'drawio-w1-02': [
        { x: 40, y: 40 }, { x: 220, y: 40 }, { x: 400, y: 40 },
        { x: 130, y: 150 }, { x: 330, y: 150 },
    ],
    // three swimlanes + their children (children are lane-relative but non-origin)
    'drawio-w1-03': [
        { x: 40, y: 40 }, { x: 60, y: 30 }, { x: 380, y: 30 },
        { x: 40, y: 150 }, { x: 40, y: 260 },
    ],
    // orthogonal edges with explicit waypoints; vertices hand-placed
    'drawio-w1-04': [{ x: 40, y: 40 }, { x: 300, y: 40 }, { x: 300, y: 200 }],
    // built-in shape vocabulary, each shape positioned
    'drawio-w1-05': [{ x: 40, y: 40 }, { x: 200, y: 40 }, { x: 360, y: 40 }],
    // decision flowchart, rhombus + branches
    'drawio-w1-07': [{ x: 120, y: 40 }, { x: 120, y: 160 }, { x: 320, y: 160 }],
    // nested group containers two levels deep
    'drawio-w1-08': [{ x: 40, y: 40 }, { x: 20, y: 40 }, { x: 20, y: 40 }],
    // dashed trust-boundary rectangles enclosing nodes
    'drawio-w1-09': [{ x: 40, y: 40 }, { x: 60, y: 40 }, { x: 360, y: 40 }],
};

describe('detectExplicitLayout — G-793d89/D-090 explicit-layout gate', () => {
    it.each(Object.entries(SPEC_ORIGINS))(
        '%s is classified as explicit layout (auto-layout skipped)',
        (_spec, origins) => {
            expect(detectExplicitLayout(origins)).toBe(true);
        }
    );

    it('fires on ANY non-origin vertex, even if others sit at (0,0)', () => {
        expect(detectExplicitLayout([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 200, y: 0 }])).toBe(true);
        expect(detectExplicitLayout([{ x: 0, y: 0 }, { x: 0, y: 120 }])).toBe(true);
    });

    it('leaves a genuine auto-layout diagram (all vertices at origin) to ELK', () => {
        expect(detectExplicitLayout([{ x: 0, y: 0 }, { x: 0, y: 0 }])).toBe(false);
        expect(detectExplicitLayout([])).toBe(false);
    });
});

/**
 * G-793d89 / D-090 REGRESSION guard. The router/placement gate alone did not
 * prevent the desync from reappearing: maxGraph's `autoSizeCells` resizes each
 * vertex to fit its label at addCell time (140x50 → ~290x100), inflating the
 * FILL while the label anchor stays put — the recorded resweep symptom "boxes
 * at ~290x100 instead of 140x50, author mxGeometry not honoured, labels left of
 * box". `shouldAutoSizeCells` is the predicate the render path now consults to
 * FREEZE author dimensions for explicit-layout diagrams while leaving genuine
 * auto-layout diagrams auto-sizable. It does not exist on the unpatched tree, so
 * importing it makes this block RED pre-fix and GREEN post-fix. The freeze is
 * geometry-based / theme-independent but guards both the light overflow and the
 * dark bare-canvas ~1:1 collapse.
 */
describe('shouldAutoSizeCells — G-793d89/D-090 author-dimension freeze', () => {
    it.each(Object.entries(SPEC_ORIGINS))(
        '%s freezes author box dimensions (autoSizeCells OFF)',
        (_spec, origins) => {
            expect(shouldAutoSizeCells(origins)).toBe(false);
        }
    );

    it('freezes as soon as ANY vertex is hand-placed, even amid (0,0) siblings', () => {
        expect(shouldAutoSizeCells([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 200, y: 0 }])).toBe(false);
        expect(shouldAutoSizeCells([{ x: 0, y: 0 }, { x: 0, y: 120 }])).toBe(false);
    });

    it('keeps autosize for a genuine auto-layout diagram (all at origin / empty)', () => {
        expect(shouldAutoSizeCells([{ x: 0, y: 0 }, { x: 0, y: 0 }])).toBe(true);
        expect(shouldAutoSizeCells([])).toBe(true);
    });

    it('is the exact inverse of detectExplicitLayout on the same origins', () => {
        for (const origins of Object.values(SPEC_ORIGINS)) {
            expect(shouldAutoSizeCells(origins)).toBe(!detectExplicitLayout(origins));
        }
    });
});
