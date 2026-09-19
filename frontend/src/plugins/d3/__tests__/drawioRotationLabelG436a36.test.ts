/**
 * @jest-environment jsdom
 *
 * G-436a36 / D-385 — rotation-style-string-breaks-svg-transform (drawio-w3-06).
 *
 * A cell authored with `rotation=NN` is drawn by maxGraph inside a group whose
 * transform carries `rotate(θ, cx, cy)`, and maxGraph ALREADY positions the
 * label (foreignObject) correctly within that rotated frame using CSS
 * margin-left / padding-top. The foreignObject-positioning pass
 * (DrawIOEnhancer.fixAllForeignObjects) then (a) converts those CSS offsets to
 * SVG x/y additions and (b) clamps margin-left from the label's axis-aligned
 * getBoundingClientRect. Both assume an UN-rotated frame, so for a rotated cell
 * they mirror/drop the label (drawio-w3-06 dark label -> 1.27:1).
 *
 * FIX: leave maxGraph's native placement alone for a rotated cell. The pure
 * `transformChainHasRotation` / DOM `elementHasRotatedAncestor` detect a real
 * rotation in the label's ancestor chain, and the loop skips the
 * reposition/clamp for that label. Neither helper exists on the unpatched tree,
 * so importing them makes this suite RED before the fix and GREEN after.
 *
 * Structural / theme-independent: not repositioning the label is correct in
 * both light and dark, which is what removes the dark ~1.27 collapse — so the
 * behaviour holds for BOTH themes (there is no colour branch to diverge on).
 */
import {
    transformChainHasRotation,
    elementHasRotatedAncestor,
    DrawIOEnhancer,
} from '../drawioEnhancer';

const SVGNS = 'http://www.w3.org/2000/svg';

describe('transformChainHasRotation — pure rotation detection', () => {
    it('detects a genuine rotation anywhere in the chain', () => {
        expect(transformChainHasRotation(['rotate(30)'])).toBe(true);
        expect(transformChainHasRotation(['rotate(-45,60,20)'])).toBe(true);
        expect(transformChainHasRotation(['scale(2)', 'translate(4,5) rotate(90)'])).toBe(true);
    });

    it('treats rotate(0) / multiples of 360 as NOT a rotation (no-op transform)', () => {
        expect(transformChainHasRotation(['rotate(0)'])).toBe(false);
        expect(transformChainHasRotation(['rotate(360, 1, 2)'])).toBe(false);
        expect(transformChainHasRotation(['rotate(-720)'])).toBe(false);
    });

    it('is false for scale/translate-only or empty chains', () => {
        expect(transformChainHasRotation(['scale(1.7)'])).toBe(false);
        expect(transformChainHasRotation(['translate(10,20)'])).toBe(false);
        expect(transformChainHasRotation([null, undefined, ''])).toBe(false);
        expect(transformChainHasRotation([])).toBe(false);
    });
});

describe('elementHasRotatedAncestor — walks ancestor transforms up to <svg>', () => {
    it('finds a rotate on an ancestor group', () => {
        const svg = document.createElementNS(SVGNS, 'svg');
        const g = document.createElementNS(SVGNS, 'g');
        g.setAttribute('transform', 'rotate(30,60,130)');
        const fo = document.createElementNS(SVGNS, 'foreignObject');
        g.appendChild(fo);
        svg.appendChild(g);
        expect(elementHasRotatedAncestor(fo)).toBe(true);
    });

    it('is false when only scale transforms are present', () => {
        const svg = document.createElementNS(SVGNS, 'svg');
        const g = document.createElementNS(SVGNS, 'g');
        g.setAttribute('transform', 'scale(2)');
        const fo = document.createElementNS(SVGNS, 'foreignObject');
        g.appendChild(fo);
        svg.appendChild(g);
        expect(elementHasRotatedAncestor(fo)).toBe(false);
    });
});

// Build an <svg> holding one foreignObject (with a label div that carries a
// margin-left, exactly as maxGraph emits) inside a cell group. The group's
// transform is parameterised so we can compare a rotated cell against an
// otherwise-identical unrotated control.
function buildFO(groupTransform: string): { svg: SVGSVGElement; fo: SVGForeignObjectElement } {
    const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('transform', groupTransform);
    const fo = document.createElementNS(SVGNS, 'foreignObject') as SVGForeignObjectElement;
    fo.setAttribute('x', '20');
    fo.setAttribute('y', '110');
    fo.setAttribute('width', '120');
    fo.setAttribute('height', '40');
    const div = document.createElement('div');
    // maxGraph's label div: a real margin-left offset the old path would move
    // into SVG x and then zero.
    div.setAttribute('style', 'margin-left: 50px; padding-top: 0px;');
    div.textContent = 'Rotated 30';
    fo.appendChild(div);
    g.appendChild(fo);
    svg.appendChild(g);
    return { svg, fo };
}

describe('fixAllForeignObjects — D-385 leaves a rotated label untouched', () => {
    it('CONTROL: an UNrotated label IS repositioned (margin-left folded into x)', () => {
        const { svg, fo } = buildFO('scale(1)');
        DrawIOEnhancer.fixAllForeignObjects(svg);
        // The old CSS->SVG conversion runs: x gains the 50px margin-left.
        expect(parseFloat(fo.getAttribute('x') || '0')).toBe(70);
        expect((fo.querySelector('div') as HTMLDivElement).style.marginLeft).toBe('0px');
    });

    it('ROTATED: the label is NOT repositioned — x and margin-left are preserved', () => {
        const { svg, fo } = buildFO('rotate(30,80,130)');
        DrawIOEnhancer.fixAllForeignObjects(svg);
        // Guard fires: maxGraph's native rotated placement is left intact.
        expect(parseFloat(fo.getAttribute('x') || '0')).toBe(20);
        const style = (fo.querySelector('div') as HTMLDivElement).getAttribute('style') || '';
        expect(style).toMatch(/margin-left:\s*50px/);
        expect(fo.getAttribute('data-force-positioned')).toBe('true');
    });

    it('ROTATED both themes: placement is identical (no colour/theme branch)', () => {
        // The guard is geometry-only, so a rotated label is treated the same
        // regardless of theme — asserting the invariant that makes the dark
        // 1.27 collapse impossible for the same reason light is unaffected.
        for (const transform of ['rotate(30,80,130)', 'rotate(-45,80,130)']) {
            const { svg, fo } = buildFO(transform);
            DrawIOEnhancer.fixAllForeignObjects(svg);
            expect(parseFloat(fo.getAttribute('x') || '0')).toBe(20);
        }
    });
});
