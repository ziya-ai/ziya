/**
 * @jest-environment jsdom
 *
 * Mermaid effective-font scaling: anchor on the SMALLEST legible text.
 *
 * The old applyEffectiveFontScaling sized the SVG so its LARGEST declared font
 * rendered at TARGET_FONT_SIZE (14px). For diagrams whose largest text is a
 * title (timeline, gantt, journey) that left every body label proportionally
 * smaller and the diagram narrow and centred in empty space — one number, two
 * symptoms. The policy is now (chooseEffectiveWidth):
 *   1. container width is absolute;
 *   2. the smallest visible text aims for TARGET (14px) and never drops below
 *      MIN (12px);
 *   3. the largest text is preferred at or under MAX (18px), honoured only
 *      while rule 2 holds;
 *   4. never below the 100px floor.
 * and hidden / degenerate text (measurement probes) must not anchor rule 2.
 *
 * Against the unpatched code: `chooseEffectiveWidth` does not exist (import
 * fails), and the DOM tests report the old largest-font widths.
 */
import { applyEffectiveFontScaling, chooseEffectiveWidth } from '../mermaidPlugin';

const CONTAINER_W = 1200;
const MAX_W = CONTAINER_W - 20;
const MIN_W = 100;

type TextSpec = { px: number; hidden?: 'display' | 'visibility' | 'opacity'; empty?: boolean };

function buildScene(vbW: number, vbH: number, texts: Array<number | TextSpec>): {
    wrapper: HTMLElement;
    svg: SVGElement;
} {
    const container = document.createElement('div');
    container.className = 'd3-container';
    Object.defineProperty(container, 'clientWidth', { value: CONTAINER_W, configurable: true });
    const wrapper = document.createElement('div');
    wrapper.className = 'mermaid-wrapper';
    container.appendChild(wrapper);

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${vbW} ${vbH}`);
    for (const spec of texts) {
        const s: TextSpec = typeof spec === 'number' ? { px: spec } : spec;
        const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        let style = `font-size:${s.px}px`;
        if (s.hidden === 'display') style += ';display:none';
        if (s.hidden === 'visibility') style += ';visibility:hidden';
        if (s.hidden === 'opacity') style += ';opacity:0';
        t.setAttribute('style', style);
        t.textContent = s.empty ? '   ' : `label at ${s.px}px`;
        svg.appendChild(t);
    }
    wrapper.appendChild(svg);
    document.body.appendChild(container);
    return { wrapper, svg: svg as unknown as SVGElement };
}

const widthPx = (svg: SVGElement) => parseFloat(svg.style.getPropertyValue('width'));
const heightPx = (svg: SVGElement) => parseFloat(svg.style.getPropertyValue('height'));
/** Effective on-screen size of a declared font at the chosen width. */
const effective = (declared: number, width: number, vbW: number) => declared * (width / vbW);

describe('chooseEffectiveWidth (pure policy)', () => {
    test('uniform fonts: smallest text lands exactly on TARGET (14px)', () => {
        const w = chooseEffectiveWidth(1000, 16, 16, MAX_W, MIN_W);
        expect(w).toBeCloseTo(875, 5);
        expect(effective(16, w, 1000)).toBeCloseTo(14, 5);
    });

    test('title-dominated (timeline: 14px labels, 22px title): labels held at MIN, title allowed past MAX', () => {
        // Old policy: 1000 * 14/22 = 636 -> labels at 8.9px.
        const w = chooseEffectiveWidth(1000, 14, 22, MAX_W, MIN_W);
        expect(w).toBeGreaterThan(636.4);
        expect(effective(14, w, 1000)).toBeCloseTo(12, 5);     // rule 2 floor wins over rule 3
        expect(effective(22, w, 1000)).toBeGreaterThan(18);     // title exceeds the soft cap
    });

    test('moderate spread (14px labels, 19px title): soft cap honoured because labels stay >= MIN', () => {
        const w = chooseEffectiveWidth(1000, 14, 19, MAX_W, MIN_W);
        expect(effective(19, w, 1000)).toBeCloseTo(18, 5);      // largest exactly at MAX
        const smallest = effective(14, w, 1000);
        expect(smallest).toBeGreaterThanOrEqual(12);
        expect(smallest).toBeLessThan(14);
    });

    test('container width is absolute even when it pushes the smallest text below MIN', () => {
        const w = chooseEffectiveWidth(2000, 12, 12, MAX_W, MIN_W);
        expect(w).toBe(MAX_W);
        expect(effective(12, w, 2000)).toBeLessThan(12);
    });

    test('small diagram is not inflated to fill the pane', () => {
        // 300-wide viewBox, uniform 16px -> 262.5px, far short of the container.
        const w = chooseEffectiveWidth(300, 16, 16, MAX_W, MIN_W);
        expect(w).toBeCloseTo(262.5, 5);
        expect(w).toBeLessThan(MAX_W);
    });

    test('minimum width floor', () => {
        expect(chooseEffectiveWidth(20, 40, 40, MAX_W, MIN_W)).toBe(MIN_W);
    });
});

describe('applyEffectiveFontScaling (DOM seam)', () => {
    beforeEach(() => { document.body.innerHTML = ''; });

    test('sizes from the declared font range and preserves aspect ratio', () => {
        const { wrapper, svg } = buildScene(1000, 500, [14, 22]);
        applyEffectiveFontScaling(svg, wrapper, 'timeline');
        const w = widthPx(svg);
        expect(w).toBeCloseTo(chooseEffectiveWidth(1000, 14, 22, MAX_W, MIN_W), 5);
        expect(heightPx(svg)).toBeCloseTo(w / 2, 5);
    });

    test.each<'display' | 'visibility' | 'opacity'>(['display', 'visibility', 'opacity'])(
        'hidden text (%s) does not anchor the smallest-font rule', (hidden) => {
            const { wrapper, svg } = buildScene(1000, 500, [{ px: 2, hidden }, 16]);
            applyEffectiveFontScaling(svg, wrapper, 'flowchart');
            // If the hidden 2px node anchored, width would be 1000*14/2 -> clamped
            // to the container (1180). Anchored on the 16px label it is 875.
            expect(widthPx(svg)).toBeCloseTo(875, 5);
        },
    );

    test('degenerate sub-4px and whitespace-only text are ignored', () => {
        const { wrapper, svg } = buildScene(1000, 500, [1, { px: 3, empty: true }, 16]);
        applyEffectiveFontScaling(svg, wrapper, 'flowchart');
        expect(widthPx(svg)).toBeCloseTo(875, 5);
    });

    test('no legible text: SVG left unsized', () => {
        const { wrapper, svg } = buildScene(1000, 500, [{ px: 16, hidden: 'display' }, { px: 1 }]);
        applyEffectiveFontScaling(svg, wrapper, 'flowchart');
        expect(svg.style.getPropertyValue('width')).toBe('');
    });

    test('re-running reproduces identical geometry (delayed safety pass cannot fight the sync pass)', () => {
        const { wrapper, svg } = buildScene(1000, 500, [14, 22]);
        applyEffectiveFontScaling(svg, wrapper, 'timeline');
        const w1 = svg.style.getPropertyValue('width');
        const h1 = svg.style.getPropertyValue('height');
        applyEffectiveFontScaling(svg, wrapper, 'timeline');
        expect(svg.style.getPropertyValue('width')).toBe(w1);
        expect(svg.style.getPropertyValue('height')).toBe(h1);
    });
});
