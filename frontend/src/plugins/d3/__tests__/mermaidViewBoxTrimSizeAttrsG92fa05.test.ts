/**
 * @jest-environment jsdom
 *
 * G-92fa05 / D-288 / D-291 residuals — oversize-canvas-content-shrunk-labels-subpixel.
 *
 * Both defects' ORIGINAL root causes (gantt `fill:currentColor` parse collapse,
 * sequence alt/else + state <<choice>> semantics) are resolved: their other
 * specs (mermaid-w1-07, w4-13, w1-03) pass both themes. The residual failing
 * specs (mermaid-w2-14, w1-05) now fail under the oversize-canvas SIZING
 * signature: mermaid over-allocates the viewBox and the content collapses to a
 * sub-pixel sliver on capture.
 *
 * The VIEWBOX-TRIM driver reclaimed the excess viewBox but rewrote ONLY the
 * `viewBox` attribute, leaving any explicit `width`/`height` ATTRIBUTES at
 * their original oversize values — so a stale size attribute could re-inflate
 * the captured canvas (the sibling pie-layout fix in this file already keeps
 * width/height "in step so a fixed size attr cannot override the viewBox").
 * applyViewBoxTrim now rescales the numeric width/height attributes by the same
 * trim factor.
 *
 * DIRECTION: on the pre-fix tree only the viewBox was updated, so the width/
 * height assertions below fail (the attributes stay at 320/2000). With the fix
 * they are brought in step with the trimmed viewBox.
 */
import { applyViewBoxTrim, computeViewBoxTrim } from '../mermaidPlugin';

/** Build a bare SVG and force getBBox() (jsdom returns nothing otherwise). */
function makeSvg(
    viewBox: string,
    attrs: Record<string, string>,
    bbox: { x: number; y: number; width: number; height: number },
): SVGElement {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', viewBox);
    for (const [k, v] of Object.entries(attrs)) svg.setAttribute(k, v);
    (svg as unknown as { getBBox: () => typeof bbox }).getBBox = () => bbox;
    return svg as unknown as SVGElement;
}

describe('G-92fa05 applyViewBoxTrim keeps width/height attrs in step with the trimmed viewBox', () => {
    it('rescales numeric width/height attributes when the oversize-HEIGHT viewBox is trimmed (w1-05 shape)', () => {
        // Content 300x180 in a 320x2000 canvas — the oversize-height case.
        const svg = makeSvg(
            '0 0 320 2000',
            { width: '320', height: '2000' },
            { x: 0, y: 0, width: 300, height: 180 },
        );

        const trim = applyViewBoxTrim(svg);
        expect(trim.shouldTrim).toBe(true);

        // viewBox reclaimed (existing behaviour).
        expect(svg.getAttribute('viewBox')).toBe('-16 -16 332 212');
        // NEW: width/height attributes brought in step (pre-fix they stayed 320/2000).
        expect(svg.getAttribute('width')).toBe('332');
        expect(svg.getAttribute('height')).toBe('212');
    });

    it('rescales for the oversize-WIDTH case too (w2-14 wide-gantt shape)', () => {
        const svg = makeSvg(
            '0 0 2000 220',
            { width: '2000', height: '220' },
            { x: 0, y: 0, width: 200, height: 180 },
        );

        const trim = applyViewBoxTrim(svg);
        expect(trim.shouldTrim).toBe(true);
        expect(svg.getAttribute('viewBox')).toBe('-16 -16 232 212');
        expect(svg.getAttribute('width')).toBe('232');
        expect(svg.getAttribute('height')).toBe('212');
    });

    it('leaves percentage/responsive size attributes untouched', () => {
        const svg = makeSvg(
            '0 0 320 2000',
            { width: '100%' },
            { x: 0, y: 0, width: 300, height: 180 },
        );
        applyViewBoxTrim(svg);
        // viewBox still trimmed, but the responsive width is preserved.
        expect(svg.getAttribute('viewBox')).toBe('-16 -16 332 212');
        expect(svg.getAttribute('width')).toBe('100%');
    });

    it('is a no-op when the viewBox already fits the content', () => {
        const svg = makeSvg(
            '0 0 340 240',
            { width: '340', height: '240' },
            { x: 0, y: 0, width: 300, height: 200 },
        );
        const trim = applyViewBoxTrim(svg);
        expect(trim.shouldTrim).toBe(false);
        expect(svg.getAttribute('viewBox')).toBe('0 0 340 240');
        expect(svg.getAttribute('width')).toBe('340');
        expect(svg.getAttribute('height')).toBe('240');
    });

    it('computeViewBoxTrim exposes the trimmed content dimensions', () => {
        const r = computeViewBoxTrim('0 0 320 2000', { x: 0, y: 0, width: 300, height: 180 });
        expect(r.trimmedWidth).toBe(332);
        expect(r.trimmedHeight).toBe(212);
    });
});
