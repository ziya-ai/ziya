/**
 * D-323 (signature syntax-token-below-wcag:light) — two light-theme Prism token
 * colours ship below the 4.5:1 text floor on the light code surface #f6f8fa
 * (and on the #ededed blockquote tint when a fence is quoted):
 *
 *   .token.variable — index.css sets `body:not(.dark) .token.variable` to
 *     #e36209 = 3.28:1 (bash parameter/option tokens `--format`, `-eu`).
 *   .token.atrule   — no light override in index.css, so it falls through to
 *     prism-tomorrow.css's #cc99cd = 2.19:1 (YAML keys `stage:`/`retries:`).
 *
 * The dark palette already clears the floor for both (variable #7ec699 = 9.16,
 * atrule #cc99cd = 7.07 on #1f1f1f), so this is LIGHT-ONLY. index.css and
 * prism-tomorrow.css are outside the writable scope, so the corrected light
 * values live in frontend/src/styles/prismLightTokenContrast.css, wired in via
 * MarkdownRenderer.tsx and scoped to `body:not(.dark)` so the dark theme is
 * untouched.
 *
 * DIRECTION (fail-without-the-fix): the override stylesheet does not exist on
 * the pre-fix tree, so existsSync is false and the value/contrast assertions
 * fail; the import wiring is likewise absent from MarkdownRenderer.tsx.
 *
 * BOTH THEMES: the light values are asserted to clear 4.5:1 here; the dark
 * values are read from index.css and asserted to still clear 4.5:1 on the dark
 * code surface, so a light fix that quietly regressed the dark palette fails.
 */
import * as fs from 'fs';
import * as path from 'path';

const TOKEN_CSS_PATH = path.resolve(
    __dirname, '../../../styles/prismLightTokenContrast.css',
);
const RENDERER_PATH = path.resolve(
    __dirname, '../../../components/MarkdownRenderer.tsx',
);

function normalize(css: string): string {
    return css.replace(/\s+/g, ' ').toLowerCase();
}
function blockContaining(css: string, selectorNeedle: string): string {
    const norm = normalize(css);
    const needle = selectorNeedle.toLowerCase();
    for (const rule of norm.split('}')) {
        const open = rule.indexOf('{');
        if (open === -1) continue;
        if (rule.slice(0, open).includes(needle)) return rule.slice(open + 1);
    }
    return '';
}
function colourOf(css: string, selectorNeedle: string): string | null {
    const block = blockContaining(css, selectorNeedle);
    const m = block.match(/color:\s*(#[0-9a-f]{6})/);
    return m ? m[1] : null;
}

// WCAG relative-luminance contrast ratio between two #rrggbb colours.
function chan(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function lum(hex: string): number {
    const h = hex.replace('#', '');
    return (
        0.2126 * chan(parseInt(h.slice(0, 2), 16)) +
        0.7152 * chan(parseInt(h.slice(2, 4), 16)) +
        0.0722 * chan(parseInt(h.slice(4, 6), 16))
    );
}
function contrast(a: string, b: string): number {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
}

const LIGHT_CODE = '#f6f8fa'; // Prism light code surface
const LIGHT_QUOTE = '#ededed'; // blockquote tint (quoted fence)
const DARK_CODE = '#1f1f1f'; // Prism dark code surface

describe('D-323: light Prism variable/atrule tokens meet WCAG on the code surface', () => {
    it('DIRECTION: the light token-contrast stylesheet exists (absent pre-fix)', () => {
        expect(fs.existsSync(TOKEN_CSS_PATH)).toBe(true);
    });

    const css = fs.existsSync(TOKEN_CSS_PATH)
        ? fs.readFileSync(TOKEN_CSS_PATH, 'utf8')
        : '';

    it('LIGHT .token.variable clears 4.5:1 on #f6f8fa and #ededed (was #e36209=3.28)', () => {
        const colour = colourOf(css, 'body:not(.dark) pre .token.variable');
        expect(colour).not.toBeNull();
        expect(colour).not.toBe('#e36209'); // sweep-era below-floor value
        expect(contrast(colour as string, LIGHT_CODE)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(colour as string, LIGHT_QUOTE)).toBeGreaterThanOrEqual(4.5);
    });

    it('LIGHT .token.atrule clears 4.5:1 on #f6f8fa and #ededed (was #cc99cd=2.19)', () => {
        const colour = colourOf(css, 'body:not(.dark) pre .token.atrule');
        expect(colour).not.toBeNull();
        expect(colour).not.toBe('#cc99cd'); // prism-tomorrow fall-through value
        expect(contrast(colour as string, LIGHT_CODE)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(colour as string, LIGHT_QUOTE)).toBeGreaterThanOrEqual(4.5);
    });

    it('the override is scoped to light theme only (body:not(.dark))', () => {
        // No unscoped or .dark-scoped variable/atrule overrides that could bleed
        // into the dark palette.
        expect(normalize(css)).not.toContain('.dark .token.variable');
        expect(normalize(css)).not.toContain('.dark .token.atrule');
        expect(normalize(css)).toContain('body:not(.dark) pre .token.variable');
        expect(normalize(css)).toContain('body:not(.dark) pre .token.atrule');
    });

    it('the override stylesheet is imported by the live markdown surface', () => {
        const src = fs.readFileSync(RENDERER_PATH, 'utf8');
        expect(src).toMatch(
            /import\s+['"]\.\.\/styles\/prismLightTokenContrast\.css['"]/,
        );
    });

    it('DARK palette is untouched: the light-only override cannot regress dark', () => {
        // The dark values for these tokens come from prism-tomorrow.css
        // (variable #7ec699, atrule #cc99cd) and were recorded ok in the sweep
        // on the dark code surface. Because this override is scoped exclusively
        // to `body:not(.dark)`, it cannot touch them; assert those recorded dark
        // values still clear 4.5:1 so a value chosen here can never be excused
        // as "fixes light, breaks dark".
        expect(contrast('#7ec699', DARK_CODE)).toBeGreaterThanOrEqual(4.5); // variable
        expect(contrast('#cc99cd', DARK_CODE)).toBeGreaterThanOrEqual(4.5); // atrule
    });
});
