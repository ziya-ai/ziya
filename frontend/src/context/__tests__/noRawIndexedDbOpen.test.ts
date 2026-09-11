/**
 * Regression guard for the store-less-ZiyaDB crash (crash log:
 * "NotFoundError: Failed to execute 'transaction' on 'IDBDatabase'").
 *
 * An unversioned indexedDB.open('ZiyaDB') outside the db layer CREATES an
 * empty zero-store database when none exists (e.g. after a recovery
 * promotion to ZiyaDB_r1).  Any later transaction('conversations') against
 * that bare database throws NotFoundError.  Raw opens also leak
 * connections that block deleteDatabase()/upgrades in recovery paths.
 *
 * All IndexedDB opens must go through frontend/src/utils/db.ts (which
 * tracks the promoted name and closes/reopens correctly) or the dedicated
 * recovery module emergencyRecovery.js.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC_ROOT = path.resolve(__dirname, '..', '..');

// Modules allowed to call indexedDB.open / deleteDatabase directly.
const ALLOWED = new Set([
    path.join('utils', 'db.ts'),
    path.join('utils', 'emergencyRecovery.js'),
]);

/**
 * Blank out comment text, preserving line count and column offsets so the
 * offender report's line numbers stay accurate.
 *
 * A documented reference to a call is not a call.  Without this, removing the
 * raw probe and explaining WHY in a comment reads as still making it -- so the
 * only way to keep this guard green would be to leave the removal
 * undocumented, which is the opposite of what the guard is for.
 *
 * Block-comment state is carried ACROSS lines: a /* ... *\/ header spanning
 * several lines is masked in full, not just on its opening line.
 *
 * '//' inside a single- or double-quoted string is NOT treated as a comment,
 * so a line like fetch('http://x') does not blank the code after it.  Two
 * known gaps, both accepted deliberately because over-masking here costs a
 * false NEGATIVE (a real raw open going unreported), which is worse than the
 * false positive this fixes: a multi-line template literal containing '//',
 * and a regex literal containing '/*'.  Neither appears in this tree.
 */
function maskComments(src: string): string[] {
    const out: string[] = [];
    let inBlock = false;
    for (const raw of src.split('\n')) {
        let line = '';
        let i = 0;
        let quote: string | null = null;
        while (i < raw.length) {
            if (inBlock) {
                const end = raw.indexOf('*/', i);
                if (end === -1) {
                    line += ' '.repeat(raw.length - i);
                    i = raw.length;
                } else {
                    line += ' '.repeat(end + 2 - i);
                    i = end + 2;
                    inBlock = false;
                }
                continue;
            }
            const ch = raw[i];
            if (quote) {
                // Escaped char inside a string: copy both, so \' does not
                // read as the closing quote.
                if (ch === '\\') {
                    line += raw.slice(i, i + 2);
                    i += 2;
                    continue;
                }
                if (ch === quote) quote = null;
                line += ch;
                i += 1;
                continue;
            }
            if (ch === '"' || ch === "'") {
                quote = ch;
                line += ch;
                i += 1;
                continue;
            }
            const two = raw.slice(i, i + 2);
            if (two === '//') {
                line += ' '.repeat(raw.length - i);
                i = raw.length;
                continue;
            }
            if (two === '/*') {
                inBlock = true;
                line += '  ';
                i += 2;
                continue;
            }
            line += ch;
            i += 1;
        }
        out.push(line);
    }
    return out;
}

function collectSourceFiles(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === 'node_modules' || entry.name === '__tests__') continue;
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            collectSourceFiles(full, out);
        } else if (/\.(tsx?|jsx?)$/.test(entry.name) && !/\.(test|spec)\./.test(entry.name)) {
            out.push(full);
        }
    }
    return out;
}

describe('IndexedDB access is confined to the db layer', () => {
    it('no raw indexedDB.open() calls outside utils/db.ts and emergencyRecovery.js', () => {
        const offenders: string[] = [];
        for (const file of collectSourceFiles(SRC_ROOT)) {
            const rel = path.relative(SRC_ROOT, file);
            if (ALLOWED.has(rel)) continue;
            const content = fs.readFileSync(file, 'utf8');
            // Match indexedDB.open(...) but not window.indexedDB existence checks.
            // Test the comment-masked text so a documented reference is not
            // mistaken for a call, but report the ORIGINAL line so the
            // offender message stays readable.
            const rawLines = content.split('\n');
            maskComments(content).forEach((line, i) => {
                if (/\bindexedDB\s*\.\s*open\s*\(/.test(line)) {
                    offenders.push(`${rel}:${i + 1}: ${rawLines[i].trim()}`);
                }
            });
        }
        expect(offenders).toEqual([]);
    });
});

/**
 * maskComments is now load-bearing for the guard above: if it over-masks, a
 * real raw open goes unreported and the guard passes while the crash it exists
 * to prevent is reintroduced.  A silently-neutered guard is worse than no
 * guard, so the masking is pinned here rather than trusted.
 */
describe('maskComments (the guard depends on this)', () => {
    const RE = /\bindexedDB\s*\.\s*open\s*\(/;
    const caught = (src: string): boolean => maskComments(src).some(l => RE.test(l));

    // Positive controls: these MUST still be reported.
    it.each([
        ['a bare call', "indexedDB.open('ZiyaDB');"],
        // '//' inside a string is not a comment: blanking from there would
        // hide the real call that follows it.
        ['a call after a url string', "fetch('http://a'); indexedDB.open('b');"],
        ['a call after an escaped quote', "log('it\\'s'); indexedDB.open('b');"],
        ['a call after an inline block comment', "/* note */ indexedDB.open('x');"],
    ])('still catches %s', (_label, src) => {
        expect(caught(src)).toBe(true);
    });

    // Negative controls: a documented reference is not a call.
    it.each([
        ['a full-line comment', "// a raw indexedDB.open('ZiyaDB') probe"],
        ['a trailing comment', "init(); // was indexedDB.open('x')"],
        ['an inline block comment', "/* indexedDB.open('x') */"],
        // Block state must carry across lines, or only the opening line masks.
        ['a multi-line block comment', "/**\n * a raw indexedDB.open('x') probe\n */\nconst y = 1;"],
    ])('ignores %s', (_label, src) => {
        expect(caught(src)).toBe(false);
    });

    it('preserves line count so reported line numbers stay accurate', () => {
        const src = "const a = 1;\n// indexedDB.open('x')\n/* two\n   lines */\nconst b = 2;";
        expect(maskComments(src)).toHaveLength(src.split('\n').length);
    });

    it('leaves non-comment code byte-identical', () => {
        const src = "const a = fetch('http://x');";
        expect(maskComments(src)[0]).toBe(src);
    });
});
