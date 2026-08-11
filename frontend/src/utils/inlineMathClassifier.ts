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
 *
 * Once a span IS classified as math, its LaTeX must survive marked's lexer
 * intact. That transport problem is solved separately, by the base64 marker
 * encoding at the bottom of this file.
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
 * Inline-math marker: an opaque, markdown-inert envelope for a LaTeX payload.
 *
 * `processInlineMath` runs BEFORE marked's lexer, and the marker it emits is
 * ordinary markdown text. So whatever sits between the marker delimiters is
 * lexed as markdown before any renderer sees it, and markdown-active
 * characters in the LaTeX are destroyed:
 *
 *   `$\#_{\mathrm{E}}$`  ->  `\#` is a valid CommonMark backslash-escape, so
 *                            marked eats the backslash and the payload
 *                            reassembles as `#_{\mathrm{E}}`, which KaTeX
 *                            rejects: "Expected 'EOF', got '#'".
 *   `$a*b*c$`            ->  `*b*` is emphasis; the asterisks vanish -> `abc`.
 *
 * Escaping cannot fix the second class: there is no backslash to double.
 * Base64 is the only encoding that makes the payload inert, because its
 * alphabet (A-Za-z0-9+/=) contains no markdown-active character. This mirrors
 * the pattern already used for DISPLAY math (`math-display-encoded` /
 * `data-math`), so both math paths now defend themselves the same way.
 *
 * Marker shape:  ⟨MATH_INLINE_B64:<base64>⟩
 *
 * The `_B64` suffix is load-bearing, not cosmetic. Several legacy guards in
 * MarkdownRenderer match the bare substring `'MATH_INLINE:'` with a greedy
 * `[^<]*` capture that also swallows the closing `⟩`. Renaming the marker
 * means those guards no longer intercept it, so the angle-bracket-aware
 * extraction path is reached instead. `chatApi.ts` separately tests for the
 * substring `'MATH_INLINE'` (no colon) to avoid misreading math as a
 * throttling error; that still matches, which is why the prefix keeps
 * `MATH_INLINE` intact.
 *
 * The marker is generated per render and never persisted, so no
 * backward-compatibility shim is needed for stored conversations.
 */
export const MATH_INLINE_MARKER_PREFIX = '⟨MATH_INLINE_B64:';

/**
 * Matches one marker and captures its base64 payload.
 *
 * The payload class is deliberately restricted to the base64 alphabet rather
 * than `[\s\S]*?`. That makes the pattern self-validating: arbitrary text
 * that merely happens to contain the prefix cannot be mistaken for a marker,
 * and a payload corrupted mid-stream fails to match instead of decoding to
 * garbage.
 */
export const MATH_INLINE_MARKER_RE = /⟨MATH_INLINE_B64:([A-Za-z0-9+/]*={0,2})⟩/;

/**
 * Capturing/global variant for `String.prototype.split`, so a text run can be
 * partitioned into alternating literal and marker segments.
 */
export const MATH_INLINE_MARKER_SPLIT_RE = new RegExp(
    `(${MATH_INLINE_MARKER_RE.source})`, 'g',
);

/**
 * Wrap LaTeX in a marker.
 *
 * `btoa` alone throws on any codepoint above U+00FF, which would break Greek
 * letters and operators (α, ∑) — exactly the content most likely to be math.
 * The `unescape(encodeURIComponent(...))` sandwich first converts to UTF-8
 * bytes; it is the same idiom already used by the display-math encoder in
 * MarkdownRenderer, kept identical here so both paths share one convention.
 */
export function encodeInlineMathMarker(latex: string): string {
    return `${MATH_INLINE_MARKER_PREFIX}${btoa(unescape(encodeURIComponent(latex)))}⟩`;
}

/**
 * Recover the LaTeX from a marker, or null if the input is not a well-formed
 * marker or its payload does not decode.
 *
 * Returning null rather than throwing lets callers fall back to rendering the
 * original text: a decode failure should degrade to visible literal content,
 * never to an exception during render.
 */
export function decodeInlineMathMarker(marker: string): string | null {
    const m = marker.match(MATH_INLINE_MARKER_RE);
    if (!m) return null;
    try {
        return decodeURIComponent(escape(atob(m[1])));
    } catch {
        return null;
    }
}

/**
 * Whether a string produced by splitting on MATH_INLINE_MARKER_SPLIT_RE is a
 * marker segment. Checks the full shape, not just the prefix, so a truncated
 * marker arriving mid-stream is not treated as complete.
 */
export function isInlineMathMarker(segment: string): boolean {
    return segment.startsWith(MATH_INLINE_MARKER_PREFIX)
        && MATH_INLINE_MARKER_RE.test(segment);
}

/**
 * Replace every inline-math span in a (code-fence-free) markdown segment with
 * the renderer's marker, leaving non-math `$...$` spans untouched.
 *
 * The match regex enforces KaTeX adjacency (no space just inside either
 * delimiter), which kills the "$5 + $5" / "$900 ... + $300" currency
 * false-positives at the source — those spans are never even matched.
 */
export function processInlineMath(segment: string): string {
    return segment.replace(
        /\$(?=\S)([^$\n]+?)(?<=\S)\$/g,
        (match, p1) => (
            isInlineMathContent(p1, match)
                ? encodeInlineMathMarker(p1.trim())
                : match
        ),
    );
}
