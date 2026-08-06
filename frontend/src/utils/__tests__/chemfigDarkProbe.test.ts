/**
 * @jest-environment jsdom
 *
 * SCRATCH PROBE (iteration 26) -- delete after use.
 *
 * Real dvisvgm 3.6 output for an ALL-BLUE chemfig chain
 * (\chemfig{-[:30,,,,blue]-[:-30,,,,blue]-[:30,,,,blue]-[:-30,,,,blue]}).
 * Every drawn path is #00f (contrast ~1.9:1 on #1f1f1f -- failing); the only
 * #000 is an outer <g> that draws nothing. This stresses the dark-mode path
 * where the ENTIRE ink set must be boosted and there is no black anchor.
 */
import { applyLatexDarkTheme, sizeLatexSvg } from '../latexSvgTheme';

const SVG = `<?xml version='1.0' encoding='UTF-8'?>
<svg version='1.1' xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' width='103.933709pt' height='15.342476pt' viewBox='-70.007476 -70.007477 103.933709 15.342476'>
<g id='page1'>
<g stroke-miterlimit='10' transform='matrix(.996264 0 0 -.996264 -69.808226 -54.864251)' fill='#000' stroke='#000' stroke-width='0.4'>
<g fill='#00f' stroke='#00f'>
<path d='M0 0L25.98087 15.00002' fill='none'/>
</g>
<g fill='#00f' stroke='#00f'>
<path d='M25.98087 15.00002L51.96173 0' fill='none'/>
</g>
<g fill='#00f' stroke='#00f'>
<path d='M51.96173 0L77.9426 15.00002' fill='none'/>
</g>
<g fill='#00f' stroke='#00f'>
<path d='M77.9426 15.00002L103.92346 0' fill='none'/>
</g>
</g>
</g>
</svg>`;

const DARK_BG: [number, number, number] = [0x1f, 0x1f, 0x1f];

function parseHex(v: string): [number, number, number] | null {
    const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(v.trim());
    if (!m) return null;
    let h = m[1];
    if (h.length === 3) h = h.split('').map((c) => c + c).join('');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function lum([r, g, b]: [number, number, number]): number {
    const f = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(a: [number, number, number], b: [number, number, number]): number {
    const la = lum(a); const lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
function hueOf([r, g, b]: [number, number, number]): number {
    const rn = r / 255, gn = g / 255, bn = b / 255;
    const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn), d = max - min;
    if (d === 0) return -1;
    let h: number;
    if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
    else if (max === gn) h = ((bn - rn) / d + 2) / 6;
    else h = ((rn - gn) / d + 4) / 6;
    return h * 360;
}
function load(markup: string): SVGElement {
    document.body.innerHTML = `<div>${markup}</div>`;
    return document.querySelector('svg') as unknown as SVGElement;
}

describe('iteration 26 scratch probe: all-blue chain', () => {
    it('leaves NO fill/stroke below WCAG 3.0 after recolour', () => {
        const svg = load(SVG);
        applyLatexDarkTheme(svg, true);
        const all = [svg, ...Array.from(svg.querySelectorAll('*'))];
        for (const el of all) {
            for (const attr of ['fill', 'stroke'] as const) {
                const v = el.getAttribute(attr);
                if (!v) continue;
                const lv = v.trim().toLowerCase();
                if (lv === 'none' || lv === 'transparent') continue;
                const rgb = parseHex(lv);
                if (!rgb) continue;
                const c = contrast(rgb, DARK_BG);
                if (c < 3.0) throw new Error(`${el.tagName} ${attr}=${v} contrast ${c.toFixed(2)} < 3.0`);
                expect(c).toBeGreaterThanOrEqual(3.0);
            }
        }
    });

    it('preserves blue hue (not flattened to grey ink)', () => {
        const svg = load(SVG);
        applyLatexDarkTheme(svg, true);
        const g = Array.from(svg.querySelectorAll('g')).find((e) => {
            const s = e.getAttribute('stroke'); return s && parseHex(s) && hueOf(parseHex(s) as [number, number, number]) >= 0;
        });
        expect(g).toBeDefined();
        const h = hueOf(parseHex(g!.getAttribute('stroke') as string) as [number, number, number]);
        expect(Math.abs(h - 240)).toBeLessThan(15);
    });

    it('mutates NO stroke-width', () => {
        const svg = load(SVG);
        const before = Array.from(svg.querySelectorAll('[stroke-width]')).map((e) => e.getAttribute('stroke-width'));
        applyLatexDarkTheme(svg, true);
        const after = Array.from(svg.querySelectorAll('[stroke-width]')).map((e) => e.getAttribute('stroke-width'));
        expect(after).toEqual(before);
    });

    it('sizes to intrinsic width, not full column', () => {
        const svg = load(SVG);
        const { widthPx } = sizeLatexSvg(svg);
        const expected = Math.round(103.933709 * 96 / 72);
        expect(widthPx).not.toBeNull();
        expect(Math.abs((widthPx as number) - expected)).toBeLessThanOrEqual(1);
        expect((svg as unknown as HTMLElement).style.maxWidth).toBe('100%');
        expect((svg as unknown as HTMLElement).style.height).toBe('auto');
    });
});
