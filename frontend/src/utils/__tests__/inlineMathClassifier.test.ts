import { isInlineMathContent, processInlineMath } from '../inlineMathClassifier';

describe('isInlineMathContent — genuine math', () => {
    it('single variables', () => {
        expect(isInlineMathContent('x')).toBe(true);
        expect(isInlineMathContent('A')).toBe(true);
        expect(isInlineMathContent('c')).toBe(true);
    });
    it('LaTeX commands (strong signal, bypass prose gate)', () => {
        expect(isInlineMathContent('\\frac{1}{2}')).toBe(true);
        expect(isInlineMathContent('\\alpha + \\beta')).toBe(true);
        // STRONG signal must win even with a run of English words inside \text
        expect(isInlineMathContent('\\text{the quick brown fox}')).toBe(true);
    });
    it('math symbols (strong signal)', () => {
        expect(isInlineMathContent('∑ from one to infinity')).toBe(true);
        expect(isInlineMathContent('|μ| ≤ 1')).toBe(true);
    });
    it('compact algebra (weak signal, no prose words)', () => {
        expect(isInlineMathContent('a + b')).toBe(true);
        expect(isInlineMathContent('x = 0')).toBe(true);
        expect(isInlineMathContent('Sc/r')).toBe(true);
    });
});

describe('isInlineMathContent — currency / prose rejection', () => {
    it('rejects currency runs from the reported lease bug', () => {
        expect(isInlineMathContent('900 refundable security deposit + ')).toBe(false);
        expect(isInlineMathContent('300 non-refundable cleaning fee** (= ')).toBe(false);
        expect(isInlineMathContent('200 after the 5th, +')).toBe(false);
        expect(isInlineMathContent('100 after the 8th, +')).toBe(false);
        expect(isInlineMathContent('75/day after) is far over')).toBe(false);
    });
    it('rejects regex back-references ($1, $2)', () => {
        expect(isInlineMathContent('1')).toBe(false);
        expect(isInlineMathContent('2')).toBe(false);
    });
    it('rejects code-context spans via the match guard', () => {
        expect(isInlineMathContent('x', 'foo.replace($x$, y)')).toBe(false);
        expect(isInlineMathContent('x', 'shell $x$ here')).toBe(false);
        expect(isInlineMathContent('a + b', 'run command $a + b$')).toBe(false);
    });
    it('does not treat URLs/paths as algebra', () => {
        expect(isInlineMathContent('https://example.com/a')).toBe(false);
        expect(isInlineMathContent('a/b://c')).toBe(false);
    });
    it('single-operator prose ("after the") is not algebra', () => {
        expect(isInlineMathContent('cats and dogs')).toBe(false);
    });
});

describe('processInlineMath — full segment transformation', () => {
    const LEASE = [
        'Deposit = $900 refundable security deposit + $300 non-refundable cleaning fee (= $1,200 total).',
        "The current draft ($200 after the 5th, +$100 after the 8th, +$75/day after) is far over Seattle's limit.",
    ].join('\n');

    it('emits no MATH_INLINE markers for the currency-laden lease text', () => {
        const out = processInlineMath(LEASE);
        expect(out).not.toContain('⟨MATH_INLINE');
    });

    it('leaves the lease text byte-identical', () => {
        expect(processInlineMath(LEASE)).toBe(LEASE);
    });

    it('still converts genuine inline math', () => {
        expect(processInlineMath('the value $x = 0$ holds'))
            .toContain('⟨MATH_INLINE:x = 0⟩');
        expect(processInlineMath('$\\frac{1}{2}$ cup'))
            .toContain('⟨MATH_INLINE:\\frac{1}{2}⟩');
        expect(processInlineMath('let $x$ vary'))
            .toContain('⟨MATH_INLINE:x⟩');
    });

    it('KaTeX adjacency kills the "$5 + $5" currency caveat at the source', () => {
        // space just inside a delimiter ⇒ never matched as a math span
        expect(processInlineMath('pay $5 + $5 today')).not.toContain('⟨MATH_INLINE');
    });

    it('adjacency: leading/trailing space inside delimiters is not math', () => {
        expect(processInlineMath('$ x = 0 $')).not.toContain('⟨MATH_INLINE');
    });

    it('mixed line: real math renders, adjacent currency stays literal', () => {
        const out = processInlineMath('cost $5 but $x$ is unknown');
        expect(out).toContain('⟨MATH_INLINE:x⟩');
        expect(out).toContain('cost $5 but');
    });
});
