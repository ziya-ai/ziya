/**
 * Reproduction: a ```html-mockup figure fence rendering as a literal code
 * block instead of a mockup.
 *
 * Reported after a message that alternated `html-mockup figure` blocks with
 * `diff` blocks under markdown headings — every block came back as plain
 * code.  This drives the SAME fence-affecting preprocessing chain
 * MarkdownRenderer runs, in its order (see its call sites around lines
 * 6393-6598), so the failure is located rather than guessed at.
 *
 * Asserting on the preprocessed TEXT rather than on tokens is deliberate:
 * `marked` ships ESM only and no other suite here imports it, and the
 * question this answers — "does the opener line still carry its language?" —
 * is fully visible in the string.  Every downstream classifier
 * (determineTokenType -> parseMockupFence) keys off exactly that line.
 */

import {
    upgradeNestedFences,
    splitJsonSpecTrailingContent,
    repairAtomicFenceRuns,
    stripBareProseFences,
    escapeNestedBacktickFences,
} from '../fenceScanner';
import { parseMockupFence } from '../../utils/mockupFence';

const F = '```';

/** MarkdownRenderer's fence-affecting passes, in its own order. */
function preprocess(md: string): string {
    let s = md;
    s = upgradeNestedFences(s);
    s = splitJsonSpecTrailingContent(s);
    s = repairAtomicFenceRuns(s);
    s = stripBareProseFences(s);
    s = escapeNestedBacktickFences(s);
    return s;
}

/** Fence-opener lines that a mockup renderer would claim. */
function mockupOpeners(md: string): string[] {
    return md.split('\n')
        .filter(l => parseMockupFence(l.replace(/^`{3,}/, '')).isMockup
                     && /^`{3,}/.test(l));
}

const FIGURE_BODY = [
    '<div style="display:flex;gap:14px;font:13px system-ui">',
    '  <div style="flex:1;background:#fff;padding:18px">',
    '    <div style="color:#1f2328">Loading conversation...</div>',
    '  </div>',
    '</div>',
];

/** The reported message shape: heading, figure, heading, diff, alternating. */
function auditMessage(): string {
    return [
        '## 1 · `.ant-spin-text` — 1.00:1',
        '',
        F + 'html-mockup figure',
        ...FIGURE_BODY,
        F,
        '',
        F + 'diff',
        'diff --git a/frontend/src/index.css b/frontend/src/index.css',
        '--- a/frontend/src/index.css',
        '+++ b/frontend/src/index.css',
        '@@ -405,13 +405,17 @@',
        ' .ant-spin-text {',
        '-  color: #ffffff;',
        '+  color: #1f2328;',
        ' }',
        F,
        '',
        '## 2 · `GraphPanel.css`',
        '',
        F + 'html-mockup figure',
        '<div style="color:#24292f">follows the app theme</div>',
        F,
        '',
    ].join('\n');
}

describe('html-mockup figure through the fence preprocessing chain', () => {
    it('keeps both figure openers intact in the audit-message shape', () => {
        const out = preprocess(auditMessage());
        // Positive assertion that the fixture itself is well-formed, so a
        // zero-length result cannot pass as "nothing was broken".
        expect(mockupOpeners(auditMessage())).toHaveLength(2);
        expect(mockupOpeners(out)).toHaveLength(2);
    });

    it('keeps a figure opener that directly follows a heading', () => {
        const md = [
            '## Heading',
            '',
            F + 'html-mockup figure',
            '<div style="color:#24292f">x</div>',
            F,
            '',
        ].join('\n');
        expect(mockupOpeners(preprocess(md))).toHaveLength(1);
    });

    it('keeps a figure opener when a bare-fence block precedes it', () => {
        // stripBareProseFences scans forward from a bare fence for a partner
        // close; a mockup opener carrying a modifier must count as a tagged
        // opener during that scan or the bare fence mis-pairs across it.
        const md = [
            'Some prose.',
            '',
            F,
            'plain preformatted text',
            F,
            '',
            F + 'html-mockup figure',
            '<div style="color:#24292f">x</div>',
            F,
            '',
        ].join('\n');
        expect(mockupOpeners(preprocess(md))).toHaveLength(1);
    });

    it('does not leave the info string as the first body line', () => {
        // What a split opener looks like once rendered: the language shows up
        // as content, which is exactly what was reported on screen.
        const out = preprocess(auditMessage()).split('\n');
        out.forEach((line, i) => {
            if (/^`{3,}\s*$/.test(line)) {
                expect(out[i + 1] ?? '').not.toMatch(/^html-mockup/);
            }
        });
    });
});
