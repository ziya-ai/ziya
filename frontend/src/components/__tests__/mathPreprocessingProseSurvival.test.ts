/**
 * Prose-survival + fence-scope guard for the math preprocessing passes.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The math preprocessing passes in MarkdownRenderer used to share this shape:
 *
 *     source.split(/(```fence```)/g).map((part, idx) => {
 *         if (idx % 2 === 1 && part.startsWith('```')) return part;
 *         return applyOutsideCodeSpans(part, ...);   // <-- the payload
 *     }).join('')
 *
 * That idiom had TWO defects:
 *
 *   1. Silent prose deletion. If the payload `return` was ever lost (a
 *      truncated edit, a bad merge), the callback yielded `undefined`, and
 *      `Array.prototype.join` stringified it as the empty string — every
 *      non-fenced segment of every rendered message was silently DELETED
 *      with nothing in the console.
 *
 *   2. Backtick-only fence scope. The split regex `(```...```)` recognises
 *      ONLY backtick fences, so a TILDE (~~~) fenced code block was treated
 *      as ordinary prose: `$$...$$` in its body was extracted into a
 *      math-display div and the literal expression was DROPPED from the
 *      rendered code block (spec-3 defect spec3-d1).
 *
 * Both are fixed by routing the passes through the shared, CommonMark-aware
 * `applyOutsideFences` (fenceScanner.ts), which:
 *   - protects tilde fences exactly as it protects backtick fences, and
 *   - THROWS (rather than silently deleting) if a transform returns
 *     undefined, because it does `transform(buffer.join('\n')).split('\n')`.
 *
 * These tests are a mix of structural (read the renderer source, assert the
 * passes are wired through applyOutsideFences) and behavioural (run the real
 * fenceScanner helpers). MarkdownRenderer.tsx cannot be imported in isolation
 * here (it pulls in the whole component tree, KaTeX, Prism, mermaid, ...), and
 * a truncation produces VALID TypeScript that neither tsc nor a parse check
 * catches — only "is the pass wired correctly?" catches it.
 */
import * as fs from 'fs';
import * as path from 'path';

import { applyOutsideCodeSpans, applyOutsideFences } from '../fenceScanner';

const RENDERER = path.resolve(__dirname, '../MarkdownRenderer.tsx');
const source = fs.readFileSync(RENDERER, 'utf8');

/**
 * Return the slice of renderer source immediately preceding `anchor`, with
 * trailing whitespace and full-line `//` comments stripped. Used to assert
 * an anchor is wrapped by / assigned from the right construct.
 */
function windowBefore(anchor: string, chars = 600): string {
    const at = source.indexOf(anchor);
    expect(at).toBeGreaterThan(-1);
    return source.slice(Math.max(0, at - chars), at);
}

describe('math preprocessing: passes are scoped through applyOutsideFences', () => {
    // Anchors uniquely identifying each of the three math passes.
    // Uniquely the STANDALONE $$ pass (the ```latex/```math conversions use a
    // different callback signature), so windowBefore lands on the right pass.
    const DISPLAY_ENCODE = 'segment.replace(/\\$\\$([\\s\\S]*?)\\$\\$/g, (_match, innerContent)';
    const INLINE_PROTECT = 'mathStore.protect(segment)';
    const MARKER_EMIT = 'applyOutsideCodeSpans(processed, processInlineMath)';

    it('the $$ display-encode pass is wrapped by applyOutsideFences (tilde-aware)', () => {
        // Regression pin for spec3-d1: this pass previously used a
        // backtick-only split, so $$...$$ inside a ~~~ fence was extracted and
        // the literal expression dropped. It must now run outside ALL fences.
        expect(windowBefore(DISPLAY_ENCODE)).toContain('applyOutsideFences(');
    });

    it('the inline-$ protect pass is wrapped by applyOutsideFences (tilde-aware)', () => {
        expect(windowBefore(INLINE_PROTECT)).toContain('applyOutsideFences(');
    });

    it('the marker-emitting pass is wrapped by applyOutsideFences (tilde-aware)', () => {
        // The marker pass's applyOutsideFences opener sits well before the
        // MARKER_EMIT line (the multi-line $$ replace is in between), so pin
        // the opener directly. Its `segment => {` signature is unique to this
        // pass (the display and protect passes use `part =>`).
        expect(source).toContain('applyOutsideFences(processedMarkdown, segment => {');
        // And the marker emit lives inside it.
        expect(source).toContain(MARKER_EMIT);
    });

    it('no math pass falls back to a backtick-only fence split', () => {
        // The old, tilde-unaware idiom must not gate any math pass. If a future
        // edit reintroduces `.split(/(```...```)/g).map((part, idx) => {` to
        // feed math, this fails loudly.
        expect(source).not.toContain('.map((part, idx) => {');
    });

    it('the marker-emitting pass assigns the result of its transform', () => {
        // Losing the left-hand side would discard the markers entirely and
        // math would render literally.
        expect(source).toMatch(
            /processed\s*=\s*applyOutsideCodeSpans\(processed,\s*processInlineMath\)/,
        );
    });

    it('every applyOutsideCodeSpans / applyOutsideFences call consumes its result', () => {
        // A bare `applyOutside*(...)` statement would be a no-op (the helpers
        // are pure) or, for a lost assignment, a silent behaviour change. Each
        // call must be consumed: preceded by `return`, `=`, `=>`, or used as a
        // call argument — never a bare statement after `;`, `{`, or `}`.
        const calls = [
            ...source.matchAll(/applyOutsideCodeSpans\(/g),
            ...source.matchAll(/applyOutsideFences\(/g),
        ];
        // Three applyOutsideCodeSpans + several applyOutsideFences today.
        expect(calls.length).toBeGreaterThanOrEqual(6);
        for (const m of calls) {
            const before = source.slice(0, m.index!);
            // Strip trailing whitespace and any run of full-line // comments.
            const cleaned = before.replace(/(?:\s|\/\/[^\n]*\n?)*$/, '');
            const lastChar = cleaned.slice(-1);
            // A consumed call is never preceded by a statement terminator or a
            // block boundary. (import lines end with `{`/`,` and are excluded
            // by the `(` in the search pattern — the import has no open paren.)
            expect([';', '{', '}']).not.toContain(lastChar);
        }
    });
});

describe('applyOutsideFences: tilde fences protect their math body', () => {
    // Behavioural pin using the REAL fenceScanner. Mimics the $$ display-encode
    // transform and asserts a ~~~ fenced body is left byte-identical while a
    // $$ OUTSIDE any fence is transformed.
    const encode = (s: string): string =>
        s.replace(/\$\$([\s\S]*?)\$\$/g, () => '<<ENCODED>>');

    it('does NOT transform $$...$$ inside a ~~~ (tilde) fenced block', () => {
        const md = [
            'Outside: $$a=b$$',
            '',
            '~~~text',
            'This is not math:',
            '$$a^2 + b^2 = c^2$$',
            'stays literal.',
            '~~~',
        ].join('\n');
        const out = applyOutsideFences(md, encode);
        // The tilde-fenced expression survives verbatim...
        expect(out).toContain('$$a^2 + b^2 = c^2$$');
        // ...while the one outside the fence is transformed.
        expect(out).toContain('Outside: <<ENCODED>>');
    });

    it('still transforms $$...$$ inside a ```diff / ```python backtick fence body? no — leaves it literal', () => {
        // Backtick fences were already protected; confirm the reroute keeps
        // that behaviour (no regression on the ```diff / ```python cases).
        const md = [
            '```python',
            'x = "$$not math$$"',
            '```',
            'prose $$m=n$$ here',
        ].join('\n');
        const out = applyOutsideFences(md, encode);
        expect(out).toContain('x = "$$not math$$"');
        expect(out).toContain('prose <<ENCODED>> here');
    });

    it('throws rather than silently deleting when the transform returns undefined', () => {
        // The architectural safety property that replaces the old silent-
        // prose-deletion failure mode of the .map().join() idiom.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const bad = (() => undefined) as unknown as (s: string) => string;
        expect(() => applyOutsideFences('some prose\nmore prose', bad)).toThrow();
    });
});

describe('applyOutsideCodeSpans: total text conservation', () => {
    // Unchanged behavioural checks: the helper itself must be lossless, and
    // the tail segment after the LAST code span is the piece most easily
    // dropped (it needs an extra transform call after the loop).

    it('is the identity when the transform is the identity', () => {
        const samples = [
            'plain prose with no ticks at all',
            'leading `code` then prose',
            'prose then trailing `code`',
            'prose `a` middle `b` more `c` tail',
            '`code at very start` and prose',
            'prose and `code at very end`',
            'unmatched ` tick then prose',
            'multi ``span with ` inside`` then tail prose',
            'a\nb `c` d\ne',
        ];
        for (const s of samples) {
            expect(applyOutsideCodeSpans(s, x => x)).toBe(s);
        }
    });

    it('preserves the tail after the final code span', () => {
        const out = applyOutsideCodeSpans('start `mid` TAIL_MUST_SURVIVE', x => x);
        expect(out).toContain('TAIL_MUST_SURVIVE');
        expect(out.endsWith('TAIL_MUST_SURVIVE')).toBe(true);
    });

    it('applies the transform to the tail, not merely copying it', () => {
        const out = applyOutsideCodeSpans('a `keep` b', s => s.toUpperCase());
        expect(out).toBe('A `keep` B');
    });

    it('never shrinks total non-code text', () => {
        const src = 'one `x` two `y` three `z` four';
        const out = applyOutsideCodeSpans(src, x => x);
        for (const word of ['one', 'two', 'three', 'four']) {
            expect(out).toContain(word);
        }
    });
});
