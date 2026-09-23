/**
 * @jest-environment jsdom
 *
 * G-4c0db1 / D-287 / D-420 / D-425 — mermaid capture-boundary sizing RACE.
 *
 * The headless capture page (DiagramRenderPage) flips data-render-status to
 * 'complete' — the signal the screenshot is taken on — exactly 500ms after the
 * SVG is first detected. The plugin's effective-font sizing pass, which mutates
 * the SVG's width/height, used to run ONLY on a coincident setTimeout(500ms).
 * Whether that width/height mutation landed before or after the screenshot was
 * therefore a pure race with no ordering guarantee, which is why:
 *   - D-287 oscillated verified<->regressed with no source change (an
 *     oversize-canvas SVG was captured at natural size OR after the
 *     fit-to-width shrink, non-deterministically -> subpixel labels);
 *   - D-420's 60-slice pie dropped most legend labels in one theme but not the
 *     other (the resize fired mid-capture) though the DOM carried every label;
 *   - D-425's 620-edge flowchart rendered subpixel or not.
 *
 * The fix runs applyEffectiveFontScaling SYNCHRONOUSLY inside
 * applyUnifiedResponsiveScaling (the synchronous render path), so the final
 * geometry is settled long before the 500ms 'complete' window elapses. The
 * delayed re-run is kept only as an idempotent safety net for live browsers.
 *
 * DIRECTION: on the pre-fix tree the SVG is NOT sized until the 500ms timer
 * fires, so immediately after the call (timers not advanced) svg.style.width is
 * empty and these assertions fail. With the fix the sizing is applied
 * synchronously and they pass. Asserted in BOTH themes.
 */
import {
    applyUnifiedResponsiveScaling,
    applyEffectiveFontScaling,
} from '../mermaidPlugin';

function buildScene(viewBox: string): {
    container: HTMLElement;
    wrapper: HTMLElement;
    svg: SVGElement;
} {
    const container = document.createElement('div');
    container.className = 'd3-container';
    const wrapper = document.createElement('div');
    wrapper.className = 'mermaid-wrapper';
    container.appendChild(wrapper);

    // Oversize-HEIGHT canvas: content is a modest width but a grossly tall
    // viewBox (the D-287 / w2-02 shape and the tall 60-row-legend pie shape).
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', viewBox);
    svg.setAttribute('width', '100%');
    // A couple of legible text nodes so findMaxDeclaredFontSize > 0.
    for (const label of ['Category number 24 [76]', 'Storage by tier (TB)']) {
        const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        t.setAttribute('style', 'font-size:16px');
        t.textContent = label;
        svg.appendChild(t);
    }
    wrapper.appendChild(svg);
    document.body.appendChild(container);
    return { container, wrapper, svg: svg as unknown as SVGElement };
}

describe('G-4c0db1 mermaid capture-boundary sizing is synchronous (race-free)', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        document.body.innerHTML = '';
    });
    afterEach(() => {
        jest.clearAllTimers();
        jest.useRealTimers();
    });

    for (const isDark of [false, true]) {
        const theme = isDark ? 'dark' : 'light';

        test(`[${theme}] SVG is sized synchronously, before any 500ms timer fires`, () => {
            const { container, svg } = buildScene('0 0 400 4000');

            // Call the render-path scaler. Do NOT advance timers: on the pre-fix
            // tree nothing sizes the SVG until the deferred 500ms callback.
            applyUnifiedResponsiveScaling(container, svg, isDark, 'flowchart');

            const w = svg.style.getPropertyValue('width');
            const h = svg.style.getPropertyValue('height');
            // Synchronously sized (fix present) -> explicit px width/height.
            expect(w).toMatch(/^\d+(\.\d+)?px$/);
            expect(h).toMatch(/^\d+(\.\d+)?px$/);

            const wPx = parseFloat(w);
            const hPx = parseFloat(h);
            // Non-degenerate: content is not collapsed to a subpixel sliver.
            expect(wPx).toBeGreaterThanOrEqual(100);
            expect(hPx).toBeGreaterThan(10);
            // The tall (400x4000) aspect is preserved: height dominates width.
            expect(hPx).toBeGreaterThan(wPx);
        });
    }

    test('effective-font sizing is idempotent (delayed safety re-run cannot fight the sync pass)', () => {
        const { wrapper, svg } = buildScene('0 0 400 4000');

        applyEffectiveFontScaling(svg, wrapper, 'flowchart');
        const w1 = svg.style.getPropertyValue('width');
        const h1 = svg.style.getPropertyValue('height');

        // Re-run against the same viewBox + declared fonts (the delayed 500ms
        // safety net). It must reproduce the identical geometry.
        applyEffectiveFontScaling(svg, wrapper, 'flowchart');
        const w2 = svg.style.getPropertyValue('width');
        const h2 = svg.style.getPropertyValue('height');

        expect(w2).toBe(w1);
        expect(h2).toBe(h1);
    });
});
