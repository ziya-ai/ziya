/**
 * G-f75896 CSS-side chat-message fixes.
 *
 * D-015 (signature blockquote-no-visual-affordance) — blockquotes inside chat
 *   messages get NO boundary stroke, tint, or muted colour, so quoted text is
 *   indistinguishable from an indented paragraph and the 3:1 graphical-boundary
 *   floor is unmeetable by construction. Both themes affected.
 *   FIX: frontend/src/styles/blockquoteAffordance.css adds a theme-resolved
 *   left rule + muted text colour, wired in via MarkdownRenderer.tsx. index.css
 *   is outside the writable scope, so the affordance lives in a writable
 *   stylesheet whose theme-scoped selectors out-specify index.css's blockquote
 *   rule (which only sets white-space).
 *
 * D-020 (signature syntax-keyword-below-wcag:light) — the Prism LIGHT keyword
 *   colour on the #f6f8fa code surface measured 4.30:1 (below the 4.5 floor).
 *   The current source is already remediated to #b31d28 (6.32:1); this test is
 *   a regression guard that FAILS if the colour reverts toward the sweep-era
 *   #d73a49 (=4.30) or otherwise drops below 4.5:1 on #f6f8fa.
 *
 * DIRECTION (fail-without-the-fix): the blockquote stylesheet does not exist on
 * the pre-fix tree, so readFileSync throws and the D-015 assertions fail; the
 * import wiring is likewise absent from MarkdownRenderer.tsx. The D-020 guard
 * fails against the sweep-era #d73a49 value.
 */
import * as fs from 'fs';
import * as path from 'path';

const BQ_CSS_PATH = path.resolve(
    __dirname, '../../../styles/blockquoteAffordance.css',
);
const INDEX_CSS_PATH = path.resolve(__dirname, '../../../index.css');
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

// WCAG relative-luminance contrast ratio between two #rrggbb colours.
function chan(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function lum(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
}
function contrast(a: string, b: string): number {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
}

const LIGHT_MSG_BG = '#f0f0f0'; // .message.ai
const DARK_MSG_BG = '#262626';  // .dark .message.ai
const CODE_SURFACE = '#f6f8fa'; // Prism light code background

describe('D-015: chat-message blockquote visual affordance', () => {
    it('DIRECTION: the blockquote affordance stylesheet exists (absent pre-fix)', () => {
        expect(fs.existsSync(BQ_CSS_PATH)).toBe(true);
    });

    const css = fs.existsSync(BQ_CSS_PATH) ? fs.readFileSync(BQ_CSS_PATH, 'utf8') : '';

    it('LIGHT: blockquote gets a left rule and a >=4.5:1 muted text colour', () => {
        const block = blockContaining(css, 'body:not(.dark) .message .message-content blockquote');
        expect(block).toMatch(/border-left:\s*4px solid #6a737d/);
        expect(block).toContain('color: #57606a');
        // border rule >= 3:1 graphical boundary, text >= 4.5:1 body text — both on #f0f0f0
        expect(contrast('#6a737d', LIGHT_MSG_BG)).toBeGreaterThanOrEqual(3.0);
        expect(contrast('#57606a', LIGHT_MSG_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('DARK: blockquote gets a left rule and a >=4.5:1 muted text colour', () => {
        const block = blockContaining(css, '.dark .message .message-content blockquote');
        expect(block).toMatch(/border-left:\s*4px solid #8b949e/);
        expect(block).toContain('color: #c9d1d9');
        expect(contrast('#8b949e', DARK_MSG_BG)).toBeGreaterThanOrEqual(3.0);
        expect(contrast('#c9d1d9', DARK_MSG_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('the affordance stylesheet is imported by the live markdown surface', () => {
        const src = fs.readFileSync(RENDERER_PATH, 'utf8');
        expect(src).toMatch(/import\s+['"]\.\.\/styles\/blockquoteAffordance\.css['"]/);
    });
});

describe('D-020: light Prism keyword colour meets WCAG on the code surface', () => {
    const css = fs.readFileSync(INDEX_CSS_PATH, 'utf8');

    it('body:not(.dark) .token.keyword resolves to a >=4.5:1 colour on #f6f8fa', () => {
        const block = blockContaining(css, 'body:not(.dark) .token.keyword');
        const m = block.match(/color:\s*(#[0-9a-f]{6})/);
        expect(m).not.toBeNull();
        const colour = (m as RegExpMatchArray)[1];
        // Guard against a revert to the sweep-era #d73a49 (=4.30:1, below floor).
        expect(colour).not.toBe('#d73a49');
        expect(contrast(colour, CODE_SURFACE)).toBeGreaterThanOrEqual(4.5);
    });
});
