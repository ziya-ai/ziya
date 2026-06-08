/**
 * Inline-math classification for the markdown renderer.
 *
 * A single `$...$` span is ambiguous: it may be KaTeX inline math
 * ($x = 0$, $\frac{1}{2}$) or incidental currency / prose
 * ("$900 deposit + $300 fee"). marked has no opinion, so MarkdownRenderer
 * decides per-span. This logic previously lived inline in a `.replace()`
 * callback and was therefore untestable; it is extracted here as the single
 * source of truth so the currency false-positive can be pinned by tests.
 *
 * Two layers of defence against currency false-positives:
 *   1. KaTeX adjacency (PROCESS regex): the char immediately after the
 *      opening ` and immediately before the closing `$` must be non-space.
 *      This is KaTeX's own rule and it eliminates "$900 ... + $300" and the
 *      "$5 + $5" caveat, because every such span has a space just inside a
 *      delimiter.
 *   2. Prose-word gate (isInlineMathContent): the WEAK math signals
 *      (algebraic operator, single var, braces) are suppressed when the span
 *      contains two or more English words of length >= 3. STRONG signals
 *      (explicit \latex commands, math symbols) bypass the gate so
 *      \text{the quick brown fox} still renders.
 */

// Greek letters + common math operators that unambiguously signal math.
const MATH_SYMBOLS = /[∫∑∏√∞≠≤≥±∓∈∉⊂⊃∪∩αβγδεζηθικλμνξοπρστυφχψω]/;

/**
 * Decide whether the text captured between single-`$` delimiters is real
 * inline math rather than incidental prose/currency.
 *
 * @param p1     content between the delimiters (no surrounding `$`)
 * @param match  full matched span including delimiters, used only for the
 *               code-context guard (regex/shell/command snippets)
 */
export function isInlineMathContent(p1: string, match: string = ''): boolean {
    // Regex back-references ($1, $2, ...) — never math.
    if (/^\d+$/.test(p1.trim())) return false;

    // Code-context guard: a `$...$` next to code-ish tokens is far more
    // likely shell/regex than math.
    const surrounding = match.substring(0, 50) +
        match.substring(Math.max(0, match.length - 50));
    if (surrounding.includes('replace(') ||
        surrounding.includes('processedDef') ||
        surrounding.includes('regex') ||
        surrounding.includes('command') ||
        surrounding.includes('shell')) {
        return false;
    }

    const hasLatex = /\\[a-zA-Z]+/.test(p1);                    // \frac, \sqrt, \alpha
    const hasMathSymbols = MATH_SYMBOLS.test(p1);
    const hasComplexMath = /[{}^_]/.test(p1) && p1.length > 2;  // sub/superscripts, braces
    const isSingleVariable = /^[A-Za-z]$/.test(p1.trim());      // $x$, $c$
    const hasAlgebraicNotation = /[A-Za-z]/.test(p1) &&
        /[/=<>+*|]/.test(p1) &&
        // Exclude URL-like or path-like strings
        !/^https?:/.test(p1.trim()) && !p1.includes('://');

    // Two or more multi-letter English words ⇒ prose, not algebra.
    const proseWordCount = (p1.match(/\b[A-Za-z]{3,}\b/g) || []).length;
    const looksLikeProse = proseWordCount >= 2;

    const strongMath = hasLatex || hasMathSymbols;
    const weakMath = hasComplexMath || isSingleVariable || hasAlgebraicNotation;

    return strongMath || (weakMath && !looksLikeProse);
}

/**
 * Replace every inline-math span in a (code-fence-free) markdown segment with
 * the renderer's `⟨MATH_INLINE:...⟩` marker, leaving non-math `$...$` spans
 * untouched.
 *
 * The match regex enforces KaTeX adjacency (no space just inside either
 * delimiter), which kills the "$5 + $5" / "$900 ... + $300" currency
 * false-positives at the source — those spans are never even matched.
 */
export function processInlineMath(segment: string): string {
    return segment.replace(
        /\$(?=\S)([^$\n]+?)(?<=\S)\$/g,
        (match, p1) => (
            isInlineMathContent(p1, match) ? `⟨MATH_INLINE:${p1.trim()}⟩` : match
        ),
    );
}
