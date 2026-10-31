/**
 * render_diagram's "Preprocessing changed the spec" notes survive the
 * production console strip.
 *
 * The headless renderer loads /render from the production bundle and reads
 * `🔧 TAG: ...` console lines (app/utils/chart_rewrites.py).  craco strips
 * console.log in production, so the diagram fix-up passes report through
 * console.info, which survives (scripts/console_info_rewrites.py).
 *
 * @jest-environment node
 */
import * as fs from 'fs';
import * as path from 'path';

const FRONTEND = path.join(__dirname, '..', '..', '..');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const craco = require(path.join(FRONTEND, 'craco.config.js'));
// eslint-disable-next-line @typescript-eslint/no-var-requires
const TerserPlugin = require(path.join(FRONTEND, 'node_modules/terser-webpack-plugin'));
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { minify } = require(path.join(FRONTEND, 'node_modules/terser'));

const SCOPE = ['src/plugins/d3', 'src/components/D3Renderer.tsx', 'src/utils/diagramUtils.ts'];

function scopeFiles(): { f: string; text: string }[] {
    const out: string[] = [];
    const walk = (p: string) => {
        if (fs.statSync(p).isFile()) { out.push(p); return; }
        for (const ent of fs.readdirSync(p, { withFileTypes: true })) {
            if (ent.name === '__tests__' || ent.name === 'node_modules') continue;
            const q = path.join(p, ent.name);
            if (ent.isDirectory()) walk(q);
            else if (/\.(ts|tsx)$/.test(ent.name) && !/\.test\./.test(ent.name)) out.push(q);
        }
    };
    SCOPE.forEach(s => walk(path.join(FRONTEND, s)));
    return out.map(f => ({ f: path.relative(FRONTEND, f), text: fs.readFileSync(f, 'utf8') }));
}

describe('diagram rewrite notes in production', () => {
    const files = scopeFiles();

    it('no diagram rewrite note is logged through console.log', () => {
        const hits = files.flatMap(({ f, text }) =>
            (text.match(/console\.log\(\s*[`'"]🔧[^\n]{0,60}/g) || []).map(m => `${f}: ${m}`));
        expect(hits).toEqual([]);
    });

    it('the notes are still there, through console.info (positive control)', () => {
        const vega = files.find(x => x.f.endsWith('vegaLitePlugin.ts'))!.text;
        expect(vega).toMatch(/fixLogScales\(spec\)\.forEach\(msg => \{ console\.info\(`🔧 VEGA-PREPROCESS:/);
        const total = files.reduce((n, { text }) =>
            n + (text.match(/console\.info\(\s*[`'"]🔧/g) || []).length, 0);
        expect(total).toBeGreaterThanOrEqual(170);
    });

    it('a console.info rewrite note survives the production terser options', async () => {
        const plugin = new TerserPlugin({ terserOptions: { compress: {} } });
        const cfg: any = { mode: 'production', module: { rules: [] }, resolve: {}, optimization: { minimizer: [plugin] } };
        craco.webpack.configure(cfg);
        const opts = plugin.options.minimizer.options;
        const out = (await minify(`
            window.f = (spec) => { fix(spec).forEach(msg => { console.info(\`🔧 VEGA-PREPROCESS: \${msg}\`); }); };
            window.g = () => { console.log('🔧 STRIPPED: gone'); };`, opts)).code as string;
        expect(out).toMatch(/console\.info\(`🔧 VEGA-PREPROCESS: /);
        expect(out).not.toMatch(/STRIPPED/);
    });
});
