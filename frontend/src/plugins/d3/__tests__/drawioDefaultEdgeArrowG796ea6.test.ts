/**
 * G-796ea6 / D-380 — arrowheads-missing-on-default-edges (drawio).
 *
 * drawio's implicit edge terminator is `classic` (mxConstants.ARROW_CLASSIC): an
 * edge whose style omits `endArrow` still renders a solid arrowhead. maxGraph 0.23
 * does NOT default `endArrow` (StyleDefaultsConfig has no such key — verified
 * headlessly), so the head only appears when it is set on the cell style or
 * inherited from the stylesheet default-edge merge.
 *
 * Two source divergences dropped/thinned the head:
 *   1. The plugin injected `classicThin` (createArrow widthFactor 3) for unstyled
 *      `html=1` edges — 1.5x narrower than drawio's `classic` (widthFactor 2), and
 *      prone to sub-pixel collapse on fit-DOWNscaled tall diagrams (drawio-w2-10 is
 *      180x1760). classicThin was NOT drawio's default.
 *   2. Edges with NO style string got an empty style object and relied purely on
 *      the stylesheet default-edge merge for their head.
 *
 * These tests import the REAL exported helper `resolveDefaultEdgeArrow` from
 * drawioPlugin. It does NOT exist on the pre-fix tree, so the file fails to resolve
 * (RED before the change); with the fix it resolves an unstyled edge's terminator
 * to `classic` and preserves any author endArrow.
 *
 * THEME: terminator geometry strokes/fills in the EDGE colour, so the resolved
 * `endArrow` value is identical in light and dark — the helper takes no theme and
 * one assertion set covers both; pixel sufficiency is the render-stage check.
 *
 * @jest-environment jsdom
 */

import { resolveDefaultEdgeArrow } from '../drawioPlugin';

describe('D-380: resolveDefaultEdgeArrow gives unstyled edges drawio\'s default classic head', () => {
    it('an edge style with NO endArrow resolves to `classic` (was `classicThin`, the bug)', () => {
        // drawio-w1-01 / w1-12: `html=1;` and orthogonal edges omit endArrow.
        const s1 = resolveDefaultEdgeArrow({ html: 1 });
        expect(s1['endArrow']).toBe('classic');
        // The regression the fix removes: the previously-injected value must NOT survive.
        expect(s1['endArrow']).not.toBe('classicThin');

        const s2 = resolveDefaultEdgeArrow({ html: 1, edgeStyle: 'orthogonalEdgeStyle' });
        expect(s2['endArrow']).toBe('classic');
    });

    it('an empty edge style object (no style string) is given `classic` explicitly', () => {
        // drawio-w2-10 / w2-15 / w3-05: edges carry NO style attribute at all.
        // They must not depend solely on the stylesheet default-edge merge.
        const s = resolveDefaultEdgeArrow({ endSize: 3 });
        expect(s['endArrow']).toBe('classic');
    });

    it('an AUTHOR-specified endArrow always wins (block/open/oval/diamond/ERmany/none)', () => {
        // drawio-w1-10: the marker vocabulary must be preserved untouched.
        for (const marker of ['block', 'open', 'oval', 'diamond', 'ERmany', 'none']) {
            const s = resolveDefaultEdgeArrow({ html: 1, endArrow: marker });
            expect(s['endArrow']).toBe(marker);
        }
    });

    it('an empty-string endArrow is treated as unset and resolves to `classic`', () => {
        expect(resolveDefaultEdgeArrow({ endArrow: '' })['endArrow']).toBe('classic');
    });

    it('is theme-independent: same resolved head regardless of caller theme', () => {
        // The helper takes no theme; prove the value does not vary by constructing
        // two identical inputs (mirrors light/dark render passes on the same edge).
        const light = resolveDefaultEdgeArrow({ html: 1 })['endArrow'];
        const dark = resolveDefaultEdgeArrow({ html: 1 })['endArrow'];
        expect(light).toBe('classic');
        expect(dark).toBe('classic');
        expect(light).toBe(dark);
    });

    it('never throws on a malformed style object', () => {
        expect(resolveDefaultEdgeArrow(null as any)).toBeNull();
        expect(resolveDefaultEdgeArrow(undefined as any)).toBeUndefined();
    });
});
