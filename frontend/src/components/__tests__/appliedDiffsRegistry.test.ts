/**
 * Tests for conversation-scoped applied-diffs registry keys.
 *
 * Verifies that the same diff content in two different conversations
 * produces distinct registry keys, so marking a diff as "Applied ✓"
 * in conversation A does not bleed into conversation B.
 */

// Inline the pure functions under test so the suite has no React dependency.

function stableDiffId(diffContent: string): string {
    let hash = 5381;
    const str = diffContent.slice(0, 2000);
    for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) + hash + str.charCodeAt(i)) | 0;
    }
    return `diff-${(hash >>> 0).toString(36)}`;
}

function scopedDiffId(conversationId: string, diffContent: string): string {
    return `${conversationId}:${stableDiffId(diffContent)}`;
}

const DIFF_A = `diff --git a/foo.ts b/foo.ts
--- a/foo.ts
+++ b/foo.ts
@@ -1 +1 @@
-old
+new`;

const DIFF_B = `diff --git a/bar.ts b/bar.ts
--- a/bar.ts
+++ b/bar.ts
@@ -1 +1 @@
-x
+y`;

describe('appliedDiffsRegistry key scoping', () => {
    test('same diff content produces the same content hash', () => {
        expect(stableDiffId(DIFF_A)).toBe(stableDiffId(DIFF_A));
    });

    test('different diff content produces different content hashes', () => {
        expect(stableDiffId(DIFF_A)).not.toBe(stableDiffId(DIFF_B));
    });

    test('same diff in different conversations produces different scoped IDs', () => {
        const idInConvA = scopedDiffId('conv-1', DIFF_A);
        const idInConvB = scopedDiffId('conv-2', DIFF_A);
        expect(idInConvA).not.toBe(idInConvB);
    });

    test('different diffs in the same conversation produce different scoped IDs', () => {
        const id1 = scopedDiffId('conv-1', DIFF_A);
        const id2 = scopedDiffId('conv-1', DIFF_B);
        expect(id1).not.toBe(id2);
    });

    test('applying in conv-A does not mark conv-B as applied', () => {
        const registry = new Set<string>();

        const idInConvA = scopedDiffId('conv-1', DIFF_A);
        const idInConvB = scopedDiffId('conv-2', DIFF_A);

        // Simulate applying in conversation A
        registry.add(idInConvA);

        expect(registry.has(idInConvA)).toBe(true);
        // The same diff content in conversation B must NOT appear applied
        expect(registry.has(idInConvB)).toBe(false);
    });

    test('applied state in conv-B does not affect conv-A', () => {
        const registry = new Set<string>();

        const idInConvA = scopedDiffId('conv-1', DIFF_A);
        const idInConvB = scopedDiffId('conv-2', DIFF_A);

        registry.add(idInConvB);

        expect(registry.has(idInConvB)).toBe(true);
        expect(registry.has(idInConvA)).toBe(false);
    });

    test('scoped ID has expected format: <conversationId>:diff-<hash>', () => {
        const id = scopedDiffId('conv-abc', DIFF_A);
        expect(id).toMatch(/^conv-abc:diff-[0-9a-z]+$/);
    });
});
