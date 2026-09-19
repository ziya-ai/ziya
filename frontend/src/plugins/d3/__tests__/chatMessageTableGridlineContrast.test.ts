/**
 * D-022 gridline contrast — the residual cause of the D-013 / D-014 regression
 * on the wave-2 chat-message specs w2-11, w2-14 and w2-15.
 *
 * BACKGROUND: D-013 (oversize-message-capture-clipped) and D-014
 * (overflow-x-clipped-no-scroll-no-wrap) are structurally FIXED — the capture
 * unclip and the overflow-scroll affordances still render every element with no
 * clipping. The evidence run 2f254c3a nonetheless failed those specs in LIGHT
 * only, with a DIFFERENT signature: `table-gridline-below-3to1:light`. The
 * markdown table gridlines fall to a faint UA/AntD default on the white cell
 * fill (~#d0d7de = 1.45:1 on #ffffff), below the 3:1 WCAG 1.4.11 boundary floor,
 * so a dense table's grid is effectively invisible in light while dark (near
 * -white default) passes — the theme asymmetry the class parity exists to catch.
 *
 * ROOT CAUSE (confirmed against source): frontend/src/index.css has NO border
 * rule for `.message .message-content` markdown tables (only `.diff-table`
 * rules). index.css is out of scope, so the fix adds theme-resolved gridline
 * colours in a writable stylesheet imported by MarkdownRenderer:
 *   - light gridline #6e7681 → 4.59:1 on #ffffff, 4.32:1 on #f6f8fa
 *   - dark  gridline #8b949e → 4.92:1 on #262626, 5.36:1 on #1f1f1f
 * both above the 3:1 floor on their own background.
 *
 * DIRECTION (fail-without-the-fix): the stylesheet does not exist on the
 * pre-fix tree, so readFileSync throws and every assertion fails; the import
 * wiring is likewise absent from MarkdownRenderer.tsx. Both pass only with the
 * fix applied. The contrast assertions verify BOTH themes.
 */
import * as fs from 'fs';
import * as path from 'path';

const CSS_PATH = path.resolve(
    __dirname, '../../../styles/messageTableGridlineContrast.css',
);
const RENDERER_PATH = path.resolve(
    __dirname, '../../../components/MarkdownRenderer.tsx',
);

function normalize(css: string): string {
    return css.replace(/\s+/g, ' ').toLowerCase();
}

// WCAG relative-luminance contrast ratio between two #rrggbb colours.
function srgbToLin(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function contrast(a: string, b: string): number {
    const la = luminance(a), lb = luminance(b);
    const hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

// Pull the hex colour out of a `border: 1px solid #xxxxxx;` inside the block
// whose selector list contains `selectorNeedle` AND `themeNeedle`.
function borderHexFor(css: string, themeNeedle: string): string | null {
    const norm = normalize(css);
    for (const rule of norm.split('}')) {
        const open = rule.indexOf('{');
        if (open === -1) continue;
        const selector = rule.slice(0, open);
        const body = rule.slice(open + 1);
        if (!selector.includes(themeNeedle)) continue;
        if (!selector.includes('.message .message-content table')) continue;
        const m = body.match(/border:\s*[^;]*?(#[0-9a-f]{6})/);
        if (m) return m[1];
    }
    return null;
}

describe('D-022: markdown table gridline contrast (unblocks D-013/D-014)', () => {
    it('DIRECTION: the gridline stylesheet exists (absent on the pre-fix tree)', () => {
        expect(fs.existsSync(CSS_PATH)).toBe(true);
    });

    const css = fs.existsSync(CSS_PATH) ? fs.readFileSync(CSS_PATH, 'utf8') : '';

    it('scopes the gridline colour per theme rather than a single constant', () => {
        expect(normalize(css)).toContain('body:not(.dark) .message .message-content table');
        expect(normalize(css)).toContain('body.dark .message .message-content table');
    });

    it('LIGHT gridline clears the 3:1 boundary floor on both light cell backgrounds', () => {
        const hex = borderHexFor(css, 'body:not(.dark)');
        expect(hex).not.toBeNull();
        expect(contrast(hex!, '#ffffff')).toBeGreaterThanOrEqual(3);
        expect(contrast(hex!, '#f6f8fa')).toBeGreaterThanOrEqual(3);
    });

    it('DARK gridline clears the 3:1 boundary floor on both dark message backgrounds', () => {
        const hex = borderHexFor(css, 'body.dark');
        expect(hex).not.toBeNull();
        expect(contrast(hex!, '#262626')).toBeGreaterThanOrEqual(3);
        expect(contrast(hex!, '#1f1f1f')).toBeGreaterThanOrEqual(3);
    });

    it('excludes diff tables so only plain markdown tables are affected', () => {
        expect(normalize(css)).toContain(':not(.diff-table)');
    });

    it('is imported by the live markdown surface so it reaches the renderer', () => {
        const src = fs.readFileSync(RENDERER_PATH, 'utf8');
        expect(src).toMatch(/import\s+['"]\.\.\/styles\/messageTableGridlineContrast\.css['"]/);
    });
});
