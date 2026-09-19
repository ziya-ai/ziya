/**
 * @jest-environment jsdom
 *
 * G-d34f80 — two mermaidEnhancer defects sharing the file:
 *
 *  - D-423 (sequence w3-06, THEME): `box <color> <title>` paints its title on
 *    the author-hardcoded box fill, but the title inherits theme ink (not a
 *    colour derived from the fill). A LIGHT box fill (rgb(200,220,255)) is
 *    illegible under light ink; a DARK box fill (rgb(60,60,60)) is illegible
 *    under dark ink — so one of a light+dark pair fails in EACH page theme.
 *    `ensureSequenceBoxTitleContrast` resolves the best black/white ink FROM
 *    the fixed fill, correcting BOTH themes in one pass. Verified colours:
 *    light box -> #000000 (15.14:1), dark box -> #ffffff (11.03:1).
 *
 *  - D-426 (flowchart w4-11, RECOVERY): an unclosed OUTER subgraph must be
 *    closed where its body ends (by indentation), not blindly at EOF, so a
 *    node authored after the inner `end` and dedented to the outer header's
 *    column stays EXTERNAL rather than being swallowed into the outer cluster.
 *
 * DIRECTION: each suite fails against the pre-fix code (title left at theme ink;
 * `balanceSubgraphEnds` appending the missing `end` at EOF) and passes with it.
 */
import {
    ensureSequenceBoxTitleContrast,
    balanceSubgraphEnds,
    resolveStyleColorToRgb,
    contrastRatioRgb,
} from '../mermaidEnhancer';

const SVGNS = 'http://www.w3.org/2000/svg';

function svgRoot(): SVGSVGElement {
    return document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
}

/** Build a sequence-box `<g>` = <rect class="rect" fill=..> + <text class="text"> */
function addBox(svg: Element, boxFill: string, titleInk: string): SVGTextElement {
    const g = document.createElementNS(SVGNS, 'g');
    const rect = document.createElementNS(SVGNS, 'rect');
    rect.setAttribute('class', 'rect');
    rect.setAttribute('fill', boxFill);
    const text = document.createElementNS(SVGNS, 'text') as SVGTextElement;
    text.setAttribute('class', 'text');
    text.setAttribute('fill', titleInk);
    text.textContent = 'Zone';
    g.appendChild(rect);
    g.appendChild(text);
    svg.appendChild(g);
    return text;
}

function ink(el: Element): { r: number; g: number; b: number } | null {
    const attr = el.getAttribute('fill');
    if (attr) return resolveStyleColorToRgb(attr);
    const m = (el.getAttribute('style') || '').match(/fill\s*:\s*([^;!]+)/i);
    return m ? resolveStyleColorToRgb(m[1].trim()) : null;
}

describe('ensureSequenceBoxTitleContrast (D-423, w3-06)', () => {
    const LIGHT_FILL = { r: 200, g: 220, b: 255 }; // rgb(200,220,255)
    const DARK_FILL = { r: 60, g: 60, b: 60 };      // rgb(60,60,60)

    it('LIGHT theme: light-ink title on the DARK box is illegible pre-fix and repaired', () => {
        // Light theme uses dark ink by default, which reads on the light box but
        // NOT on the dark box.
        const svg = svgRoot();
        const lightBoxTitle = addBox(svg, 'rgb(200,220,255)', '#333333'); // dark ink, OK on light box
        const darkBoxTitle = addBox(svg, 'rgb(60,60,60)', '#333333');     // dark ink, FAILS on dark box

        // pre-condition: dark box title is illegible on its fill
        expect(contrastRatioRgb(ink(darkBoxTitle)!, DARK_FILL)).toBeLessThan(4.5);

        const n = ensureSequenceBoxTitleContrast(svg);
        expect(n).toBe(1); // only the dark box needed fixing

        // dark box title now legible; resolved to white from the fill
        expect(contrastRatioRgb(ink(darkBoxTitle)!, DARK_FILL)).toBeGreaterThanOrEqual(4.5);
        expect(ink(darkBoxTitle)).toEqual({ r: 255, g: 255, b: 255 });
        // light box title (already legible) is untouched
        expect(ink(lightBoxTitle)).toEqual({ r: 0x33, g: 0x33, b: 0x33 });
    });

    it('DARK theme: light-ink title on the LIGHT box is illegible pre-fix and repaired', () => {
        // Dark theme uses light ink by default, which reads on the dark box but
        // NOT on the light box.
        const svg = svgRoot();
        const lightBoxTitle = addBox(svg, 'rgb(200,220,255)', '#eceff4'); // light ink, FAILS on light box
        const darkBoxTitle = addBox(svg, 'rgb(60,60,60)', '#eceff4');     // light ink, OK on dark box

        expect(contrastRatioRgb(ink(lightBoxTitle)!, LIGHT_FILL)).toBeLessThan(4.5);

        const n = ensureSequenceBoxTitleContrast(svg);
        expect(n).toBe(1);

        expect(contrastRatioRgb(ink(lightBoxTitle)!, LIGHT_FILL)).toBeGreaterThanOrEqual(4.5);
        expect(ink(lightBoxTitle)).toEqual({ r: 0, g: 0, b: 0 }); // black on light box
        expect(ink(darkBoxTitle)).toEqual({ r: 0xec, g: 0xef, b: 0xf4 }); // untouched
    });

    it('leaves an unresolvable / transparent box fill alone', () => {
        const svg = svgRoot();
        const t = addBox(svg, 'transparent', '#eceff4');
        const n = ensureSequenceBoxTitleContrast(svg);
        expect(n).toBe(0);
        expect(ink(t)).toEqual({ r: 0xec, g: 0xef, b: 0xf4 });
    });
});

describe('balanceSubgraphEnds (D-426, w4-11)', () => {
    it('closes the outer subgraph where its body ends, keeping later nodes external', () => {
        const def = [
            'flowchart TD',
            '  subgraph Outer[Outer boundary]',
            '    subgraph Inner[Inner boundary]',
            '      A[Task A] --> B[Task B]',
            '    end',
            '  A --> C[Task C]',
            '  D[Outside] --> A',
        ].join('\n');

        const out = balanceSubgraphEnds(def);
        const lines = out.split('\n');

        // one `end` added (opens 2, closes 1 originally)
        const endCount = lines.filter((l) => l.trim() === 'end').length;
        expect(endCount).toBe(2);

        // The Outer `end` must appear BEFORE `A --> C` / `D[Outside]`, so those
        // nodes are external. Pre-fix the missing end was appended at EOF.
        const outerEndIdx = lines.findIndex(
            (l, i) => l.trim() === 'end' && i > 4, // the second/added end
        );
        const taskCIdx = lines.findIndex((l) => l.includes('Task C'));
        const outsideIdx = lines.findIndex((l) => l.includes('Outside'));
        expect(outerEndIdx).toBeGreaterThan(0);
        expect(outerEndIdx).toBeLessThan(taskCIdx);
        expect(outerEndIdx).toBeLessThan(outsideIdx);

        // NOT appended at EOF (the last line is the external edge, not `end`)
        expect(lines[lines.length - 1].trim()).not.toBe('end');
    });

    it('is a no-op when subgraphs are already balanced', () => {
        const def = 'flowchart TD\n  subgraph S\n    A --> B\n  end';
        expect(balanceSubgraphEnds(def)).toBe(def);
    });

    it('falls back to EOF append when the body never dedents', () => {
        const def = 'flowchart TD\n  subgraph S\n    A --> B';
        const out = balanceSubgraphEnds(def);
        expect(out.split('\n').pop()!.trim()).toBe('end');
        expect(out.split('\n').filter((l) => l.trim() === 'end').length).toBe(1);
    });
});
