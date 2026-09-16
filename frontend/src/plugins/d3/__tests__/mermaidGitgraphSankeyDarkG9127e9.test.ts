/**
 * @jest-environment jsdom
 *
 * D-293 / G-9127e9 — mermaid dark-theme flattening of DATA-BEARING graphical
 * encodings (gitGraph + sankey). Two independent root causes, both dark-only
 * (all these specs pass in light):
 *
 *   1. w3-07 / w1-10 gitGraph palette + labels: the dark `mermaid.initialize`
 *      themeVariables block never set the per-branch git palette, so mermaid's
 *      built-in dark theme derived near-identical branch hues and a dark-navy
 *      commit-id label on a mid-slate chip (~1.69:1). Fixed at the theme source
 *      with `buildGitGraphDarkThemeVariables()`.
 *   2. w3-04 sankey ribbons + w1-10 branch line widths: the universal contrast
 *      pass (`enhanceSVGVisibility` FIX 3) flattened every path stroke to a flat
 *      colour + ~2px width, destroying flow-width and per-branch encoding. Fixed
 *      by honouring `skipSelectors` in the shape/line passes (previously it only
 *      guarded the text pass) so mermaidPlugin can exempt those paths in dark.
 *
 * Theme defect => BOTH themes asserted.
 */
import {
    buildGitGraphDarkThemeVariables,
    buildMermaidLightThemeVariables,
} from '../mermaidPlugin';
import { enhanceSVGVisibility } from '../../../utils/colorUtils';

const SVGNS = 'http://www.w3.org/2000/svg';

// --- WCAG contrast (self-contained, no source dependency) ---
function lin(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function lum(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function cr(a: string, b: string): number {
    const la = lum(a), lb = lum(b);
    const hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

const LIGHT_BG = '#ffffff';
const DARK_BG_A = '#1e1e1e'; // CHART_DARK_BG
const DARK_BG_B = '#2e3440'; // enhanceSVGVisibility pageBg

describe('D-293 gitGraph dark palette (w3-07 / w1-10) — theme source fix', () => {
    const dark = buildGitGraphDarkThemeVariables();
    const gitKeys = ['git0', 'git1', 'git2', 'git3', 'git4', 'git5', 'git6', 'git7'];

    it('assigns a DISTINCT branch colour per index (no flattening)', () => {
        const vals = gitKeys.map(k => dark[k]);
        expect(new Set(vals).size).toBe(gitKeys.length);
    });

    it('every dark branch colour clears the 3:1 graphic floor on BOTH dark backgrounds', () => {
        for (const k of gitKeys) {
            expect(cr(dark[k], DARK_BG_A)).toBeGreaterThanOrEqual(3.0);
            expect(cr(dark[k], DARK_BG_B)).toBeGreaterThanOrEqual(3.0);
        }
    });

    it('branch labels clear the 4.5:1 text floor on their branch chip', () => {
        gitKeys.forEach((k, i) => {
            const label = dark[`gitBranchLabel${i}`];
            expect(label).toBeDefined();
            expect(cr(label, dark[k])).toBeGreaterThanOrEqual(4.5);
        });
    });

    it('commit-id label is legible on its chip (was ~1.69:1)', () => {
        expect(cr(dark.commitLabelColor, dark.commitLabelBackground)).toBeGreaterThanOrEqual(4.5);
    });

    it('LIGHT theme git palette stays distinct and legible on white (both themes)', () => {
        const light = buildMermaidLightThemeVariables();
        const vals = gitKeys.map(k => light[k]);
        expect(new Set(vals).size).toBe(gitKeys.length);
        for (const k of gitKeys) {
            expect(cr(light[k], LIGHT_BG)).toBeGreaterThanOrEqual(3.0);
        }
    });
});

describe('D-293 enhanceSVGVisibility must not flatten data-bearing strokes when skipped', () => {
    /** A path whose stroke-WIDTH comes from a CSS class (no inline attr) —
     *  exactly how mermaid gitGraph draws its 3px branch lines. */
    function branchLine(svg: Element, stroke: string): SVGPathElement {
        const p = document.createElementNS(SVGNS, 'path') as SVGPathElement;
        p.setAttribute('d', 'M0 0 L 40 0');
        p.setAttribute('fill', 'none');
        p.setAttribute('stroke', stroke);
        p.setAttribute('class', 'branch');
        // NOTE: no inline stroke-width — the real 3px lives in the SVG's <style>.
        svg.appendChild(p);
        return p;
    }
    /** A sankey ribbon: stroke-width carries the flow magnitude (inline). */
    function ribbon(svg: Element, width: string, stroke: string): SVGPathElement {
        const p = document.createElementNS(SVGNS, 'path') as SVGPathElement;
        p.setAttribute('d', 'M0 0 C 10 0 10 20 20 20');
        p.setAttribute('fill', 'none');
        p.setAttribute('stroke', stroke);
        p.setAttribute('stroke-width', width);
        svg.appendChild(p);
        return p;
    }

    // Regression direction: WITHOUT the skip, the line pass slaps an inline
    // stroke-width:1.5 onto a class-styled 3px branch line (overriding the CSS)
    // — this is the w1-10 "3px -> ~1px" flatten. The assertion FAILS if the
    // colorUtils skip guard is absent (the width override would fire).
    it('documents the width flatten: line pass overrides a class-styled branch width without skip', () => {
        const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
        const r = branchLine(svg, '#61afef');
        enhanceSVGVisibility(svg, true, {}); // no skip
        // an inline width override was injected (crushing the CSS 3px)
        expect(r.getAttribute('stroke-width')).toBe('1.5');
    });

    it('PRESERVES a class-styled branch width in DARK when the path is skipped', () => {
        const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
        const r = branchLine(svg, '#61afef');
        enhanceSVGVisibility(svg, true, { skipSelectors: ['path', 'line'] });
        // no inline override injected -> the CSS 3px survives
        expect(r.getAttribute('stroke-width')).toBeNull();
    });

    it('PRESERVES a class-styled branch width in LIGHT when the path is skipped (both themes)', () => {
        const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
        const r = branchLine(svg, '#1f77b4');
        enhanceSVGVisibility(svg, false, { skipSelectors: ['path', 'line'] });
        expect(r.getAttribute('stroke-width')).toBeNull();
    });

    it('leaves a skipped sankey ribbon byte-unchanged (flow width + colour) in DARK', () => {
        const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
        const r = ribbon(svg, '24', '#3b4252');
        enhanceSVGVisibility(svg, true, { skipSelectors: ['path'] });
        expect(r.getAttribute('stroke-width')).toBe('24');
        expect(r.getAttribute('stroke')).toBe('#3b4252');
    });

    it('keeps distinct gitGraph branch colours (no palette collapse) when skipped', () => {
        const svg = document.createElementNS(SVGNS, 'svg') as SVGSVGElement;
        const b0 = ribbon(svg, '3', '#61afef');
        b0.setAttribute('class', 'branch');
        const b1 = ribbon(svg, '3', '#e06c75');
        b1.setAttribute('class', 'branch');
        enhanceSVGVisibility(svg, true, { skipSelectors: ['path', 'line'] });
        // distinct colours survive; without the skip both would collapse to the
        // single theme lineColor.
        expect(b0.getAttribute('stroke')).toBe('#61afef');
        expect(b1.getAttribute('stroke')).toBe('#e06c75');
        expect(b0.getAttribute('stroke')).not.toBe(b1.getAttribute('stroke'));
    });
});
