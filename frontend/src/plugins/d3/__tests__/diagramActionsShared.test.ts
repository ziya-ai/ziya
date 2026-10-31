/**
 * @jest-environment jsdom
 *
 * Open / Source / Save controls on every SVG-producing plugin.
 *
 * Until now only mermaid, graphviz, vega(-lite), drawio and packet grew the
 * `.diagram-actions` cluster, each with its own inline copy.  wavedrom,
 * railroad, flamegraph, chord, force-directed and d2 rendered a bare SVG.
 * The trio now comes from plugins/d3/diagramActions.ts; these tests pin the
 * helper's behaviour and, at the seam, that each of those plugins actually
 * mounts it on a successful render -- and that a glyph-sized render (the
 * latex/music case the user asked to keep clean) gets nothing.
 */
import {
    addDiagramActions, diagramSourceText, isTooSmallForActions, svgNominalSize,
    MIN_ACTIONS_WIDTH, MIN_ACTIONS_HEIGHT,
} from '../diagramActions';
import { wavedromPlugin } from '../wavedromPlugin';
import { railroadPlugin } from '../railroadPlugin';
import { chordPlugin } from '../chordPlugin';
import { forceDirectedPlugin } from '../forceDirectedPlugin';
import { d2Plugin } from '../d2Plugin';
import { packetPlugin } from '../packetPlugin';
import { latexPlugin } from '../latexPlugin';
import { musicPlugin } from '../musicPlugin';
import * as realD3 from 'd3';

const SVG_NS = 'http://www.w3.org/2000/svg';

function freshContainer(): HTMLElement {
    const c = document.createElement('div');
    c.className = 'd3-container';
    document.body.appendChild(c);
    return c;
}

function svgOf(w: number, h: number): SVGSVGElement {
    const s = document.createElementNS(SVG_NS, 'svg') as SVGSVGElement;
    s.setAttribute('width', String(w));
    s.setAttribute('height', String(h));
    return s;
}

const buttonLabels = (c: HTMLElement) =>
    Array.from(c.querySelectorAll('.diagram-actions .diagram-action-button'))
        .map(b => b.textContent || '');

describe('diagramActions helper', () => {
    it('reads nominal size from attributes, then viewBox', () => {
        expect(svgNominalSize(svgOf(640, 120))).toEqual({ width: 640, height: 120 });
        const vb = document.createElementNS(SVG_NS, 'svg') as SVGSVGElement;
        vb.setAttribute('viewBox', '-10 -10 300 80');
        expect(svgNominalSize(vb)).toEqual({ width: 300, height: 80 });
    });

    it('refuses glyph-sized renders and containers with no SVG', () => {
        expect(isTooSmallForActions([svgOf(70, 40)])).toBe(true);
        expect(isTooSmallForActions([])).toBe(true);
        expect(isTooSmallForActions([svgOf(MIN_ACTIONS_WIDTH, MIN_ACTIONS_HEIGHT)])).toBe(false);
        // Several SVGs (railroad): the tallest/widest decides.
        expect(isTooSmallForActions([svgOf(40, 20), svgOf(400, 90)])).toBe(false);
    });

    it('mounts Open / Source / Save and the source toggle swaps the view', () => {
        const c = freshContainer();
        const view = document.createElement('div');
        view.appendChild(svgOf(500, 100));
        c.appendChild(view);
        const actions = addDiagramActions(c, {
            view, source: 'the <source>', title: 'T', filenameBase: 't', isDarkMode: false,
        });
        expect(actions).not.toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save']);

        const srcBtn = c.querySelector('.diagram-source-button') as HTMLButtonElement;
        const pre = c.querySelector('pre.diagram-source-view') as HTMLElement;
        expect(pre.style.display).toBe('none');
        expect(pre.textContent).toBe('the <source>');   // textContent, not innerHTML
        srcBtn.click();
        expect(view.style.display).toBe('none');
        expect(pre.style.display).toBe('block');
        expect(srcBtn.textContent).toBe('🎨 View');
        srcBtn.click();
        expect(view.style.display).toBe('');
        expect(pre.style.display).toBe('none');
    });

    it('mounts nothing on a small render', () => {
        const c = freshContainer();
        const view = document.createElement('div');
        view.appendChild(svgOf(60, 30));
        c.appendChild(view);
        expect(addDiagramActions(c, {
            view, source: 'x', title: 'T', filenameBase: 't', isDarkMode: false,
        })).toBeNull();
        expect(c.querySelector('.diagram-actions')).toBeNull();
        expect(c.querySelector('pre.diagram-source-view')).toBeNull();
    });

    it('diagramSourceText prefers the fence text and falls back to JSON', () => {
        expect(diagramSourceText({ type: 'x', definition: 'raw text' })).toBe('raw text');
        expect(diagramSourceText('raw text')).toBe('raw text');
        expect(diagramSourceText({ type: 'x', definition: { a: 1 } })).toBe(JSON.stringify({ a: 1 }, null, 2));
        expect(diagramSourceText({ type: 'chord', matrix: [[1]] })).toContain('"matrix"');
    });
});

// ---------------------------------------------------------------------------
// Seam: each plugin mounts the cluster on a real render.  The nominal-size
// guard is satisfied by the plugins' own SVG attributes (jsdom has no
// layout), which is also what the guard reads in the browser.
// ---------------------------------------------------------------------------
describe('plugins mount the shared controls', () => {
    it('wavedrom', async () => {
        const c = freshContainer();
        await wavedromPlugin.render(c, null, {
            type: 'wavedrom',
            definition: JSON.stringify({ signal: [
                { name: 'clk', wave: 'p..........' },
                { name: 'data', wave: 'x.345x.....' },
            ] }),
        }, false);
        expect(c.querySelector('svg')).not.toBeNull();
        expect(c.querySelector('[data-diagram-error]')).toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save']);
    });

    it('railroad', () => {
        const c = freshContainer();
        railroadPlugin.render(c, null, {
            type: 'railroad',
            definition: JSON.stringify({ rules: [{ name: 'expr', diagram: { sequence: ['a', 'b', 'c', 'd', 'e', 'f'] } }] }),
        }, false);
        expect(c.querySelector('[data-diagram-error]')).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save']);
    });

    it('chord', () => {
        const c = freshContainer();
        chordPlugin.render(c, realD3, {
            type: 'chord', matrix: [[0, 5], [3, 0]], names: ['A', 'B'],
        }, false);
        expect(c.querySelector('svg')).not.toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save']);
    });

    it('force-directed', () => {
        // d3-zoom's defaultExtent reads svg.viewBox.baseVal, which jsdom does
        // not implement.  Shim it from the attribute so the real zoom runs.
        const proto = (window as any).SVGSVGElement.prototype;
        if (!('viewBox' in proto)) {
            Object.defineProperty(proto, 'viewBox', {
                configurable: true,
                get(this: SVGElement) {
                    const [x, y, width, height] = (this.getAttribute('viewBox') || '0 0 0 0')
                        .trim().split(/[\s,]+/).map(Number);
                    return { baseVal: { x, y, width, height } };
                },
            });
        }
        const c = freshContainer();
        const cleanup = forceDirectedPlugin.render(c, realD3, {
            type: 'force-directed', width: 600, height: 400,
            nodes: [{ id: 'a' }, { id: 'b' }], links: [{ source: 'a', target: 'b' }],
        }, false);
        expect(c.querySelector('svg')).not.toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save']);
        if (typeof cleanup === 'function') cleanup();
    });

    it('d2', async () => {
        const c = freshContainer();
        await d2Plugin.render(c, realD3, {
            type: 'd2', definition: 'a -> b\nb -> c\nc -> d', isMarkdownBlockClosed: true,
        } as any, false);
        expect(c.querySelector('svg')).not.toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save']);
    });

    it('packet (migrated from its inline copy; keeps its Theme toggle via extraButtons)', () => {
        const c = freshContainer();
        packetPlugin.render(c, realD3, {
            type: 'packet',
            definition: 'packet-beta\ntitle Frame\n0-15: "Src"\n16-31: "Dst"',
        }, false);
        expect(c.querySelector('[data-diagram-error]')).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
        expect(buttonLabels(c)).toEqual(['↗️ Open', '📝 Source', '💾 Save', '🌙 Dark']);
        // Source view is the author's fence text, not the JSON envelope.
        expect(c.querySelector('pre.diagram-source-view')?.textContent).toContain('packet-beta');
    });
});

describe('glyph-sized plugins stay clean (source contract)', () => {
    // latex and music are excluded by design, not by the size guard alone.
    it('latex and music never import the shared controls', () => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const fs = require('fs'); const path = require('path');
        for (const f of ['latexPlugin.ts', 'musicPlugin.ts']) {
            const src = fs.readFileSync(path.join(__dirname, '..', f), 'utf8');
            expect(src).not.toMatch(/diagramActions/);
        }
        expect(latexPlugin.name).toBe('latex-renderer');
        expect(musicPlugin.name).toBe('music-renderer');
    });
});
