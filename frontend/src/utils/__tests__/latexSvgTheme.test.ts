/**
 * @jest-environment jsdom
 *
 * Tests for dark-mode recolouring of dvisvgm LaTeX output.
 *
 * The fixture below is REAL dvisvgm 3.6 output (trimmed), not a hand-written
 * approximation.  That matters: the bug existed precisely because the shape of
 * real output differs from what was assumed -- paths carry no stroke, colour
 * sits on ancestor <g>, and text has no fill at all.  A synthetic fixture with
 * fill/stroke on each element would have passed while the product was broken.
 */
import { applyLatexDarkTheme } from '../latexSvgTheme';

/** Trimmed but structurally faithful dvisvgm circuitikz output. */
const DVISVGM_SVG = `
<svg version='1.1' xmlns='http://www.w3.org/2000/svg' width='190pt' height='99pt'>
<defs>
<font id='cmmi10'><font-face font-family='cmmi10'/>
<glyph unicode='R' glyph-name='R' d='M375 614C381 638Z'/>
</font>
</defs>
<style type='text/css'><![CDATA[text.f0 {font-family:cmmi10;font-size:9.9px}]]></style>
<g id='page1'>
<g stroke-miterlimit='10' fill='#000' stroke='#000' stroke-width='0.4'>
  <path d='M0 0L10 0' fill='none'/>
  <path d='M20 0L30 0' fill='none'/>
  <g stroke='none' fill='#000'>
    <text class='f0' x='-63' y='-43'>R</text>
  </g>
  <path d='M40 0L45 5L45 -5Z'/>
  <g stroke-width='0.79999'>
    <path d='M50 0L60 0' fill='none'/>
  </g>
</g>
</g>
</svg>`;

function load(markup: string): SVGElement {
    document.body.innerHTML = `<div>${markup}</div>`;
    return document.querySelector('svg') as unknown as SVGElement;
}

const attrsOf = (svg: SVGElement, attr: 'fill' | 'stroke'): string[] =>
    Array.from(svg.querySelectorAll(`[${attr}]`))
        .map((el) => (el.getAttribute(attr) || '').toLowerCase());

describe('the defect this module exists to fix', () => {
    it('confirms real dvisvgm output puts colour on <g>, not on paths', () => {
        // Pins the assumption the old implementation got wrong.  If dvisvgm
        // ever starts emitting per-path colour, this fails and the rationale
        // in latexSvgTheme.ts needs revisiting.
        const svg = load(DVISVGM_SVG);
        expect(svg.querySelectorAll('path[stroke]')).toHaveLength(0);
        expect(svg.querySelectorAll('text[fill]')).toHaveLength(0);
        expect(svg.querySelectorAll('g[fill="#000"]').length).toBeGreaterThan(0);
    });

    it('leaves no black ink anywhere after recolouring', () => {
        const svg = load(DVISVGM_SVG);
        applyLatexDarkTheme(svg, true);
        const black = ['#000', '#000000', 'black'];
        expect(attrsOf(svg, 'fill').filter((v) => black.includes(v))).toHaveLength(0);
        expect(attrsOf(svg, 'stroke').filter((v) => black.includes(v))).toHaveLength(0);
    });

    it('sets a root-level ink so CSS-styled <text> inherits it', () => {
        // <text> has no fill of its own, so the root default is the only thing
        // that can reach it.
        const svg = load(DVISVGM_SVG);
        applyLatexDarkTheme(svg, true);
        expect(svg.getAttribute('fill')).toBe('#e6e6e6');
        expect(svg.getAttribute('stroke')).toBe('#e6e6e6');
    });
});

describe('regressions the previous implementation caused', () => {
    it("never rewrites fill='none'", () => {
        // dvisvgm marks stroke-only paths fill='none'; recolouring that would
        // flood every circuit body and glyph interior with solid ink.
        const svg = load(DVISVGM_SVG);
        const before = svg.querySelectorAll('path[fill="none"]').length;
        expect(before).toBeGreaterThan(0);
        applyLatexDarkTheme(svg, true);
        expect(svg.querySelectorAll('path[fill="none"]')).toHaveLength(before);
    });

    it('never touches stroke-width (TeX hairlines are deliberate)', () => {
        const svg = load(DVISVGM_SVG);
        const before = Array.from(svg.querySelectorAll('[stroke-width]'))
            .map((el) => el.getAttribute('stroke-width'));
        applyLatexDarkTheme(svg, true);
        const after = Array.from(svg.querySelectorAll('[stroke-width]'))
            .map((el) => el.getAttribute('stroke-width'));
        expect(after).toEqual(before);
        expect(before).toContain('0.4');       // the hairline that was smeared
    });

    it('never adds a stroke to a filled arrowhead', () => {
        // The filled arrowhead path has no stroke by design; the old code gave
        // it stroke='#88c0d0' + width 2, outlining a solid triangle.
        const svg = load(DVISVGM_SVG);
        const head = Array.from(svg.querySelectorAll('path'))
            .find((p) => !p.hasAttribute('fill')) as Element;
        expect(head).toBeDefined();
        applyLatexDarkTheme(svg, true);
        expect(head.hasAttribute('stroke')).toBe(false);
        expect(head.hasAttribute('stroke-width')).toBe(false);
    });

    it('leaves glyph outlines in <defs> alone', () => {
        const svg = load(DVISVGM_SVG);
        applyLatexDarkTheme(svg, true);
        const glyph = svg.querySelector('glyph') as Element;
        expect(glyph.hasAttribute('fill')).toBe(false);
    });
});

describe('authored colours', () => {
    const colored = (c: string) =>
        `<svg xmlns='http://www.w3.org/2000/svg'><g stroke='${c}' fill='none'>` +
        `<path d='M0 0L1 1' fill='none'/></g></svg>`;

    it('lightens a failing colour while preserving its hue', () => {
        // Pure blue measures 1.92:1 on #1f1f1f -- unreadable.  It must survive
        // as blue, not be flattened to the generic ink, because the author used
        // colour to carry meaning.
        const svg = load(colored('#0000ff'));
        applyLatexDarkTheme(svg, true);
        const out = (svg.querySelector('g') as Element).getAttribute('stroke') as string;
        expect(out).not.toBe('#0000ff');
        const [r, g, b] = [1, 3, 5].map((i) => parseInt(out.slice(i, i + 2), 16));
        expect(b).toBeGreaterThan(r);            // still blue-dominant
        expect(r).toEqual(g);                    // hue exactly preserved
    });

    it('leaves an already-legible colour untouched', () => {
        // Red is 4.12:1 on #1f1f1f and needs no help; changing it would be
        // gratuitous drift from what the author wrote.
        const svg = load(colored('#ff0000'));
        applyLatexDarkTheme(svg, true);
        expect((svg.querySelector('g') as Element).getAttribute('stroke')).toBe('#ff0000');
    });

    it('leaves named colours it cannot parse alone rather than guessing', () => {
        const svg = load(colored('darkslategray'));
        applyLatexDarkTheme(svg, true);
        expect((svg.querySelector('g') as Element).getAttribute('stroke')).toBe('darkslategray');
    });
});

describe('light mode', () => {
    it('is a no-op, keeping TeX black on white', () => {
        const svg = load(DVISVGM_SVG);
        const before = svg.outerHTML;
        const result = applyLatexDarkTheme(svg, false);
        expect(result.remapped).toBe(0);
        expect(svg.outerHTML).toBe(before);
    });

    it('tolerates a null element', () => {
        expect(applyLatexDarkTheme(null, true)).toEqual({ remapped: 0 });
    });
});
