/**
 * Tests for formatFileWrite, reached via the exported formatMCPOutput.
 *
 * Why this file exists
 * --------------------
 * formatFileWrite's expandable body was once implemented twice in a single
 * commit: an earlier find/replace version and a later unified-diff version. The
 * later block was pasted in without deleting the former, and a stray closing
 * brace left the whole second implementation inside `if (!body) { ... }` after an
 * unconditional `return`. It compiled cleanly, because the re-declared
 * `writtenContent`/`patchFind` were in an inner block scope, so tsc saw no
 * duplicate `const`. Only ESLint's no-unreachable flagged it.
 *
 * The user-visible consequence: the `renderAs: 'diff'` assignment is the only
 * producer of that flag anywhere in the tree, and it sat in the dead region. So
 * ToolDiffPreview was unreachable and patch results rendered as plain
 * `--- find --- / --- replace ---` text instead of a colored diff.
 *
 * These tests pin the intended contract so the dead branch cannot silently
 * reappear, and lock the paths that were not supposed to change (full write,
 * empty content, errors) against over-correction. Verified to discriminate:
 * 7 fail against the pre-fix file, 0 against the fixed one.
 *
 * Consumer coupling: MarkdownRenderer reads `renderAs === 'diff'` and routes to
 * react-diff-view. Its <Diff> sets gutterType="none", which is why the
 * synthesized `@@ -1,N +1,M @@` header is tolerable — see the note on the
 * line-1 test below.
 */
import { formatMCPOutput } from '../mcpFormatter';

/** Successful patch result: `input.patch` present means patch mode. */
function patchCall(overrides: { patch?: string; content?: string } = {}) {
    return formatMCPOutput(
        'file_write',
        {
            path: 'src/a.ts',
            bytes_written: 120,
            message: 'Updated src/a.ts (replaced 1 of 1)',
        },
        {
            path: 'src/a.ts',
            patch: overrides.patch ?? 'old line',
            content: overrides.content ?? 'new line',
        },
        {},
    );
}

/** n lines of throwaway text, for exercising the collapse thresholds. */
const lines = (n: number) => Array.from({ length: n }, (_, i) => `l${i}`).join('\n');

describe('file_write patch mode routes to the diff renderer', () => {
    it('sets renderAs="diff" so MarkdownRenderer uses react-diff-view', () => {
        // The regression that motivated this file: renderAs was undefined
        // because its only assignment sat after an unconditional return.
        expect(patchCall().renderAs).toBe('diff');
    });

    it('emits a parseable unified diff', () => {
        const { content } = patchCall();
        expect(content).toContain('diff --git a/src/a.ts b/src/a.ts');
        expect(content).toContain('--- a/src/a.ts');
        expect(content).toContain('+++ b/src/a.ts');
        expect(content).toContain('-old line');
        expect(content).toContain('+new line');
    });

    it('no longer emits the superseded find/replace transcript', () => {
        const { content } = patchCall();
        expect(content).not.toContain('--- find ---');
        expect(content).not.toContain('--- replace ---');
    });

    it('keeps the byte and occurrence summary alongside the diff', () => {
        // The diff occupies `content`, so the human-readable line has to
        // survive in `summary` or the byte count vanishes from the UI.
        const { summary } = patchCall();
        expect(summary).toContain('src/a.ts');
        expect(summary).toContain('120 bytes');
        expect(summary).toContain('1 of 1 occurrence replaced');
    });

    it('counts hunk lines from the actual find and replace text', () => {
        const { content } = patchCall({ patch: 'a\nb\nc', content: 'x\ny' });
        expect(content).toContain('@@ -1,3 +1,2 @@');
        expect(content).toContain('-a\n-b\n-c');
        expect(content).toContain('+x\n+y');
    });

    it('documents that the hunk header always claims line 1', () => {
        // Known limitation, not desired behavior. The formatter receives
        // `input` but not the offset where `patch` matched, so it cannot emit a
        // true start line. Harmless only because <Diff gutterType="none">
        // hides the gutter. If gutters are ever enabled, the displayed line
        // numbers will be wrong and this test should be revisited.
        expect(patchCall().content).toMatch(/^@@ -1,\d+ \+1,\d+ @@$/m);
    });

    it('falls back to a placeholder path when none is reported', () => {
        const out = formatMCPOutput(
            'file_write',
            { bytes_written: 10, message: 'Updated' },
            { patch: 'old', content: 'new' },
            {},
        );
        expect(out.renderAs).toBe('diff');
        expect(out.content).toContain('diff --git a/file b/file');
    });

    it('leaves a short diff expanded', () => {
        expect(patchCall().collapsed).toBe(false);
    });

    it('collapses a long diff', () => {
        expect(patchCall({ patch: lines(6), content: lines(6) }).collapsed).toBe(true);
    });

    it('honours defaultCollapsed=false even for a long diff', () => {
        const out = formatMCPOutput(
            'file_write',
            { path: 'src/a.ts', bytes_written: 900, message: 'Updated src/a.ts' },
            { path: 'src/a.ts', patch: lines(20), content: lines(20) },
            { defaultCollapsed: false },
        );
        expect(out.collapsed).toBe(false);
    });

    it('parses a JSON string result before formatting', () => {
        const out = formatMCPOutput(
            'file_write',
            JSON.stringify({
                path: 'src/f.ts',
                bytes_written: 7,
                message: 'Updated src/f.ts (replaced 1 of 1)',
            }),
            { path: 'src/f.ts', patch: 'a', content: 'b' },
            {},
        );
        expect(out.renderAs).toBe('diff');
        expect(out.content).toContain('-a');
        expect(out.content).toContain('+b');
    });
});

describe('file_write paths that must not change', () => {
    // Guards against over-correction. All of these already passed before the
    // dead code was removed, and must keep passing.
    it('shows full-write content as plain text, never as a diff', () => {
        const out = formatMCPOutput(
            'file_write',
            { path: 'src/b.ts', bytes_written: 40, message: 'Created src/b.ts' },
            { path: 'src/b.ts', content: 'hello\nworld' },
            {},
        );
        // A synthetic diff here would be all-additions and misleading, since
        // the previous file content is not available to the formatter.
        expect(out.renderAs).toBeUndefined();
        expect(out.content).toBe('hello\nworld');
        expect(out.summary).toContain('new file');
    });

    it('keeps the compact form when there is no content at all', () => {
        const out = formatMCPOutput(
            'file_write',
            { path: 'src/c.ts', bytes_written: 0, message: 'Updated src/c.ts' },
            { path: 'src/c.ts' },
            {},
        );
        expect(out.renderAs).toBeUndefined();
        expect(out.collapsed).toBe(false);
        expect(out.content).toContain('src/c.ts');
        expect(out.content).toContain('0 bytes');
    });

    it('treats an empty patch string as a full write, not a patch', () => {
        // isPatch is truthiness-based, so '' must not reach the diff branch.
        const out = formatMCPOutput(
            'file_write',
            { path: 'src/d.ts', bytes_written: 5, message: 'Updated src/d.ts' },
            { path: 'src/d.ts', patch: '', content: 'body' },
            {},
        );
        expect(out.renderAs).toBeUndefined();
        expect(out.content).toBe('body');
    });
});

describe('file_write error results', () => {
    it('surfaces a string error and never routes it to the diff renderer', () => {
        const out = formatMCPOutput(
            'file_write',
            { error: 'Permission denied' },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(out.type).toBe('error');
        expect(out.renderAs).toBeUndefined();
        expect(out.content).toContain('Permission denied');
    });

    it('reads the message when error is the bare boolean true', () => {
        // Regression guard. formatMCPOutput's generic error handler runs before
        // per-tool dispatch, so it claims every result with a truthy `error` --
        // the error branches inside formatFileWrite and formatAstTool never see
        // one. It used to interpolate `result.error` directly, rendering the
        // useless "Error: true" and discarding the real reason. 130 backend
        // returns across app/mcp use this {error: true, message: ...} shape.
        const out = formatMCPOutput(
            'file_write',
            { error: true, message: 'Permission denied' },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(out.type).toBe('error');
        expect(out.content).toContain('Permission denied');
        expect(out.content).not.toContain('Error: true');
    });

    it('falls back to detail, then to a placeholder, when message is absent', () => {
        const withDetail = formatMCPOutput(
            'file_write',
            { error: true, detail: 'ENOSPC: no space left on device' },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(withDetail.content).toContain('ENOSPC: no space left on device');
        // detail was promoted to the headline, so it must not also be appended.
        // The path is appended because the message does not already name it --
        // context the per-tool formatFileWrite branch used to add before it was
        // folded into the single error handler.
        expect(withDetail.content).toBe(
            '❌ Error: ENOSPC: no space left on device (src/e.ts)',
        );

        const bare = formatMCPOutput(
            'file_write',
            { error: true },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(bare.content).toContain('Unknown error');
        expect(bare.content).not.toContain('true');
    });

    it('appends path context, then detail, without a trailing newline', () => {
        const out = formatMCPOutput(
            'file_write',
            { error: 'Write failed', detail: 'at fs.writeFileSync' },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        // Order matters: path context belongs on the headline, detail below it.
        expect(out.content).toBe(
            '❌ Error: Write failed (src/e.ts)\nat fs.writeFileSync',
        );

        // No detail must not leave a dangling newline, as the old template did.
        const noDetail = formatMCPOutput(
            'file_write',
            { error: 'Write failed' },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(noDetail.content).toBe('❌ Error: Write failed (src/e.ts)');
    });

    it('omits path context when the message already names the file', () => {
        const out = formatMCPOutput(
            'file_write',
            { error: true, message: 'Cannot write src/e.ts: read-only' },
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(out.content).toBe('❌ Error: Cannot write src/e.ts: read-only');
    });

    it('recovers an error delivered as a JSON string instead of an object', () => {
        // MarkdownRenderer hands file_* and ast_* results through as plain text,
        // so a JSON string used to slip past the object-only guard and leak raw
        // JSON to the user.
        const out = formatMCPOutput(
            'file_write',
            JSON.stringify({ error: true, message: 'Permission denied' }),
            { path: 'src/e.ts', content: 'x' },
            {},
        );
        expect(out.type).toBe('error');
        expect(out.content).toBe('❌ Error: Permission denied (src/e.ts)');
        expect(out.content).not.toContain('{');
    });
});
