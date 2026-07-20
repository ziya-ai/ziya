/**
 * Source-contract guard: fence-injection passes in MarkdownRenderer must
 * run through applyOutsideFences().
 *
 * The recurring bug class: a preprocessing pass that inserts a blank line
 * before a fence opener ("text```lang" → "text\n\n```lang") is added as a
 * bare global processedMarkdown.replace(). Run against a 
 * BODY quotes an inline fence token (e.g. a docs table row containing
 * `` 
 * floating the inner fence to column 0 — where it closes the outer 
 * fence early and truncates the rest of the message.
 *
 * This has now happened twice: one round of passes was wrapped in
 * applyOutsideFences() (see CHANGELOG), but one pass was missed and
 * resurfaced the truncation. Like diagramObserverLeak.test.tsx, this is a
 * source-scan contract test: it fails the build if anyone adds a new bare
 * fence-injection pass instead of wrapping it.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
    path.join(__dirname, '..', 'MarkdownRenderer.tsx'),
    'utf-8'
);

/** 1-based line number of a character offset in SRC. */
const lineOf = (offset: number, src: string = SRC): number =>
    src.slice(0, offset).split('\n').length;

/**
 * Detect bare fence-injection replaces in a source string.
 *
 * A violation is a `.replace()` whose receiver is `processedMarkdown`
 * (i.e. NOT the segment param of an applyOutsideFences transform), whose
 * regex contains exactly one fence-opener token (a `{3,} run), and whose
 * replacement string injects a blank line (\n\n). Paired open+close fence
 * rewrites (two fence tokens, e.g. the 
 * safe class and are not flagged.
 */
function findBareFenceInjections(src: string): number[] {
    const violations: number[] = [];
    // receiver.replace(/pattern/flags, 'literal replacement')
    const RE = /(\w[\w.]*)\.replace\(\s*\/((?:[^/\\\n]|\\.)+)\/[a-z]*\s*,\s*'((?:[^'\\]|\\.)*)'/g;
    let m: RegExpExecArray | null;
    while ((m = RE.exec(src)) !== null) {
        const [, receiver, pattern, replacement] = m;
        if (!receiver.endsWith('processedMarkdown')) continue;
        // Normalize escapes so `\`` and ` are counted identically.
        const normalized = pattern.replace(/\\/g, '');
        const fenceTokens = (normalized.match(/`\{3,\}/g) || []).length;
        if (fenceTokens === 1 && replacement.includes('\\n\\n')) {
            violations.push(lineOf(m.index, src));
        }
    }
    return violations;
}

/**
 * Find every use of the canonical blank-line-injection replacement
 * ('$1\n\n$2') and verify each sits inside an applyOutsideFences() call.
 */
function findUnwrappedInjectionReplacements(src: string): number[] {
    const NEEDLE = "'$1\\n\\n$2'";
    const violations: number[] = [];
    let idx = -1;
    while ((idx = src.indexOf(NEEDLE, idx + 1)) !== -1) {
        // The enclosing statement begins at the last `processedMarkdown =`
        // before this replacement. For a wrapped pass, the text between the
        // assignment and the replacement contains `applyOutsideFences(`.
        const stmtStart = src.lastIndexOf('processedMarkdown =', idx);
        const between = stmtStart >= 0 ? src.slice(stmtStart, idx) : '';
        if (!between.includes('applyOutsideFences(')) {
            violations.push(lineOf(idx, src));
        }
    }
    return violations;
}

describe('fence-injection passes must be wrapped in applyOutsideFences', () => {
    it('has at least one fence-injection pass to guard (sanity)', () => {
        // If the passes are refactored away entirely, this test should be
        // revisited rather than silently passing on an empty scan.
        expect(SRC).toContain("'$1\\n\\n$2'");
        expect(SRC).toContain('applyOutsideFences(');
    });

    it("every '$1\\n\\n$2' blank-line injection runs inside applyOutsideFences()", () => {
        const violations = findUnwrappedInjectionReplacements(SRC);
        expect(violations).toEqual([]);
    });

    it('no bare processedMarkdown.replace() injects a blank line before a fence token', () => {
        const violations = findBareFenceInjections(SRC);
        expect(violations).toEqual([]);
    });

    // Self-test: prove the detectors actually fire on the exact shape of the
    // regression that motivated this guard (the pre-fix line 6122 pass).
    // Without this, a bug in the scan regexes could make the suite pass
    // vacuously while offering no protection.
    const KNOWN_BAD =
        "processedMarkdown = processedMarkdown.replace(" +
        "/([^\\n\\`])(\\`{3,}[a-zA-Z][a-zA-Z0-9_-]*)(?=\\s|$)/g, '$1\\n\\n$2');";

    it('detector self-test: the historical bare pass is caught by the generic scan', () => {
        expect(findBareFenceInjections(KNOWN_BAD)).toHaveLength(1);
    });

    it('detector self-test: the historical bare pass is caught by the wrapper scan', () => {
        expect(findUnwrappedInjectionReplacements(KNOWN_BAD)).toHaveLength(1);
    });

    it('detector self-test: a properly wrapped pass is NOT flagged', () => {
        const WRAPPED =
            "processedMarkdown = applyOutsideFences(processedMarkdown, (s) =>\n" +
            "    s.replace(/([^\\n\\`])(\\`{3,}[a-zA-Z][a-zA-Z0-9_-]*)(?=\\s|$)/g, '$1\\n\\n$2')\n" +
            ");";
        expect(findBareFenceInjections(WRAPPED)).toEqual([]);
        expect(findUnwrappedInjectionReplacements(WRAPPED)).toEqual([]);
    });
});
