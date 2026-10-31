/**
 * Sidebar search: a stale response must not overwrite a newer one, and a
 * partial local fallback must say so.
 *
 * Measured: one all-projects server search took 8.9 s on a 2,350-chat
 * install.  performSearch fired one per debounced keystroke with no
 * cancellation, so overlapping scans each passed the 25 s client deadline
 * and db.searchConversations fell back to the IndexedDB scan, which holds
 * message bodies only for conversations hydrated in this browser (shells
 * are never written to IDB).  The user saw "searches missing terms", with
 * nothing telling them the result came from the partial path.
 *
 * Source-anchored: performSearch lives inside MUIChatHistory, which needs
 * the whole provider tree to mount.
 */
import * as fs from 'fs';
import * as path from 'path';

const HIST = fs.readFileSync(path.join(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8');
const DB = fs.readFileSync(path.join(__dirname, '..', '..', 'utils', 'db.ts'), 'utf8');

function slice(src: string, start: string, end: string): string {
    const s = src.indexOf(start);
    expect(s).toBeGreaterThan(-1);
    const e = src.indexOf(end, s + start.length);
    expect(e).toBeGreaterThan(s);
    return src.slice(s, e);
}

const perform = slice(HIST, 'const performSearch = useCallback(async (query: string) => {',
    '}, [searchAllProjects, searchSort, currentProject?.id, projects]);');
const dbSearch = slice(DB, 'async searchConversations(query: string, options: SearchOptions = {})',
    'private async _searchConversationsLocal');

describe('performSearch', () => {
    it('drops a response superseded by a later search', () => {
        expect(HIST).toMatch(/const searchSeqRef = useRef\(0\);/);
        expect(perform).toMatch(/const seq = \+\+searchSeqRef\.current;/);
        // Every state write after the await is guarded.
        const afterAwait = perform.slice(perform.indexOf('await db.searchConversations'));
        const guards = afterAwait.match(/if \(seq !== searchSeqRef\.current\) return;/g) || [];
        expect(guards.length).toBeGreaterThanOrEqual(2);
        expect(afterAwait).toMatch(/if \(seq === searchSeqRef\.current\) setIsSearching\(false\);/);
        // Positive: results are still set.
        expect(afterAwait).toMatch(/setSearchResults\(enriched\);/);
    });

    it('warns when the results came from the partial local scan', () => {
        expect(perform).toMatch(/db\.lastSearchSource === 'local'/);
        expect(perform).toMatch(/message\.warning\(/);
    });
});

describe('db.searchConversations', () => {
    it('records which path produced the results', () => {
        expect(DB).toMatch(/lastSearchSource: 'server' \| 'local' = 'server';/);
        const serverReturn = dbSearch.indexOf('return server as SearchResult[];');
        const markServer = dbSearch.lastIndexOf("this.lastSearchSource = 'server';", serverReturn);
        expect(markServer).toBeGreaterThan(-1);
        const localReturn = dbSearch.indexOf('return this._searchConversationsLocal(query, options);');
        expect(dbSearch.lastIndexOf("this.lastSearchSource = 'local';", localReturn)).toBeGreaterThan(serverReturn);
    });
});
