/**
 * Sidebar footer: model row + token breakdown must sit IN the flex column,
 * not float over it.
 *
 * Observed 2026-10-20: the token breakdown moved to the panel bottom under
 * the model row, but index.css still declared .model-id-display as
 * position:fixed; bottom:0; height:35px; text-align:center.  The pinned bar
 * covered the bottom 35px of the panel — exactly the breakdown's hand-off /
 * telemetry row — and the model name sat centered above a left-aligned
 * legend.  theme-tokens.css (loaded after index.css) must neutralise that
 * rule.  jsdom does no layout, so this is a static check on the stylesheets.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', '..', rel), 'utf8');

/** Declaration blocks of rules whose selector list ends in `sel`. */
function blocksFor(css: string, sel: string): string[] {
    const out: string[] = [];
    const re = /([^{}]+)\{([^{}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(css)) !== null) {
        if (m[1].split(',').some(s => s.trim().endsWith(sel))) out.push(m[2]);
    }
    return out;
}

describe('sidebar footer layout', () => {
    const indexCss = read('index.css');
    const tokensCss = read('styles/theme-tokens.css');
    const folderTree = read('components/FolderTree.tsx');

    it('precondition: index.css still pins .model-id-display (the rule being overridden)', () => {
        const legacy = blocksFor(indexCss, '.model-id-display').join('\n');
        expect(legacy).toMatch(/position:\s*fixed/);
        expect(legacy).toMatch(/text-align:\s*center/);
    });

    it('theme-tokens.css returns the model row to the flex column, left-aligned', () => {
        const override = blocksFor(tokensCss, '.folder-tree-panel .model-id-display').join('\n');
        expect(override).toMatch(/position:\s*static/);
        expect(override).toMatch(/height:\s*auto/);
        expect(override).toMatch(/text-align:\s*left/);
        expect(override).toMatch(/justify-content:\s*flex-start/);
        // Loaded after index.css, so the override wins on specificity + order.
        const indexTsx = read('index.tsx');
        expect(indexTsx.indexOf("'./index.css'")).toBeLessThan(indexTsx.indexOf("'./styles/theme-tokens.css'"));
    });

    it('total row: "x / y" at the left edge, "n%" at the right', () => {
        const total = blocksFor(tokensCss, '.ziya-tok-total').join('\n');
        expect(total).toMatch(/display:\s*flex/);
        expect(total).toMatch(/justify-content:\s*space-between/);
        expect(total).not.toMatch(/justify-content:\s*flex-end/);
    });

    it('legend spenders are spread evenly so a fourth or fifth slice fits', () => {
        const legend = blocksFor(tokensCss, '.ziya-tok-legend').join('\n');
        expect(legend).toMatch(/justify-content:\s*space-between/);
    });

    it('budget zone is an outline on the whole bar: orange at warning, red at danger', () => {
        expect(tokensCss).toMatch(/--z-tok-warning:/);
        const warn = blocksFor(tokensCss, '.ziya-tok-bar.warning').join('\n');
        const danger = blocksFor(tokensCss, '.ziya-tok-bar.danger').join('\n');
        expect(warn).toMatch(/box-shadow:[^;]*var\(--z-tok-warning\)/);
        expect(danger).toMatch(/box-shadow:[^;]*var\(--z-tok-danger\)/);
        // The total row carries the warning colour too, not only danger.
        expect(tokensCss).toMatch(/\.ziya-tok-total\.warning b/);
    });

    it('legend figures are not coloured per number; the zone lives on the bar and the total', () => {
        // 2026-10-20: getTokenStyle painted every legend count green until the
        // danger threshold — three green numbers over a grey legend on linen.
        const tcd = read('components/TokenCountDisplay.tsx');
        expect(tcd).not.toMatch(/getTokenStyle|getTokenColor/);
        expect(tcd).toMatch(/const budgetTier/);
        expect(tcd).toMatch(/warning=\{budgetTier === 'warning'\} danger=\{budgetTier === 'danger'\}/);
        expect(tcd).toMatch(/className=\{`ziya-tok-total \$\{budgetTier\}`\}/);
        // Chat is pushed before the AST item so AST wraps off first.
        expect(tcd.indexOf('key="chat"')).toBeLessThan(tcd.indexOf('key="ast"'));
        // The collapsed strip receives the same zone through panelStatus.
        expect(read('components/CollapsedPanelStrip.tsx')).toMatch(/warning=\{tokens\.warning\}/);
        expect(read('utils/panelStatus.ts')).toMatch(/warning\?: boolean/);
    });

    it('token-bar segments are the cool analogous set, not brand magenta, in both themes', () => {
        // 2026-10-20: teal + magenta vibrated on the dark surface (option E in
        // design/visual-refresh.md).  Pin the decision, not the exact hex: chat
        // and tools must differ from the brand magenta and from each other, and
        // the warning token must be a real orange (red channel dominant, green
        // mid, blue low), distinct from the beads amber #f59e0b.
        const tokens = (scope: string) => {
            const block = blocksFor(tokensCss, scope).join('\n');
            const get = (name: string) => block.match(new RegExp(`${name}:\\s*(#[0-9a-f]{6})`, 'i'))?.[1].toLowerCase();
            return { chat: get('--z-tok-chat'), tools: get('--z-tok-tools'), magenta: get('--z-brand-magenta'), warning: get('--z-tok-warning') };
        };
        for (const scope of [':root', 'body.dark']) {
            const t = tokens(scope);
            expect(t.chat).toBeDefined();
            expect(t.chat).not.toBe(t.magenta);
            expect(t.tools).not.toBe(t.magenta);
            expect(t.chat).not.toBe(t.tools);
            expect(t.warning).toBeDefined();
            expect(t.warning).not.toBe('#f59e0b');
            const [r, g, b] = [1, 3, 5].map(i => parseInt(t.warning!.slice(i, i + 2), 16));
            expect(r).toBeGreaterThan(200);
            expect(g).toBeGreaterThan(80);
            expect(g).toBeLessThan(r);
            expect(b).toBeLessThan(g);
        }
    });

    it('nothing sits under the footer: the panel has no bottom padding', () => {
        const panel = blocksFor(tokensCss, '.folder-tree-panel').join('\n');
        expect(panel).toMatch(/padding-bottom:\s*0/);
    });

    it('FolderTree renders the model row immediately before the token block', () => {
        const model = folderTree.indexOf('className="model-id-display"');
        const tokens = folderTree.indexOf('<TokenCountDisplay />', model);
        expect(model).toBeGreaterThan(-1);
        expect(tokens).toBeGreaterThan(model);
        // Nothing between them that could render a row.
        expect(folderTree.slice(model, tokens)).not.toMatch(/<Tabs|<ProjectSwitcher|<ActiveContextBar/);
    });
});
