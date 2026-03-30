/**
 * Tests for global conversation duplication fixes.
 *
 * Global conversations match BOTH:
 *   - localProjectConvs filter  (c.projectId === projectId || c.isGlobal)
 *   - the old otherProjectConvs filter  (c.projectId !== projectId)
 *
 * This caused them to land in both buckets simultaneously, producing
 * persistent duplicate entries in every IDB save cycle.
 *
 * Fix: otherProjectConvs must also exclude isGlobal entries.
 * Applied in three places:
 *   1. queueSave — otherProjectConvsCache first miss (slow path A)
 *   2. queueSave — otherProjectConvsCache stale / second miss (slow path B)
 *   3. syncWithServer — the save call at the end of the merge cycle
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = path.resolve(__dirname, '../../frontend/src/context/ChatContext.tsx');

describe('Global conversation duplication fix', () => {
    let src: string;

    beforeAll(() => {
        src = fs.readFileSync(SRC, 'utf-8');
    });

    /**
     * Returns every occurrence of a pattern's match groups in the source,
     * useful for checking all call-sites of a filter expression.
     */
    function findAllFilterExpressions(source: string, marker: string): string[] {
        const results: string[] = [];
        let offset = 0;
        while (true) {
            const idx = source.indexOf(marker, offset);
            if (idx === -1) break;
            results.push(source.slice(idx, idx + 120));
            offset = idx + 1;
        }
        return results;
    }

    describe('queueSave otherProjectConvsCache filters', () => {
        it('excludes isGlobal from the otherProjectConvsCache filter (slow path A)', () => {
            // The first cache-miss path builds otherProjectConvsCache.
            // Both occurrences must exclude globals.
            const cacheAssignments = findAllFilterExpressions(
                src,
                'otherProjectConvsCache.current = {'
            );
            expect(cacheAssignments.length).toBeGreaterThanOrEqual(1);

            // Each cache assignment block must be followed by a !c.isGlobal filter
            // within the same expression. Check the surrounding 300 chars.
            cacheAssignments.forEach((assignment, i) => {
                const idx = src.indexOf(assignment);
                const block = src.slice(idx, idx + 400);
                expect(block).toContain('!c.isGlobal');
            });
        });

        it('does not use a bare projectId-only filter for other-project convs in queueSave', () => {
            // Old (buggy) pattern: .filter(c => c.projectId !== pid)  with nothing after
            // New pattern always chains .filter(c => !c.isGlobal) or combines both conditions
            const queueSaveStart = src.indexOf('const queueSave');
            expect(queueSaveStart).toBeGreaterThan(-1);
            const queueSaveBlock = src.slice(queueSaveStart, queueSaveStart + 3000);

            // There must be no bare single-condition projectId filter adjacent to the cache
            // (i.e. filter that only checks projectId and stops there)
            const bareFilterMatches = queueSaveBlock.match(
                /\.filter\(c\s*=>\s*c\.projectId\s*!==\s*pid\s*\)\s*\n/g
            );
            expect(bareFilterMatches).toBeNull();
        });
    });

    describe('syncWithServer otherProjectConvs filter', () => {
        it('excludes isGlobal in the syncWithServer otherProjectConvs filter', () => {
            // Find the otherProjectConvs assignment inside syncWithServer
            const syncStart = src.indexOf('SERVER_SYNC: Got');
            expect(syncStart).toBeGreaterThan(-1);

            // Search backward from syncStart to find the enclosing syncWithServer function
            const syncFnStart = src.lastIndexOf('const syncWithServer', syncStart);
            expect(syncFnStart).toBeGreaterThan(-1);

            const syncBlock = src.slice(syncFnStart, syncFnStart + 6000);
            const otherConvsIdx = syncBlock.indexOf('otherProjectConvs');
            expect(otherConvsIdx).toBeGreaterThan(-1);

            const otherConvsExpr = syncBlock.slice(otherConvsIdx, otherConvsIdx + 300);
            expect(otherConvsExpr).toContain('!c.isGlobal');
        });

        it('localProjectConvs still includes isGlobal conversations', () => {
            // The per-project view must continue to show global conversations.
            const syncFnStart = src.indexOf('localProjectConvs');
            expect(syncFnStart).toBeGreaterThan(-1);

            const localExpr = src.slice(syncFnStart, syncFnStart + 200);
            expect(localExpr).toContain('c.isGlobal');
        });

        it('global conversations do not appear in both localProjectConvs and otherProjectConvs', () => {
            // A conversation where c.isGlobal is true must not satisfy both:
            //   (c.projectId === projectId || c.isGlobal)   [local]
            //   (c.projectId !== projectId && !c.isGlobal)  [other — fixed]
            // Verify the logical exclusion by ensuring the two filter expressions
            // are mutually exclusive for isGlobal entries.

            // localProjectConvs includes globals
            const localIdx = src.indexOf('localProjectConvs');
            const localExpr = src.slice(localIdx, localIdx + 150);
            expect(localExpr).toMatch(/c\.isGlobal/);

            // otherProjectConvs excludes globals (the fix)
            const otherIdx = src.indexOf('otherProjectConvs');
            const otherExpr = src.slice(otherIdx, otherIdx + 200);
            expect(otherExpr).toMatch(/!\s*c\.isGlobal/);
        });
    });

    describe('formatter deduplication guard (mcpFormatterLoader)', () => {
        const LOADER_PATH = path.resolve(
            __dirname,
            '../../frontend/src/utils/mcpFormatterLoader.ts'
        );

        let loaderSrc: string;
        beforeAll(() => {
            loaderSrc = fs.readFileSync(LOADER_PATH, 'utf-8');
        });

        it('declares a module-level Set to track loaded formatter URLs', () => {
            expect(loaderSrc).toMatch(/const\s+loadedFormatterUrls\s*=\s*new\s+Set/);
        });

        it('checks the Set before injecting a script tag', () => {
            expect(loaderSrc).toMatch(/loadedFormatterUrls\.has\(/);
        });

        it('adds the URL to the Set after successful load', () => {
            expect(loaderSrc).toMatch(/loadedFormatterUrls\.add\(/);
        });

        it('the guard appears before the script element is created', () => {
            const hasIdx = loaderSrc.indexOf('loadedFormatterUrls.has(');
            const createIdx = loaderSrc.indexOf("document.createElement('script')");
            expect(hasIdx).toBeGreaterThan(-1);
            expect(createIdx).toBeGreaterThan(-1);
            expect(hasIdx).toBeLessThan(createIdx);
        });
    });
});
