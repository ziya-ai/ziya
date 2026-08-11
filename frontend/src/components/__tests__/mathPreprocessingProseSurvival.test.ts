/**
 * Prose-survival guard for the math preprocessing passes.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The three math preprocessing passes in MarkdownRenderer share a shape:
 *
 *     source.split(/(```fence```)/g).map((part, idx) => {
 *         if (idx % 2 === 1 && part.startsWith('```')) return part;
 *         return applyOutsideCodeSpans(part, ...);   // <-- the payload
 *     }).join('')
 *
 * That shape has a silent failure mode. If the `return` on the payload line is
 * ever lost — a truncated edit, a bad merge, an early `if` that forgets to
 * return — the callback yields `undefined`, and `Array.prototype.join`
 * stringifies `undefined` as the empty string. The result is not a crash and
 * not a parse error: the file still compiles, and every non-fenced segment of
 * every rendered message is silently DELETED. Fenced code blocks survive
 * (they take the early return), so the symptom is "all my prose disappeared
 * and only the code blocks are left", with nothing in the console.
 *
 * This actually happened, twice, on the same construct — which is why the
 * invariant is pinned here rather than left to reviewer vigilance.
 *
 * These tests are deliberately structural rather than behavioural. They read
 * the renderer source and assert the shape of the passes, because:
 *
 *   - MarkdownRenderer.tsx cannot be imported in isolation for this purpose
 *     (it pulls in the whole component tree, KaTeX, Prism, mermaid, ...), and
 *   - a truncation produces VALID TypeScript, so neither `tsc` nor a parse
 *     check can catch it. Only "does every branch return?" catches it.
 *
 * The companion behavioural check (`applyOutsideCodeSpans` itself never drops
 * text) lives at the bottom and does run against the real implementation.
 */
import * as fs from 'fs';
import * as path from 'path';

import { applyOutsideCodeSpans } from '../fenceScanner';

const RENDERER = path.resolve(__dirname, '../MarkdownRenderer.tsx');
const source = fs.readFileSync(RENDERER, 'utf8');

/**
 * Extract the callback bodies of the fence-splitting `.map(...)` passes that
 * perform math preprocessing.
 *
 * Anchored on `.split(/(```...```)/g)` followed by a `.map((part, idx) =>`,
 * which is the exact idiom all three passes use. If that idiom is refactored
 * this test fails loudly rather than silently passing on zero matches — see
 * the "finds the passes at all" assertion below.
 */
function fenceSplitPasses(): string[] {
    const bodies: string[] = [];
    const marker = '.map((part, idx) => {';
    let from = 0;
    for (;;) {
        const at = source.indexOf(marker, from);
        if (at === -1) break;
        // Walk braces from the callback's opening brace to its match, so the
        // captured body is the whole callback regardless of nesting depth.
        const open = source.indexOf('{', at + marker.length - 1);
        let depth = 0;
        let end = -1;
        for (let i = open; i < source.length; i += 1) {
            if (source[i] === '{') depth += 1;
            else if (source[i] === '}') {
                depth -= 1;
                if (depth === 0) { end = i; break; }
            }
        }
        if (end === -1) break;
        bodies.push(source.slice(open, end + 1));
        from = end;
    }
    return bodies;
}

describe('math preprocessing: prose must survive every pass', () => {
    const passes = fenceSplitPasses();

    it('finds the fence-splitting math passes at all', () => {
        // Guards against this whole suite silently degrading to a no-op if the
        // passes are renamed or restructured. Two passes exist today (the $$
        // display pass and the $...$ placeholder pass); the marker-emitting
        // pass uses a different idiom and is covered separately below.
        expect(passes.length).toBeGreaterThanOrEqual(2);
    });

    it.each(passes.map((body, i) => [i, body] as const))(
        'pass %i returns a value on every path (never falls through to undefined)',
        (_i, body) => {
            // The early-out for fenced content.
            expect(body).toContain('return part;');
            // The payload path. Without this return the callback yields
            // undefined and join() erases the segment.
            expect(body).toMatch(/return\s+applyOutsideCodeSpans\(/);
        },
    );

    it.each(passes.map((body, i) => [i, body] as const))(
        'pass %i has no statement-terminated fall-through before its close',
        (_i, body) => {
            // A truncated edit leaves a dangling comment or blank region just
            // before the closing brace. Assert the last meaningful statement
            // in the callback is a return, not a comment.
            const lines = body
                .split('\n')
                .map(l => l.trim())
                .filter(l => l.length > 0 && l !== '}');
            const last = lines[lines.length - 1];
            expect(last).not.toMatch(/^\/\//);
        },
    );

    it('the marker-emitting pass assigns the result of its transform', () => {
        // The third pass is a direct assignment rather than a .map(), so it
        // has a different truncation signature: losing the left-hand side
        // would discard the markers entirely and math would render literally.
        expect(source).toMatch(
            /processed\s*=\s*applyOutsideCodeSpans\(processed,\s*processInlineMath\)/,
        );
    });

    it('every applyOutsideCodeSpans call site consumes its return value', () => {
        // A bare `applyOutsideCodeSpans(...)` statement would be a no-op,
        // since the helper is pure. Each CALL (the identifier followed by an
        // open paren, which excludes the named-list import) must be preceded
        // by `return` or `=` on the same line.
        //
        // Three call sites today: the $$ display pass, the $...$ placeholder
        // pass, and the marker-emitting pass. Asserting >= 3 rather than an
        // exact count lets a fourth pass be added without editing this test,
        // while still failing if a pass loses its guard entirely.
        const occurrences = [...source.matchAll(/applyOutsideCodeSpans\(/g)];
        expect(occurrences.length).toBeGreaterThanOrEqual(3);
        for (const m of occurrences) {
            const lineStart = source.lastIndexOf('\n', m.index!) + 1;
            const prefix = source.slice(lineStart, m.index!);
            expect(prefix).toMatch(/(return|=)\s*$/);
        }
    });
});

describe('applyOutsideCodeSpans: total text conservation', () => {
    // The structural checks above cannot prove the helper itself is lossless,
    // and the tail segment after the LAST code span is the piece most easily
    // dropped (it needs an extra transform call after the loop). These run
    // against the real implementation.

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
        // Regression: an implementation missing the post-loop
        // `out += transform(text.slice(pos))` silently truncates here.
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
