/**
 * The production bundle drops console.log / debug / trace.
 *
 * craco.config.js left CRA's TerserPlugin untouched, so ~1,500 console.log
 * calls shipped -- 320 in vegaLitePlugin alone -- plus a console.trace on
 * every queueSave (a stack capture per save).  Logged objects are kept by
 * the console for as long as the page lives once DevTools has been opened.
 *
 * Exercised end to end: the real craco hook mutates a real TerserPlugin,
 * and the resulting options are run through the real terser.
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

function cracoConfigured(mode: string) {
    const plugin = new TerserPlugin({ terserOptions: { compress: { comparisons: false, inline: 2 } } });
    const cfg: any = { mode, module: { rules: [] }, resolve: {}, optimization: { minimizer: [plugin] } };
    craco.webpack.configure(cfg);
    return plugin.options.minimizer.options;
}

describe('production terser options', () => {
    it('drops log/debug/trace, keeps warn/error/info and side effects in arguments', async () => {
        const opts = cracoConfigured('production');
        expect(opts.compress.pure_funcs).toEqual(expect.arrayContaining(['console.log', 'console.debug', 'console.trace']));
        // CRA's own settings survive the merge.
        expect(opts.compress.comparisons).toBe(false);
        const src = `
            var n = 0;
            function bump(){ n++; return n; }
            console.log('a', bump());
            console.debug('b');
            console.trace('c');
            console.warn('kept-warn');
            console.error('kept-error');
            console.info('kept-info');
            window.out = n;`;
        const out = (await minify(src, opts)).code as string;
        expect(out).not.toMatch(/console\.(log|debug|trace)/);
        expect(out).toMatch(/kept-warn/);
        expect(out).toMatch(/kept-error/);
        expect(out).toMatch(/kept-info/);
        // bump() had a side effect, so it still runs.
        expect(out).toMatch(/bump\(\)|n\+\+|\+\+n/);
    });

    it('drops console.group / groupCollapsed / groupEnd (left empty once their logs go)', async () => {
        const opts = cracoConfigured('production');
        const out = (await minify(
            `console.group('g'); console.groupCollapsed('gc'); console.groupEnd(); console.warn('kept');`,
            opts)).code as string;
        expect(out).not.toMatch(/console\.group/);
        expect(out).toMatch(/kept/);
    });

    it('only block-bodied log calls are dropped: an expression arrow or an alias survives', async () => {
        // Why the source scan below exists.  pure_funcs drops a call only
        // when its value is unused; an expression-bodied arrow returns it,
        // and an alias is not the literal console.log.
        const opts = cracoConfigured('production');
        const out = (await minify(`
            window.a = (xs) => xs.forEach(m => console.log('SURVIVES-ARROW ' + m));
            window.b = (xs) => xs.forEach(m => { console.log('DROPPED-BLOCK ' + m); });
            window.c = (d) => { const log = d ? console.log : () => {}; log('SURVIVES-ALIAS'); };
            window.d = (d) => { const log = d ? (...x) => { console.log(...x); } : () => {}; log('DROPPED-SPREAD', 1); };`,
            opts)).code as string;
        expect(out).toMatch(/SURVIVES-ARROW/);
        expect(out).toMatch(/SURVIVES-ALIAS/);
        expect(out).not.toMatch(/DROPPED-BLOCK/);
        // The literal survives as a dead argument; the call itself is gone.
        expect(out).not.toMatch(/console\.log\(\.\.\./);
    });

    it('leaves development builds alone', () => {
        const opts = cracoConfigured('development');
        expect(opts.compress.pure_funcs).toBeUndefined();
    });
});

describe('no app source escapes the strip', () => {
    const SRC = path.join(FRONTEND, 'src');
    function walk(dir: string, out: string[] = []): string[] {
        for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
            if (ent.name === '__tests__' || ent.name === 'node_modules') continue;
            const p = path.join(dir, ent.name);
            if (ent.isDirectory()) walk(p, out);
            else if (/\.(ts|tsx)$/.test(ent.name) && !/\.test\.tsx?$/.test(ent.name)) out.push(p);
        }
        return out;
    }
    const files = walk(SRC).map(f => ({ f: path.relative(SRC, f), text: fs.readFileSync(f, 'utf8') }));

    it('scans the app source (positive control)', () => {
        expect(files.length).toBeGreaterThan(200);
        expect(files.some(x => x.f === path.join('utils', 'colorUtils.ts'))).toBe(true);
    });

    it('has no expression-bodied arrow returning console.log/debug', () => {
        const hits = files.flatMap(({ f, text }) => text.split('\n')
            .map((l, i) => /=>\s*console\.(log|debug)\(/.test(l) ? `${f}:${i + 1}: ${l.trim()}` : null)
            .filter(Boolean));
        expect(hits).toEqual([]);
    });

    it('has no alias of console.log/debug (debug ? console.log : ...)', () => {
        const hits = files.flatMap(({ f, text }) => text.split('\n')
            .map((l, i) => /[=?:]\s*console\.(log|debug)\b(?!\s*[.(])/.test(l) ? `${f}:${i + 1}: ${l.trim()}` : null)
            .filter(Boolean));
        expect(hits).toEqual([]);
    });

    it('colorUtils log still prints in development, through a block-bodied wrapper', () => {
        const color = files.find(x => x.f === path.join('utils', 'colorUtils.ts'))!.text;
        expect(color).toMatch(/const log\b[^=]*=\s*debug\s*\?\s*\(\.\.\.(\w+)[^)]*\)\s*=>\s*\{\s*console\.log\(\.\.\.\1\);?\s*\}/);
    });
});

describe('console helpers and temporary traces', () => {
    const chat = fs.readFileSync(path.join(FRONTEND, 'src/context/ChatContext.tsx'), 'utf8');
    const vega = fs.readFileSync(path.join(FRONTEND, 'src/plugins/d3/vegaLitePlugin.ts'), 'utf8');

    function slice(src: string, start: string, end: string): string {
        const s = src.indexOf(start);
        expect(s).toBeGreaterThan(-1);
        const e = src.indexOf(end, s + start.length);
        expect(e).toBeGreaterThan(s);
        return src.slice(s, e);
    }

    it('debug helpers print through console.info, which survives the strip', () => {
        const helpers = slice(chat, '(window as any).debugChatContext = () => {', '}, [conversations, currentConversationId]);');
        expect(helpers).not.toMatch(/console\.log\(/);
        expect((helpers.match(/console\.info\(/g) || []).length).toBeGreaterThanOrEqual(10);
        // End before the announcement line itself (its own console.log
        // prefix would otherwise fall inside the slice).
        const inspect = slice(vega, 'inspect: () => {', "console.log('Vega-Lite debugging available");
        expect(inspect).not.toMatch(/console\.log\(/);
        expect((inspect.match(/console\.info\(/g) || []).length).toBeGreaterThanOrEqual(10);
    });

    it('the temporary save / add-message traces are gone', () => {
        expect(chat).not.toMatch(/console\.trace\('📋 saveQueue\.then called/);
        expect(chat).not.toMatch(/console\.trace\('📋 FAST_PATH caller/);
        expect(chat).not.toMatch(/console\.trace\('📝 addMessageToConversation first call stack/);
        expect(chat).not.toMatch(/__fastPathTraced|__addMsgTraced/);
        // Positive: the once-only duplicate-guard trace is untouched.
        expect(chat).toMatch(/console\.trace\('🛑 DUPLICATE_GUARD/);
    });
});
