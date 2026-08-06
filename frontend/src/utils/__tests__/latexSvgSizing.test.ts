/**
 * @jest-environment jsdom
 *
 * Tests for intrinsic sizing of dvisvgm LaTeX output.
 *
 * The bug: `latexPlugin.mount()` removed BOTH `width` and `height` from the
 * dvisvgm root, keeping only the viewBox.  An SVG with no width and a viewBox
 * defaults to `width: 100%` of its container, so every diagram was stretched
 * to the full chat column regardless of its natural size.  Measured against an
 * ~820px column, a benzene ring (52.2 x 60.2 pt = 70 x 80 px) came out at an
 * 11.8x upscale -- which is why the symptom read as "renders as a huge image".
 *
 * The fixtures use REAL dvisvgm dimensions rather than round numbers, so a
 * regression in unit conversion cannot hide behind a convenient value.
 */
import { sizeLatexSvg } from '../latexSvgTheme';

/** Real dvisvgm 3.6 root attributes for `\chemfig{*6(-=-=-=)}`. */
const BENZENE_WIDTH = '52.166097pt';
const BENZENE_HEIGHT = '60.174416pt';
const BENZENE_VIEWBOX = '-70.007476 -70.007476 52.166097 60.174416';

function load(attrs: string): SVGElement {
    document.body.innerHTML =
        `<div><svg xmlns='http://www.w3.org/2000/svg' ${attrs}>` +
        `<path d='M0 0L10 10'/></svg></div>`;
    return document.querySelector('svg') as unknown as SVGElement;
}

const benzene = (): SVGElement => load(
    `width='${BENZENE_WIDTH}' height='${BENZENE_HEIGHT}' viewBox='${BENZENE_VIEWBOX}'`,
);

describe('the defect: diagrams stretched to the container', () => {
    it('sizes a small diagram to its intrinsic width, not 100%', () => {
        const svg = benzene();
        const { widthPx } = sizeLatexSvg(svg);
        // 52.166097pt x 96/72 = 69.55 -> 70px.
        expect(widthPx).toBe(70);
        expect(svg.style.width).toBe('70px');
        expect(svg.style.width).not.toBe('100%');
    });

    it('leaves no absolute width/height attributes to fight the CSS', () => {
        // Both an attribute and a style would otherwise disagree about size.
        const svg = benzene();
        sizeLatexSvg(svg);
        expect(svg.getAttribute('width')).toBeNull();
        expect(svg.getAttribute('height')).toBeNull();
    });

    it('still allows shrink on a narrow viewport', () => {
        // The intrinsic size is an upper bound, not a fixed size: a bare
        // absolute width would overflow, which is what the original
        // removeAttribute calls were guarding against.
        const svg = benzene();
        sizeLatexSvg(svg);
        expect(svg.style.maxWidth).toBe('100%');
        expect(svg.style.height).toBe('auto');
    });

    it('keeps the viewBox, without which the element cannot scale at all', () => {
        const svg = benzene();
        sizeLatexSvg(svg);
        expect(svg.getAttribute('viewBox')).toBe(BENZENE_VIEWBOX);
    });

    it('centres the geometry if given a mismatched box', () => {
        const svg = benzene();
        sizeLatexSvg(svg);
        expect(svg.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet');
    });
});

describe('unit handling', () => {
    it.each([
        ['1in', 96],
        ['2.54cm', 96],
        ['25.4mm', 96],
        ['6pc', 96],
        ['72pt', 96],
        ['96px', 96],
    ])('converts %s to %ipx', (width, expected) => {
        const svg = load(`width='${width}' height='${width}' viewBox='0 0 10 10'`);
        expect(sizeLatexSvg(svg).widthPx).toBe(expected);
    });

    it('treats a bare number as px, per the SVG spec', () => {
        const svg = load(`width='96' height='96' viewBox='0 0 10 10'`);
        expect(sizeLatexSvg(svg).widthPx).toBe(96);
    });
});

describe('inputs with no intrinsic size', () => {
    it('falls back to full width for a percentage width', () => {
        // Already container-relative: there is no intrinsic size to recover,
        // so guessing one would be worse than the previous behaviour.
        const svg = load(`width='100%' height='100%' viewBox='0 0 10 10'`);
        expect(sizeLatexSvg(svg).widthPx).toBeNull();
        expect(svg.style.width).toBe('100%');
    });

    it('falls back to full width when width is absent', () => {
        const svg = load(`viewBox='0 0 10 10'`);
        expect(sizeLatexSvg(svg).widthPx).toBeNull();
        expect(svg.style.width).toBe('100%');
    });

    it.each(['0pt', '-5pt', 'abc', ''])(
        'rejects the nonsensical width %p rather than computing from it', (w) => {
            const svg = load(`width='${w}' height='10pt' viewBox='0 0 10 10'`);
            expect(sizeLatexSvg(svg).widthPx).toBeNull();
        });

    it('synthesizes a viewBox from absolute dimensions when one is missing', () => {
        // Without a viewBox the element cannot scale, so max-width:100% would
        // clip rather than shrink.
        const svg = load(`width='100pt' height='50pt'`);
        sizeLatexSvg(svg);
        expect(svg.getAttribute('viewBox')).toBe('0 0 100 50');
    });

    it('does not invent a viewBox it cannot derive', () => {
        const svg = load(`width='100%'`);
        sizeLatexSvg(svg);
        expect(svg.getAttribute('viewBox')).toBeNull();
    });

    it('tolerates a null element', () => {
        expect(sizeLatexSvg(null)).toEqual({ widthPx: null });
    });
});

describe('geometry is never touched', () => {
    it('leaves inner elements and stroke widths alone', () => {
        // The companion defect: enhanceSVGVisibility force-set stroke-width=2
        // over dvisvgm's inherited 0.4, a 5x thickening stacked on top of the
        // 11.8x upscale.  Sizing must not reintroduce that.
        document.body.innerHTML = `
            <div><svg xmlns='http://www.w3.org/2000/svg'
                      width='190pt' height='99pt' viewBox='0 0 190 99'>
              <g stroke-width='0.4'><path d='M0 0L10 0' fill='none'/></g>
            </svg></div>`;
        const svg = document.querySelector('svg') as unknown as SVGElement;
        sizeLatexSvg(svg);
        expect(svg.querySelector('g')!.getAttribute('stroke-width')).toBe('0.4');
        expect(svg.querySelector('path')!.getAttribute('stroke-width')).toBeNull();
        expect(svg.querySelector('path')!.getAttribute('d')).toBe('M0 0L10 0');
    });
});

describe('the measured regression cases', () => {
    it.each([
        ['benzene ring', '52.166097pt', 70],
        ['EAS scheme', '185.3pt', 247],
        ['Fischer esterification', '443pt', 591],
    ])('%s sizes to %s = %ipx rather than the full column', (_n, width, px) => {
        const svg = load(`width='${width}' height='63pt' viewBox='0 0 10 10'`);
        expect(sizeLatexSvg(svg).widthPx).toBe(px);
    });
});
