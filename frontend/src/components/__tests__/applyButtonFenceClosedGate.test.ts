/**
 * The Apply button's streaming gate must key on THIS diff's fence being
 * CLOSED, not on the shape of the diff body.
 *
 * Symptom reported: in an answer containing several patches, one or more
 * Apply buttons stayed greyed until the whole response finished, while the
 * others enabled as soon as their block arrived. It looked random.
 *
 * Root cause: while the message was still streaming the gate ran
 * `isDiffComplete(diff, true)`, whose streaming branch guesses completeness
 * from the body's shape. Its `endsAbruptly` clause reads a fully-arrived
 * diff whose last line is a '+'/'-' line as "cut off mid-hunk". Most patches
 * end on a '+' line, so the subset of diffs that stayed disabled was
 * determined by whether the last hunk happened to end on a context line or a
 * blank line — hence the arbitrary appearance.
 *
 * Fix: derive the per-diff streaming flag from the marked code token's `raw`,
 * which contains the closing fence only once it has actually arrived. A
 * closed fence is deterministic and matches the stated intent: protect
 * against applying a half-arrived patch, but do not keep a settled patch
 * hostage to whatever else the message goes on to emit.
 *
 * Covered here:
 *  1. `isFenceClosed` itself.
 *  2. The heuristic-hostile shape that produced the symptom (positive
 *     control: the old streaming heuristic really does reject it).
 *  3. The composite disable predicate under the new gate, in both
 *     directions (closed fence → enabled, open fence → disabled).
 *  4. The seam: MarkdownRenderer's diff case actually derives and forwards
 *     the fence-closed flag to BOTH diff render paths.
 */

// ``marked`` is ESM-only and the CRA jest transform won't process it.
// Stub at module scope so importing the MarkdownRenderer module (which we
// only need for the pure ``isDiffComplete`` helper) doesn't fail when its
// top-level ``marked`` import resolves.
jest.mock('marked', () => {
    const marked = (s: string) => s;
    Object.assign(marked, {
        parse: (s: string) => s,
        setOptions: () => {},
        use: () => {},
        walkTokens: () => {},
        parseInline: (s: string) => s,
    });
    return { marked, Tokens: {} };
});
// ``uuid`` is also ESM-only and pulled in transitively via the
// FolderContext → ProjectContext → db.ts chain MarkdownRenderer imports.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import * as fs from 'fs';
import * as path from 'path';
import { isFenceClosed } from '../fenceScanner';
import { isApplyGated, isDiffComplete } from '../MarkdownRenderer';

/**
 * A complete, applicable patch whose last line is an ADDED line — the
 * overwhelmingly common shape, and the one the old streaming heuristic
 * misreads as truncated.
 */
const BODY_ENDING_ON_ADDED_LINE = [
    'diff --git a/foo.ts b/foo.ts',
    '--- a/foo.ts',
    '+++ b/foo.ts',
    '@@ -1,2 +1,2 @@',
    ' context line',
    '-old line',
    '+new line',
].join('\n');

/** The same patch, but ending on a context line, which the heuristic accepts. */
const BODY_ENDING_ON_CONTEXT_LINE = [
    'diff --git a/foo.ts b/foo.ts',
    '--- a/foo.ts',
    '+++ b/foo.ts',
    '@@ -1,2 +1,2 @@',
    '-old line',
    '+new line',
    ' trailing context',
].join('\n');

const fence = (body: string, closed: boolean): string =>
    '```diff\n' + body + (closed ? '\n```' : '\n');

describe('isFenceClosed', () => {
    it('is true when the closing fence has arrived', () => {
        expect(isFenceClosed(fence(BODY_ENDING_ON_ADDED_LINE, true))).toBe(true);
    });

    it('is true when the closing fence is followed by trailing whitespace', () => {
        expect(isFenceClosed(fence(BODY_ENDING_ON_ADDED_LINE, true) + '\n\n')).toBe(true);
    });

    it('is false while the block is still arriving', () => {
        expect(isFenceClosed(fence(BODY_ENDING_ON_ADDED_LINE, false))).toBe(false);
    });

    it('is false for the opener alone', () => {
        expect(isFenceClosed('```diff\n')).toBe(false);
    });

    it('is false for missing / empty raw, so callers fall back rather than assume settled', () => {
        expect(isFenceClosed(undefined)).toBe(false);
        expect(isFenceClosed(null)).toBe(false);
        expect(isFenceClosed('')).toBe(false);
        expect(isFenceClosed('   \n\t')).toBe(false);
    });

    it('is false for content that is not a fenced block at all', () => {
        expect(isFenceClosed('    indented code\n    more\n')).toBe(false);
        expect(isFenceClosed('just a paragraph\n')).toBe(false);
    });

    it('does not treat a diff-prefixed inner fence as the close', () => {
        // A patch to a markdown file carries the file's own fences as diff
        // lines. Those are body content; only the column-0 fence closes.
        const raw = [
            '```diff',
            'diff --git a/README.md b/README.md',
            '--- a/README.md',
            '+++ b/README.md',
            '@@ -1,3 +1,3 @@',
            '+```sql',
            '+SELECT 1;',
            '+```',
        ].join('\n');
        expect(isFenceClosed(raw)).toBe(false);
        expect(isFenceClosed(raw + '\n```')).toBe(true);
    });

    it('honours a widened outer fence (upgradeNestedFences output)', () => {
        const raw = ['````diff', '+```sql', '+SELECT 1;', '+```', '````'].join('\n');
        expect(isFenceClosed(raw)).toBe(true);
    });
});

describe('the shape sensitivity that produced the symptom', () => {
    // Positive control: without this the fence-closed gate below would be
    // asserting the absence of a problem that never existed.
    it('the old streaming heuristic rejects a settled diff ending on an added line', () => {
        expect(isDiffComplete(BODY_ENDING_ON_ADDED_LINE, true)).toBe(false);
    });

    it('...but accepts the same diff when it happens to end on a context line', () => {
        expect(isDiffComplete(BODY_ENDING_ON_CONTEXT_LINE, true)).toBe(true);
    });

    it('accepts both once streaming is over, which is why the symptom cleared at the end', () => {
        expect(isDiffComplete(BODY_ENDING_ON_ADDED_LINE, false)).toBe(true);
        expect(isDiffComplete(BODY_ENDING_ON_CONTEXT_LINE, false)).toBe(true);
    });
});

/**
 * The gate itself, asserted through the REAL exported derivation rather than a
 * hand-copy.  Three test files previously each carried a local model of this
 * predicate, and the gate has already regressed twice through changes that
 * satisfied every local model while the shipped code diverged.  The copies are
 * gone; all three now call isApplyGated.
 */
describe('isApplyGated', () => {
    it('enables a settled patch mid-message regardless of how its body ends', () => {
        // The reported symptom, directly: two patches differing ONLY in whether
        // the last hunk ends on an added line or a context line must agree.
        for (const body of [BODY_ENDING_ON_ADDED_LINE, BODY_ENDING_ON_CONTEXT_LINE]) {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: body,
                    raw: fence(body, true),
                }),
            ).toBe(false);
        }
    });

    it('still gates a patch whose fence has not closed yet', () => {
        // Both shapes, including the one the old heuristic would have released
        // early.  An open fence means the block can still change.
        for (const body of [BODY_ENDING_ON_ADDED_LINE, BODY_ENDING_ON_CONTEXT_LINE]) {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: body,
                    raw: fence(body, false),
                }),
            ).toBe(true);
        }
    });

    it('never gates once the message has settled', () => {
        for (const body of [BODY_ENDING_ON_ADDED_LINE, BODY_ENDING_ON_CONTEXT_LINE]) {
            for (const superseded of [false, true]) {
                expect(
                    isApplyGated({
                        messageStreaming: false,
                        superseded,
                        diff: body,
                        raw: fence(body, false),
                    }),
                ).toBe(false);
            }
        }
    });

    it('gates a superseded patch mid-message even though its fence closed', () => {
        // A retraction is expressed by a LATER diff in the same message, so it
        // is not knowable until the message ends.  Asserted for BOTH body
        // shapes: composing this term with isDiffComplete's shape heuristic
        // satisfied it for one shape and silently dropped it for the other.
        for (const body of [BODY_ENDING_ON_ADDED_LINE, BODY_ENDING_ON_CONTEXT_LINE]) {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: true,
                    diff: body,
                    raw: fence(body, true),
                }),
            ).toBe(true);
        }
    });

    it('does not let a superseded patch gate its siblings', () => {
        const shared = {
            messageStreaming: true,
            diff: BODY_ENDING_ON_ADDED_LINE,
            raw: fence(BODY_ENDING_ON_ADDED_LINE, true),
        };
        expect(isApplyGated({ ...shared, superseded: true })).toBe(true);
        expect(isApplyGated({ ...shared, superseded: false })).toBe(false);
    });

    describe('content validity, which is streaming-independent', () => {
        it('gates an empty diff even behind a closed fence', () => {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: '',
                    raw: '```diff\n```',
                }),
            ).toBe(true);
        });

        it('gates a continuation-truncated diff even behind a closed fence', () => {
            const truncated =
                BODY_ENDING_ON_ADDED_LINE + '\n<!-- ZIYA_CONTINUATION_INCOMPLETE -->';
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: truncated,
                    raw: fence(truncated, true),
                }),
            ).toBe(true);
        });
    });

    describe('fallback when the verbatim source is unavailable', () => {
        // The lexer's error path synthesizes a code token with no `raw`.
        // Treating unknown as settled would enable a half-arrived patch;
        // treating it as unsettled would restore the reported symptom for the
        // whole stream.  Fall back to the old shape heuristic for those only.
        it('uses the shape heuristic when raw is absent', () => {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: BODY_ENDING_ON_CONTEXT_LINE,
                }),
            ).toBe(false);
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: BODY_ENDING_ON_ADDED_LINE,
                }),
            ).toBe(true);
        });
    });

    describe('the `arrived` override, used by multi-file sections', () => {
        // renderMultiFileDiff re-wraps each section in a SYNTHETIC closed
        // fence, so a section's own raw always reads closed.  The parent
        // block's fence state is the only truthful arrival signal.
        it('honours arrived=false over a closed-looking raw', () => {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: BODY_ENDING_ON_ADDED_LINE,
                    raw: fence(BODY_ENDING_ON_ADDED_LINE, true),
                    arrived: false,
                }),
            ).toBe(true);
        });

        it('honours arrived=true over an open-looking raw', () => {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: false,
                    diff: BODY_ENDING_ON_ADDED_LINE,
                    raw: fence(BODY_ENDING_ON_ADDED_LINE, false),
                    arrived: true,
                }),
            ).toBe(false);
        });

        it('still gates a superseded section regardless of arrived', () => {
            expect(
                isApplyGated({
                    messageStreaming: true,
                    superseded: true,
                    diff: BODY_ENDING_ON_ADDED_LINE,
                    arrived: true,
                }),
            ).toBe(true);
        });
    });
});

/**
 * The button's own predicate must be a straight pass-through of the gate.
 * Re-deriving it through isDiffComplete's streaming heuristic is exactly what
 * defeated the superseded term: a superseded diff whose body ended on a context
 * line read as "complete" and enabled anyway.
 */
describe("ApplyChangesButton's disable predicate", () => {
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'MarkdownRenderer.tsx'),
        'utf8',
    );

    it('disables on isProcessing or the gate, with nothing in between', () => {
        const reason = source.match(/const disabledReason = isProcessing[\s\S]*?: null;/);
        expect(reason).not.toBeNull();
        expect(reason![0]).toMatch(/:\s*isStreaming\b/);
        expect(reason![0]).not.toContain('diffComplete');
    });

    it('keeps no heuristic copy inside the button', () => {
        expect(source).not.toMatch(/const\s+diffComplete\s*=\s*useMemo/);
        expect(source).not.toMatch(/const\s+shouldDisableButton\s*=/);
    });
});

/**
 * Seam coverage.  The pure gate above and the renderer can each be correct
 * while never meeting -- the exact failure class this gate has regressed
 * through twice.  Assert the wiring at the call sites rather than trusting it.
 */
describe('renderTokens wires the gate into both diff paths', () => {
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'MarkdownRenderer.tsx'),
        'utf8',
    );

    it('imports isFenceClosed from fenceScanner', () => {
        expect(source).toMatch(
            /import\s*\{[^}]*\bisFenceClosed\b[^}]*\}\s*from\s*'\.\/fenceScanner'/,
        );
    });

    it('exports isApplyGated as the single source of truth', () => {
        expect(source).toMatch(/export\s+const\s+isApplyGated\s*=/);
    });

    it('derives the single-file gate from isApplyGated with the token raw', () => {
        const call = source.match(/const\s+diffStreaming\s*=[\s\S]*?;\n/);
        expect(call).not.toBeNull();
        expect(call![0]).toContain('isApplyGated');
        expect(call![0]).toContain('tokenWithText.raw');
        expect(call![0]).toContain('singleFileSuperseded');
    });

    it('passes an already-decided flag straight through on a sub-render', () => {
        expect(source).toMatch(/isSubRender\s*\?\s*isStreaming\s*:/);
    });

    it('forwards the parent fence state to the multi-file path', () => {
        expect(source).toMatch(/renderMultiFileDiff\([\s\S]*?fenceClosed\)/);
    });

    it('forwards the gate to the single-file DiffToken', () => {
        const singleFile = source.match(/return <DiffToken key=\{sk\}[^;]*;/);
        expect(singleFile).not.toBeNull();
        expect(singleFile![0]).toContain('isStreaming={diffStreaming}');
    });
});

/**
 * The multi-file path specifically.  Its per-section gate is the half most
 * likely to be dropped: single-file diffs keep working while every section of a
 * multi-file diff silently loses the superseded exception.
 */
describe('renderMultiFileDiff applies the gate per file', () => {
    const source = fs.readFileSync(
        path.join(__dirname, '..', 'MarkdownRenderer.tsx'),
        'utf8',
    );

    it('accepts a fenceClosed parameter', () => {
        const sig = source.match(/const renderMultiFileDiff = \([\s\S]*?\): JSX\.Element =>/);
        expect(sig).not.toBeNull();
        expect(sig![0]).toMatch(/fenceClosed:\s*boolean/);
    });

    it('derives each section gate from isApplyGated, keyed on that section', () => {
        const call = source.match(/const\s+fileStreaming\s*=[\s\S]*?\}\);\n/);
        expect(call).not.toBeNull();
        expect(call![0]).toContain('isApplyGated');
        expect(call![0]).toContain('supersededFileIndices.has(fileIndex)');
        // The PARENT block's fence state, not the synthetic per-section re-wrap.
        expect(call![0]).toMatch(/arrived:\s*fenceClosed/);
    });

    it('forwards the per-section gate to the nested renderer', () => {
        expect(source).toMatch(/isStreaming=\{fileStreaming\}/);
    });
});
