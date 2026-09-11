/**
 * G-9d2066 / D-012: mermaid dark-theme node/cluster label contrast.
 *
 * The dark overlay in mermaid-theme.css sets a STATIC label colour for
 * `.dark .node .label text` / `.dark .cluster .label text`. Because the node
 * fill is deliberately left unset (mermaid's own dark mainBkg #3b4252 / cluster
 * #4c566a stand), that static label colour must be LIGHT to clear the WCAG-AA
 * 4.5:1 floor. The regression was `fill:#000000` (2.09:1 on #3b4252 — below
 * floor), which only rendered legibly when the runtime contrast pass ran; that
 * pass is skipped for any diagram declaring an explicit colour, leaving default
 * nodes illegible in dark mode.
 *
 * This guard reads the shipped stylesheet, extracts the dark node/cluster label
 * rule, and asserts its fill clears 4.5:1 against every dark surface mermaid can
 * place a default label on. The rule is .dark-scoped, so it never paints in the
 * light theme (asserted structurally); light node labels stay mermaid's default
 * dark-on-white and are unaffected.
 */
import * as fs from 'fs';
import * as path from 'path';

// --- self-contained WCAG relative-luminance contrast (no imports) ---
function lin(c: number): number {
    const n = c / 255;
    return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
}
function lum(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: string, b: string): number {
    const la = lum(a);
    const lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const CSS = fs.readFileSync(
    path.join(__dirname, '..', '..', '..', 'styles', 'mermaid-theme.css'),
    'utf-8',
);

/** Extract the `fill:` value from the dark node/cluster label rule block. */
function darkNodeLabelFill(): string {
    // Find the selector block that targets `.dark ... .node .label text`.
    const m = CSS.match(
        /\.dark[^{}]*\.node \.label text[^{]*\{([^}]*)\}/,
    );
    expect(m).not.toBeNull();
    const body = m![1];
    const fill = body.match(/fill:\s*(#[0-9a-fA-F]{6})/);
    expect(fill).not.toBeNull();
    return fill![1].toLowerCase();
}

// Dark surfaces mermaid places default node/cluster labels on (from the
// themeVariables in mermaidPlugin.ts: mainBkg / clusterBkg / secondBkg).
const DARK_NODE_BG = '#3b4252';
const DARK_CLUSTER_BG = '#4c566a';
const DARK_SECOND_BG = '#434c5e';
const WCAG_AA = 4.5;

describe('G-9d2066 / D-012 mermaid dark node-label contrast', () => {
    it('dark node/cluster label fill clears 4.5:1 on every dark surface', () => {
        const fill = darkNodeLabelFill();
        expect(contrast(fill, DARK_NODE_BG)).toBeGreaterThanOrEqual(WCAG_AA);
        expect(contrast(fill, DARK_CLUSTER_BG)).toBeGreaterThanOrEqual(WCAG_AA);
        expect(contrast(fill, DARK_SECOND_BG)).toBeGreaterThanOrEqual(WCAG_AA);
    });

    it('the regressed value (#000000) would fail the floor — proves the guard bites', () => {
        // Sanity anchor: the old value is genuinely below floor on the node bg,
        // so this test fails if the stylesheet reverts to black.
        expect(contrast('#000000', DARK_NODE_BG)).toBeLessThan(WCAG_AA);
    });

    it('the label rule is scoped to the dark theme only (light theme untouched)', () => {
        const m = CSS.match(/([^\n]*\.node \.label text[^{]*)\{[^}]*fill:\s*#[0-9a-fA-F]{6}/);
        expect(m).not.toBeNull();
        // Every selector in the rule's group must be .dark-scoped.
        expect(m![1]).toMatch(/\.dark/);
        expect(m![1]).not.toMatch(/(^|,)\s*\.mermaid-container \.node \.label text/);
    });
});
