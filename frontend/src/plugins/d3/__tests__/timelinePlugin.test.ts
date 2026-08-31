/**
 * @jest-environment jsdom
 *
 * Tests for the timeline PLUGIN WRAPPER (plugins/d3/timelinePlugin).
 *
 * The layout engine has its own suite; this one covers only what the wrapper
 * adds, and it exists mainly for one seam:
 *
 *   THE WRAPPER MUST PASS d3 THROUGH TO THE ENGINE.
 *
 * The engine takes d3 as an argument rather than importing it (the project's
 * jest cannot parse d3's ESM-only source), so the wrapper is the ONLY thing
 * connecting D3Renderer's resolved d3 instance to the layout code.  Both halves
 * are individually correct and fully tested; if the wrapper drops the argument,
 * every timeline renders as an error card and nothing in the engine suite or the
 * wrapper's own structural assertions would notice.  That is exactly the
 * "defined but never called" shape worth an end-to-end assertion.
 *
 * d3 is required from its UMD build here for the same reason the engine suite
 * does: the ESM entry point is unparseable by this runner, and a stub would let
 * the assertions pass while proving nothing about real tick placement.
 */
import { timelinePlugin } from '../timelinePlugin';
import type { TimelineD3 } from '../../../utils/d3Plugins/timelinePlugin';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const realD3 = require('d3/dist/d3.js') as TimelineD3;

const MARKER = '[data-diagram-error]';

const SPEC = {
    title: 'Interwar Europe',
    items: [
        { lane: 'Conflicts', label: 'WWI', start: '1914-07-28', end: '1918-11-11' },
        { lane: 'Treaties', label: 'Versailles', at: '1919-06-28' },
    ],
};

function renderWith(definition: any, d3: any = realD3, isDark = false): HTMLElement {
    const container = document.createElement('div');
    (timelinePlugin as any).render(
        container, d3, { type: 'timeline', definition }, isDark);
    return container;
}

describe('timeline plugin — spec routing', () => {
    it('handles its own type and nothing else', () => {
        expect(timelinePlugin.canHandle({ type: 'timeline' })).toBe(true);
        expect(timelinePlugin.canHandle({ type: 'railroad' })).toBe(false);
        expect(timelinePlugin.canHandle({ type: 'wavedrom' })).toBe(false);
    });

    it('canHandle never throws on a foreign or malformed spec', () => {
        // findPluginForSpec walks canHandle against EVERY spec in priority
        // order, and a throw there used to latch D3Renderer's plugin-loading
        // flag on permanently -- wedging the component for the rest of the
        // page's life on behalf of a plugin that was never the right one.
        for (const junk of [undefined, null, 0, '', 'timeline', [], {}]) {
            expect(() => timelinePlugin.canHandle(junk as any)).not.toThrow();
            if (junk !== undefined && junk !== null) {
                expect(timelinePlugin.canHandle(junk as any)).toBe(false);
            }
        }
    });

    it('is registered under a distinct name at the shared plugin priority', () => {
        expect(timelinePlugin.name).toBe('timeline-renderer');
        expect(timelinePlugin.priority).toBe(6);
    });
});

describe('timeline plugin — the d3 hand-off (the seam)', () => {
    it('renders an SVG when given a real d3, proving the instance reaches the engine', () => {
        const c = renderWith(SPEC);
        const svg = c.querySelector('svg');
        expect(svg).not.toBeNull();
        // Real content, not an empty frame: two marks and the title.
        expect(svg!.querySelectorAll('rect').length).toBeGreaterThanOrEqual(1);
        expect(svg!.querySelector('polygon')).not.toBeNull();   // the instant
        expect(c.querySelector(MARKER)).toBeNull();
    });

    it('the d3 that reaches the engine is REAL: ticks land on year boundaries', () => {
        // A stub or partial shim would still produce an <svg>, so the assertion
        // is on behaviour only a genuine calendar scale provides: every tick on
        // 1 January, hence strictly increasing whole years with a uniform gap.
        //
        // This discriminates against a naive linear split, which is the shape a
        // hand-rolled axis would take. Dividing 1914-07-28..1919-06-28 into
        // equal steps puts ticks mid-year, and %Y then renders REPEATS
        // (1914,1915,1915,1916,...) rather than a clean sequence -- so the
        // duplicate-free uniform gap is the discriminating property, not the
        // mere fact that the labels look like years.
        const c = renderWith(SPEC);
        const years = [...c.querySelectorAll('text')]
            .map(t => t.textContent || '')
            .filter(s => /^\d{4}$/.test(s))
            .map(Number);
        expect(years.length).toBeGreaterThanOrEqual(4);
        expect(new Set(years).size).toBe(years.length);          // no repeats
        const gaps = years.slice(1).map((y, i) => y - years[i]);
        expect(new Set(gaps).size).toBe(1);                      // uniform
        expect(gaps[0]).toBeGreaterThanOrEqual(1);
        // And inside the data's own span, so the domain was really consulted.
        expect(Math.min(...years)).toBeGreaterThanOrEqual(1914);
        expect(Math.max(...years)).toBeLessThanOrEqual(1920);
    });

    it('a MISSING d3 becomes a named error card, not an obscure TypeError', () => {
        // Callers legitimately pass null (the headless harness does, for plugins
        // that need no d3), so the failure has to be legible.
        const c = renderWith(SPEC, null);
        const card = c.querySelector(MARKER);
        expect(card).not.toBeNull();
        const msg = card!.getAttribute('data-diagram-error') || '';
        expect(msg).toContain('scaleUtc');
        expect(msg).not.toContain('is not a function');
    });
});

describe('timeline plugin — error surface', () => {
    it('tags an unknown key with the accepted vocabulary', () => {
        const c = renderWith({ itmes: [] });
        const card = c.querySelector(MARKER);
        expect(card).not.toBeNull();
        const msg = card!.getAttribute('data-diagram-error') || '';
        expect(msg).toContain('itmes');    // names the offending key
        expect(msg).toContain('items');    // and the correct vocabulary
    });

    it('tags malformed JSON', () => {
        const c = renderWith('{"items": [');
        const card = c.querySelector(MARKER);
        expect(card).not.toBeNull();
        expect(card!.getAttribute('data-diagram-error')).toContain('JSON');
    });

    it('keeps the offending definition visible so nothing is lost', () => {
        const c = renderWith({ itmes: [] });
        expect(c.querySelector('details')).not.toBeNull();
        expect(c.textContent).toContain('itmes');
    });

    it('never materialises markup smuggled in through a spec key', () => {
        // The assertion is on the DOM, not on innerHTML. An error message is
        // model-authored text and lands in two places with DIFFERENT escaping
        // rules: the card body (escaped by escape(), so it stays inert text) and
        // the data-diagram-error attribute (set via setAttribute, so the
        // serializer escapes the quote but legitimately leaves < and > inside
        // the quoted value). Grepping innerHTML therefore reports a "leak" for
        // the attribute case that cannot execute, while saying nothing about
        // whether an element was actually created. Asking the DOM what exists is
        // the property that matters.
        const c = renderWith('{"</script><img src=x onerror=alert(1)>": 1}');
        expect(c.querySelector('img')).toBeNull();
        expect(c.querySelector('script')).toBeNull();
        // The text is still shown, so the diagnosis is not silently swallowed.
        expect(c.textContent).toContain('img src=x');
    });

    it('quotes the marker attribute so it cannot be broken out of', () => {
        // The wrapper sets the marker with setAttribute precisely because the
        // message embeds model-authored keys; this pins that a double quote in
        // the message is serialized escaped rather than closing the attribute.
        const c = renderWith('{"a\\" onload=\\"alert(1)": 1}');
        const card = c.querySelector(MARKER);
        expect(card).not.toBeNull();
        expect(card!.getAttribute('data-diagram-error')).toContain('onload');
        expect(card!.hasAttribute('onload')).toBe(false);
        expect(c.innerHTML).not.toContain('" onload="');
    });
});

describe('timeline plugin — mounting', () => {
    it('does not repeat the title as an HTML heading', () => {
        // The engine draws the title INSIDE the svg, where it participates in
        // the vertical frame. The sibling railroad wrapper does add HTML
        // headings, so copying that shape here would show the title twice.
        const c = renderWith(SPEC);
        const outside = [...c.children]
            .flatMap(el => [...el.childNodes])
            .filter(n => n.nodeName !== 'svg' && n.nodeName !== 'SVG')
            .map(n => n.textContent || '')
            .join(' ');
        expect(outside).not.toContain('Interwar Europe');
        expect(c.querySelector('svg')!.textContent).toContain('Interwar Europe');
    });

    it('replaces prior content instead of appending on a re-render', () => {
        const c = document.createElement('div');
        for (let i = 0; i < 3; i++) {
            (timelinePlugin as any).render(
                c, realD3, { type: 'timeline', definition: SPEC }, false);
        }
        expect(c.querySelectorAll('svg').length).toBe(1);
    });

    it('lets the svg scale down without distorting', () => {
        const svg = renderWith(SPEC).querySelector('svg') as any;
        expect(svg.style.maxWidth).toBe('100%');
        expect(svg.style.height).toBe('auto');
    });

    it('accepts a pre-parsed object definition as well as a string', () => {
        const fromObj = renderWith(SPEC);
        const fromStr = renderWith(JSON.stringify(SPEC));
        expect(fromObj.querySelector('svg')).not.toBeNull();
        expect(fromStr.querySelector('svg')).not.toBeNull();
        expect(fromObj.querySelector(MARKER)).toBeNull();
        expect(fromStr.querySelector(MARKER)).toBeNull();
    });
});

describe('timeline plugin — streaming gate', () => {
    it('waits for a parseable body rather than flashing an error card', () => {
        const gate = timelinePlugin.isDefinitionComplete!;
        expect(gate('{"items": [{"label": "a"')).toBe(false);
        expect(gate('')).toBe(false);
        expect(gate(JSON.stringify(SPEC))).toBe(true);
    });

    it('accepts a body that arrived inside a stray fence', () => {
        const gate = timelinePlugin.isDefinitionComplete!;
        const fenced = ['```timeline', JSON.stringify(SPEC), '```'].join('\n');
        expect(gate(fenced)).toBe(true);
    });
});
