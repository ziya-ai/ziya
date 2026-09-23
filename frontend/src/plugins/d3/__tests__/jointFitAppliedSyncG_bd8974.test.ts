/**
 * @jest-environment jsdom
 *
 * G-bd8974 — the joint fit frame must span the FULL content bbox, and the fit
 * path must apply it synchronously (D-129 content-cropped, D-404 viewbox-fit-clip).
 *
 * Root cause of the recurring joint regressions: the fit-plan pure functions
 * (computeJointFitPlan / computeJointResizeFit) are correct and unit-tested, but
 * jointPlugin.render applied the resulting content-framing viewBox ONLY inside
 * `setTimeout(fitContentToPaper, 300)`. DiagramRenderPage's headless completion
 * detector snapshots the DOM 500ms after the FIRST <svg> mutation — an
 * independent, uncoupled timer. A heavy graph (100-node / 500-cell / long
 * auto-layout chain — exactly the D-129/D-404 failing specs) whose synchronous
 * layout + first paint starve the main thread can have its capture fire before
 * the naked 300ms timer runs, so the PNG is taken with the paper's initial
 * container-sized viewBox: content cropped (D-129) or mis-framed (D-404). Small
 * graphs fit inside the 200ms margin, which is why they verified while the big
 * specs regressed.
 *
 * The fix (a) extracts the DOM frame mutation into applyJointContentFrame and
 * (b) calls fitContentToPaper() inline immediately after layout (the paper is
 * synchronous, so graph.getBBox() is valid at once), keeping the 300ms re-run as
 * an idempotent belt-and-suspenders pass.
 *
 * A full jointPlugin.render cannot run under jsdom (JointJS dia.Paper touches
 * SVG classList internals jsdom does not implement), so the render-order is
 * covered by asserting the extracted frame helper on a real <svg> element: it
 * frames the full content extent (never the container width) and sets
 * preserveAspectRatio 'meet'. Direction is asserted explicitly against the
 * pre-fix container-pinned form.
 */
import {
    applyJointContentFrame,
    computeJointResizeFit,
} from '../jointPlugin';

function makeSvg(): SVGSVGElement {
    return document.createElementNS('http://www.w3.org/2000/svg', 'svg') as SVGSVGElement;
}

describe('G-bd8974 — applyJointContentFrame frames the full content bbox, not the container', () => {
    it('an oversized wide grid gets a viewBox spanning the whole content extent (not clipped to container width)', () => {
        const svg = makeSvg();
        // A 4x10 DirectedGraph grid: content ~1800 wide x 700 tall, laid out from
        // origin. The container was only ~1264 wide — the pre-fix bug pinned the
        // viewBox to `0 0 1264 h`, cropping the right two columns (joint-w2-06 /
        // D-129 / D-404).
        const bbox = { x: 0, y: 0, width: 1800, height: 700 };
        const padding = 40;
        const vb = applyJointContentFrame(svg, bbox, padding);

        expect(vb).not.toBeNull();
        // viewBox WIDTH must be the content width + 2*padding, NOT the container width.
        expect(vb!.width).toBe(1800 + padding * 2);
        expect(vb!.height).toBe(700 + padding * 2);
        expect(vb!.x).toBe(-padding);
        expect(vb!.y).toBe(-padding);

        const attr = svg.getAttribute('viewBox');
        expect(attr).toBe(`${-padding} ${-padding} ${1800 + padding * 2} ${700 + padding * 2}`);
        // Pre-fix container-pinned form would have started at 0 and been 1264 wide.
        expect(attr!.startsWith('0 0 ')).toBe(false);
        expect(svg.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
    });

    it('a negative-origin content bbox keeps the origin (labels above/left of node 0 stay in frame)', () => {
        const svg = makeSvg();
        // Auto-layout can place content at negative coordinates; the frame origin
        // must follow the bbox, not clamp to 0 (D-404 empty-band / off-canvas nodes).
        const bbox = { x: -120, y: -600, width: 500, height: 1600 };
        const vb = applyJointContentFrame(svg, bbox, 40);
        expect(vb!.x).toBe(-160);
        expect(vb!.y).toBe(-640);
        expect(vb!.width).toBe(580);
        expect(vb!.height).toBe(1680);
    });

    it('a null svg is a no-op (never throws when the paper failed to mount)', () => {
        expect(applyJointContentFrame(null, { x: 0, y: 0, width: 10, height: 10 })).toBeNull();
    });
});

describe('G-bd8974 — the resize fit frames the full content bbox too (both paths agree)', () => {
    it('computeJointResizeFit viewBox spans the content extent, not the container width', () => {
        // Content 1800x700 into a 1264-wide container: the viewBox must be the
        // content box (plus padding), never `0 0 1264 h`.
        const fit = computeJointResizeFit({ x: 0, y: 0, width: 1800, height: 700 }, 1264, 2000, 40);
        expect(fit.viewBox.width).toBe(1800 + 80);
        expect(fit.viewBox.height).toBe(700 + 80);
        expect(fit.viewBox.x).toBe(-40);
        // paper is bounded to the container (downscaled), proving content is scaled
        // to fit rather than cropped.
        expect(fit.paperWidth).toBeLessThanOrEqual(1264);
    });
});
