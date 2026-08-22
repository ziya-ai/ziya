/**
 * Round-trip contract for the inline-math PLACEHOLDER store.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * MarkdownRenderer protects every `$...$` span behind an opaque placeholder
 * before running `escapeNestedBacktickFences`, then puts the spans back. This
 * is a pure protect/restore round-trip — it makes no judgement about what is
 * math (that is `isInlineMathContent`'s job, in a later pass) — so its ONLY
 * contract is byte-identity: whatever went in must come back out unchanged.
 *
 * The round-trip previously violated that contract in two ways, both confirmed
 * by direct measurement rather than inspection:
 *
 *   1. It restored with `String.replace(placeholder, content)`, i.e. a STRING
 *      replacement. In a string replacement `$&`, `$'` and `` $` `` are
 *      special, and `content` always begins with `$`. So any span whose first
 *      inner character was `&`, `'` or a backtick was mangled — and two of the
 *      three splice in UNRELATED surrounding text rather than merely dropping
 *      characters:
 *
 *        "prime $'a$ here"  ->  "prime  herea$ here"     ($' = text after match)
 *        "tick $`a$ here"   ->  "tick tick a$ here"      ($` = text before match)
 *        "align $&x$ here"  ->  "align __MATH_INLINE_0__x$ here"
 *
 *   2. The placeholder was `__MATH_INLINE_<n>__`, with `n` a per-render
 *      counter. Source text containing that literal token therefore collided
 *      with a live placeholder, and because restore replaced the FIRST
 *      occurrence, the math was relocated INTO the colliding text while the
 *      real slot kept its placeholder:
 *
 *        "log `mark.__MATH_INLINE_0__{fallback}` and math $x$ after"
 *          ->  "log `mark.$x${fallback}` and math __MATH_INLINE_0__ after"
 *
 *      This is not hypothetical. `frontend/src/plugins/d3/vegaLitePlugin.ts`
 *      has three lines containing exactly such tokens (committed there by an
 *      earlier round-trip that lost its restore), so rendering that file in a
 *      chat reaches the collision.
 *
 * These tests exercise the store directly. The equivalent logic inlined in
 * MarkdownRenderer was untestable — the module cannot be imported in isolation
 * (it pulls in KaTeX, Prism, mermaid and the whole component tree), which is
 * why both defects survived in a file that already has math tests.
 */
import * as fs from 'fs';
import * as path from 'path';

import { createMathPlaceholderStore } from '../inlineMathClassifier';

/**
 * The pre-fix restore, kept as a WITNESS. It exists to pin why the store must
 * use a function replacement and a collision-proof placeholder: if someone
 * "simplifies" restore back to a string replacement, the contrast between this
 * function and the real store is the documentation of what breaks.
 *
 * It is never used by production code and is asserted to CORRUPT, so it cannot
 * be mistaken for a passing implementation.
 */
function legacyRoundTrip(md: string): string {
    const EXTRACT = /(?<!\$)\$(?!\$)((?:(?!\$).)+?)\$(?!\$)/g;
    const blocks: { placeholder: string; content: string }[] = [];
    let counter = 0;
    let out = md.replace(EXTRACT, match => {
        const placeholder = `__MATH_INLINE_${counter}__`;
        blocks.push({ placeholder, content: match });
        counter++;
        return placeholder;
    });
    for (const { placeholder, content } of blocks) {
        out = out.replace(placeholder, content);   // string replacement — the bug
    }
    return out;
}

/** Protect then restore in one step, the way the renderer uses the store. */
function roundTrip(md: string): string {
    const store = createMathPlaceholderStore();
    return store.restore(store.protect(md));
}

describe('math placeholder store — byte-identical round-trip', () => {
    it('round-trips ordinary inline math', () => {
        for (const md of [
            'the value $x = 0$ holds',
            '$\\frac{1}{2}$ cup',
            'two spans $a$ and $b$ here',
            'no math at all',
        ]) {
            expect(roundTrip(md)).toBe(md);
        }
    });

    it('round-trips spans whose first character is a replacement-pattern special', () => {
        // Each of these was corrupted by the string replacement. `$'` and the
        // backtick form are the severe cases: they splice in text from OUTSIDE
        // the span, so the damage is not confined to the math.
        expect(roundTrip("align $&x$ here")).toBe("align $&x$ here");
        expect(roundTrip("prime $'a$ here")).toBe("prime $'a$ here");
        expect(roundTrip('tick $`a$ here')).toBe('tick $`a$ here');
        expect(roundTrip('all $&$ and $$$ mixed')).toBe('all $&$ and $$$ mixed');
    });

    it('round-trips source text that already contains a placeholder-shaped token', () => {
        // Reachable today: this exact token shape is committed in
        // frontend/src/plugins/d3/vegaLitePlugin.ts.
        const md = 'log `mark.__MATH_INLINE_0__{fallback}` and math $x$ after';
        expect(roundTrip(md)).toBe(md);
    });

    it('keeps math in its ORIGINAL position, not merely present somewhere', () => {
        // A round-trip that relocated the span would still satisfy a
        // "contains $x$" assertion, which is why identity is asserted above
        // and position is asserted explicitly here.
        const md = 'before __MATH_INLINE_0__ middle $x$ after';
        const out = roundTrip(md);
        expect(out.indexOf('$x$')).toBe(md.indexOf('$x$'));
    });
});

describe('math placeholder store — the protect step actually ran', () => {
    it('removes every dollar delimiter, so fence escaping cannot see the math', () => {
        // Paired with the identity assertions above: without this, a store
        // whose protect() was a no-op would pass every round-trip test while
        // providing no protection at all.
        const store = createMathPlaceholderStore();
        const protectedText = store.protect('the value $x = 0$ holds');
        expect(protectedText).not.toContain('$');
        expect(protectedText).not.toContain('x = 0');
    });

    it('shares one counter across successive protect() calls', () => {
        // The renderer calls protect() once per non-fence segment, so distinct
        // segments must not be handed the same placeholder.
        const store = createMathPlaceholderStore();
        const a = store.protect('first $a$ span');
        const b = store.protect('second $b$ span');
        expect(a).not.toBe(b);
        expect(store.restore(a)).toBe('first $a$ span');
        expect(store.restore(b)).toBe('second $b$ span');
    });

    it('uses a placeholder that is inert for the backtick-fence escaper', () => {
        const store = createMathPlaceholderStore();
        const protectedText = store.protect('math $a$ here');
        expect(protectedText).not.toContain('`');
        expect(protectedText).not.toContain('$');
    });

    it('restore is a no-op when nothing was protected', () => {
        const store = createMathPlaceholderStore();
        expect(store.restore('plain text __MATH_INLINE_0__ untouched'))
            .toBe('plain text __MATH_INLINE_0__ untouched');
    });

    it('two stores do not share placeholders', () => {
        // Collision-proofing must be per-store, not per-counter: two renders
        // in flight would otherwise mint identical placeholders.
        const a = createMathPlaceholderStore();
        const b = createMathPlaceholderStore();
        expect(a.protect('$x$')).not.toBe(b.protect('$y$'));
    });
});

/**
 * Source-hygiene guard.
 *
 * A placeholder is a RENDER-TIME artifact: it should never exist on disk. It
 * does, in three places, because renderer output was written back into source
 * with the round-trip's restore step missing. That is how
 * `frontend/src/plugins/d3/vegaLitePlugin.ts` ended up with template literals
 * whose `${...}` expressions had been eaten:
 *
 *     console.log(`...Gradient in mark.__MATH_INLINE_56__{fallback}`)
 *
 * The damage is cosmetic (log strings), but the token on disk is also the
 * collision source for the round-trip bug above, so leaving it would keep that
 * defect reachable. This guard is structural on purpose: the corruption
 * produces VALID TypeScript, so no compile or parse check can find it.
 */
describe('no placeholder tokens are committed to source', () => {
    const SRC = path.resolve(__dirname, '..', '..');
    // Files that legitimately name the token: the implementation and its tests.
    const ALLOWED = [
        path.join('utils', 'inlineMathClassifier.ts'),
        path.join('utils', '__tests__', 'mathPlaceholderStore.test.ts'),
    ];

    const walk = (dir: string, acc: string[] = []): string[] => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                if (entry.name === 'node_modules') continue;
                walk(full, acc);
            } else if (/\.(ts|tsx|js|jsx)$/.test(entry.name)) {
                acc.push(full);
            }
        }
        return acc;
    };

    it('finds no __MATH_INLINE_<n>__ token anywhere under src/', () => {
        const offenders: string[] = [];
        for (const file of walk(SRC)) {
            const rel = path.relative(SRC, file);
            if (ALLOWED.some(a => rel.endsWith(a))) continue;
            const text = fs.readFileSync(file, 'utf8');
            text.split('\n').forEach((line, i) => {
                if (line.includes('__MATH_INLINE_')) {
                    offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
                }
            });
        }
        expect(offenders).toEqual([]);
    });
});

describe('witness: the pre-fix string replacement is what corrupted these', () => {
    it('legacy round-trip mangles specials and splices outside text', () => {
        expect(legacyRoundTrip("align $&x$ here")).not.toBe("align $&x$ here");
        expect(legacyRoundTrip("prime $'a$ here")).toBe('prime  herea$ here');
        expect(legacyRoundTrip('tick $`a$ here')).toBe('tick tick a$ here');
    });

    it('legacy round-trip relocates math into colliding text', () => {
        expect(legacyRoundTrip('log `mark.__MATH_INLINE_0__{fallback}` and math $x$ after'))
            .toBe('log `mark.$x${fallback}` and math __MATH_INLINE_0__ after');
    });

    it('legacy round-trip is correct for plain math (so the fix is not a rewrite)', () => {
        expect(legacyRoundTrip('the value $x = 0$ holds')).toBe('the value $x = 0$ holds');
    });
});
