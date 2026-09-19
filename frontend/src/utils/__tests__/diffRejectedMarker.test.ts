/**
 * Server-declared diff rejection (see diffUtils "Server-declared diff
 * rejection"). GOLDEN_BODY / GOLDEN_HASH are shared with
 * internal/tests/test_diff_validation_rejection_marker.py: the Python hook
 * and this hash MUST agree or the renderer never finds the fence the server
 * refused.
 */
import {
    diffBodyHash,
    diffRejectedMarker,
    extractRejectedDiffHashes,
    findRejectedDiffIndices,
    findSupersededDiffIndices,
    buildDiffRejectionNotice,
} from '../diffUtils';

const GOLDEN_BODY =
    'diff --git a/x/y.py b/x/y.py\n--- a/x/y.py\n+++ b/x/y.py\n' +
    '@@ -10,6 +10,9 @@\n     channel pointer.\n+  * reconcile \u2014 back-fills tags \u{1F3F7}\n';
const GOLDEN_HASH = 'fcaa05f2';

describe('diffBodyHash', () => {
    it('matches the Python implementation on the shared golden vector', () => {
        expect(diffBodyHash(GOLDEN_BODY)).toBe(GOLDEN_HASH);
    });
    it('ignores CRLF and surrounding whitespace, distinguishes content', () => {
        expect(diffBodyHash(GOLDEN_BODY.replace(/\n/g, '\r\n') + '\n\n')).toBe(GOLDEN_HASH);
        expect(diffBodyHash('x')).not.toBe(diffBodyHash('y'));
    });
});

describe('rejection marker round trip', () => {
    it('is invisible HTML and parses back to the hash', () => {
        const marker = diffRejectedMarker('deadbeef', 'a/b c.py');
        expect(marker.startsWith('<!--')).toBe(true);
        expect(extractRejectedDiffHashes(`text\n\n${marker}\n\nmore`)).toEqual(new Set(['deadbeef']));
    });
    it('collects every marker in a message', () => {
        const text = `${diffRejectedMarker('00000001')}\nx\n${diffRejectedMarker('00000002', 'f.py')}`;
        expect(extractRejectedDiffHashes(text)).toEqual(new Set(['00000001', '00000002']));
    });
});

// The case the heuristic misses: the correction merged two hunks into one
// and moved the anchor, so hunk ranges do not overlap and the body overlap
// is below the redo threshold.
const ORIGINAL =
    'diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n' +
    '@@ -10,6 +10,9 @@\n a\n b\n+  * one\n+  * two\n+  * three\n c\n d\n e\n' +
    '@@ -221,8 +224,11 @@\n f\n g\n-    old = 1\n+    new = 1\n+    extra = 2\n+    more = 3\n h\n';
const CORRECTION =
    'diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n' +
    '@@ -300,4 +300,12 @@\n z\n+def helper():\n+    return 1\n+\n+def other():\n+    return 2\n+\n+def third():\n+    return 3\n y\n';

describe('findRejectedDiffIndices', () => {
    it('flags the server-rejected fence where the heuristic does not', () => {
        // Positive control on the seam: without a declaration, nothing is
        // superseded for this pair by either mechanism.
        expect(findSupersededDiffIndices([ORIGINAL, CORRECTION]).size).toBe(0);
        expect(findRejectedDiffIndices('no markers here', [ORIGINAL, CORRECTION]).size).toBe(0);

        const message =
            `first try\n\n\`\`\`diff\n${ORIGINAL}\n\`\`\`\n\n` +
            `${diffRejectedMarker(diffBodyHash(ORIGINAL), 't.py')}\n\n` +
            `> patch did not apply\n\n\`\`\`diff\n${CORRECTION}\n\`\`\`\n`;
        expect(findRejectedDiffIndices(message, [ORIGINAL, CORRECTION])).toEqual(new Set([0]));
    });
    it('matches on the raw body even when marked dropped the trailing newline', () => {
        const marker = diffRejectedMarker(diffBodyHash(ORIGINAL + '\n'));
        expect(findRejectedDiffIndices(marker, [ORIGINAL.trimEnd()])).toEqual(new Set([0]));
    });
});

describe('buildDiffRejectionNotice', () => {
    it('emits a marker per rejected fence, a readable reason, and the separator', () => {
        const out = buildDiffRejectionNotice(
            [{ file_path: 't.py', body_hash: 'fcaa05f2', reason: 'Hunk 3: line counts did not match' }],
            '\n\n---\n\n**Correcting failed diff(s):**\n\n',
        );
        expect(extractRejectedDiffHashes(out)).toEqual(new Set(['fcaa05f2']));
        expect(out).toContain('`t.py`');
        expect(out).toContain('Hunk 3: line counts did not match');
        expect(out).toContain('**Correcting failed diff(s):**');
        // Marker before the notice, notice before the separator.
        expect(out.indexOf('ZIYA_DIFF_REJECTED')).toBeLessThan(out.indexOf('did not apply'));
        expect(out.indexOf('did not apply')).toBeLessThan(out.indexOf('Correcting failed'));
    });
    it('still explains the pause when the server sent no rejection records', () => {
        const out = buildDiffRejectionNotice([]);
        expect(out).toContain('did not apply cleanly');
        expect(extractRejectedDiffHashes(out).size).toBe(0);
        expect(out).toContain('Correcting failed diff(s)');
    });
});
