/**
 * Locate the preprocessing pass that mangles a ```html-mockup figure opener.
 *
 * Diagnostic harness, not a unit test of any one function: it replays the
 * EXACT pass order MarkdownRenderer uses (including the six inline
 * applyOutsideFences prose regexes, which the earlier pipeline test omitted)
 * and prints every fence line after each stage, so the culprit is identified
 * by observation rather than by reading.
 *
 * Reported symptom, confirmed from the DOM: the block renders as
 * class="language-plaintext" with `html-mockup figure` as its FIRST BODY
 * LINE. The stored source is intact (verified in raw view), so a pass in
 * this chain is separating the info string from its backtick run.
 */

import {
    upgradeNestedFences,
    splitJsonSpecTrailingContent,
    repairAtomicFenceRuns,
    stripBareProseFences,
    escapeNestedBacktickFences,
    applyOutsideFences,
} from '../fenceScanner';

const F = '`'.repeat(3);

/** The real shape: heading, blank line, figure mockup, then a diff block. */
function auditMessage(): string {
    return [
        '## 1 · `.ant-spin-text` — 1.00:1',
        '',
        F + 'html-mockup figure',
        '<div style="display:flex;gap:14px;font:13px system-ui">',
        '  <div style="flex:1;background:#fff;border:1px solid #d0d7de">',
        '    <div style="color:#ffffff">Loading conversation...</div>',
        '  </div>',
        '</div>',
        F,
        '',
        F + 'diff',
        'diff --git a/frontend/src/index.css b/frontend/src/index.css',
        '--- a/frontend/src/index.css',
        '+++ b/frontend/src/index.css',
        '@@ -405,13 +405,17 @@',
        ' .ant-spin .ant-spin-dot-item {',
        '   background-color: currentColor;',
        ' }',
        '+.ant-spin-text {',
        '+  color: #1f2328;',
        '+}',
        F,
        '',
        '## 3 · CalleeHoldPanel — 1.33-2.54:1, no light values at all',
        '',
        F + 'html-mockup figure',
        '<div style="display:flex;gap:14px;font:12px system-ui">',
        '  <span style="color:#c9d1d9">authentication_error</span>',
        '</div>',
        F,
        '',
    ].join('\n');
}

/** Every line that starts a backtick run, with its 0-based index. */
function fenceLines(md: string): string[] {
    return md.split('\n')
        .map((l, i) => [i, l] as const)
        .filter(([, l]) => /^\s*`{3,}/.test(l) || /^html-mockup/.test(l))
        .map(([i, l]) => `${i}: ${JSON.stringify(l)}`);
}

/** The six inline prose passes from MarkdownRenderer, in order, verbatim. */
const PROSE_PASSES: Array<[string, (s: string) => string]> = [
    ['fix0-bold', (s) =>
        s.replace(/(\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|_[^_]+_)\n(```[a-zA-Z0-9_-]*)/gm, '$1\n\n$2')],
    ['fix0b-bold2', (s) => s.replace(/(\*\*)\n(```)/g, '$1\n\n$2')],
    ['fix1-heading', (s) =>
        s.replace(/(^#{1,6}\s+[^\n`]+?)\s+(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/gm, '$1\n\n$2')],
    ['fix2-numlist', (s) =>
        s.replace(/(\d+\.\s+[^\n`]+?)\s+(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/gm, '$1\n\n$2')],
    ['fix-para', (s) =>
        s.replace(/([^\n])\n(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2')],
    ['fix3-glued', (s) =>
        s.replace(/([^\n`])(`{3,}[a-zA-Z][a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2')],
];

describe('html-mockup figure through the REAL MarkdownRenderer pass order', () => {
    it('reports which pass separates the info string from its fence', () => {
        let md = auditMessage();
        const log: string[] = [];
        log.push('--- input ---');
        log.push(...fenceLines(md));

        const record = (name: string, before: string, after: string) => {
            const changed = before !== after;
            log.push(`--- after ${name} ${changed ? '(CHANGED)' : '(no-op)'} ---`);
            if (changed) log.push(...fenceLines(after));
        };

        let before = md;
        md = upgradeNestedFences(md);
        record('upgradeNestedFences', before, md);

        for (const [name, fn] of PROSE_PASSES) {
            before = md;
            md = applyOutsideFences(md, fn);
            record(`applyOutsideFences:${name}`, before, md);
        }

        before = md;
        md = splitJsonSpecTrailingContent(md);
        record('splitJsonSpecTrailingContent', before, md);

        before = md;
        md = repairAtomicFenceRuns(md);
        record('repairAtomicFenceRuns', before, md);

        before = md;
        md = stripBareProseFences(md);
        record('stripBareProseFences', before, md);

        before = md;
        md = escapeNestedBacktickFences(md);
        record('escapeNestedBacktickFences', before, md);

        // eslint-disable-next-line no-console
        console.log(log.join('\n'));

        // The failure signature: an info string stranded on its own line.
        const orphan = md.split('\n').findIndex(l => /^html-mockup\b/.test(l));
        expect(orphan).toBe(-1);
        // And both openers must still carry their language.
        expect(md.split('\n').filter(l => l.startsWith(F + 'html-mockup')).length)
            .toBe(2);
    });
});
