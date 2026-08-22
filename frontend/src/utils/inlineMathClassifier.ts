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

    // Currency / markdown-structure guard. In a table row like
    //   | $255,100/yr | **$320,000/yr** |
    // the span between the two `$` is `255,100/yr | **`, which passes KaTeX
    // adjacency (digit after the opener, `*` before the closer) and trips
    // hasAlgebraicNotation via "yr" + "/", while the prose gate stays silent
    // because "yr" is under three letters. It then swallows a cell `|`, so the
    // row loses a column and the marker payload leaks into the output.
    //
    // `**` inside a span means the match crossed a bold delimiter rather than
    // enclosing an expression; `**` is meaningless in LaTeX, so rejecting it
    // outright is safe. Comma-grouped digits are a currency spelling that does
    // not occur in bare LaTeX, so reject those too unless a command is present.
    if (/\*\*/.test(p1)) return false;
    if (/\d,\d{3}(?!\d)/.test(p1) && !hasLatex) return false;

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
const MARKER_PAYLOAD_PATTERN = '[A-Za-z0-9+/]*={0,2}';

export const MATH_INLINE_MARKER_RE = new RegExp(
    `${MATH_INLINE_MARKER_PREFIX}(${MARKER_PAYLOAD_PATTERN})⟩`,
);

/**
 * Capturing/global variant for `String.prototype.split`, so a text run can be
 * partitioned into alternating literal and marker segments.
 *
 * This pattern must contain EXACTLY ONE capture group. It is deliberately
 * built from MARKER_PAYLOAD_PATTERN rather than by wrapping
 * MATH_INLINE_MARKER_RE.source in parentheses: that produced a NESTED group,
 * and `String.prototype.split` emits every capture as its own segment. The
 * payload therefore appeared twice — once inside the marker segment (rendered
 * as math) and once alone (rendered as literal text), so every inline span
 * was followed by its own visible base64, e.g.
 * "\mu^{-}" rendering as "μ⁻XGxhbWJkYSBcdG8gXG11XnstfQ==".
 */
export const MATH_INLINE_MARKER_SPLIT_RE = new RegExp(
    `(${MATH_INLINE_MARKER_PREFIX}${MARKER_PAYLOAD_PATTERN}⟩)`, 'g',
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
 * A GFM table delimiter row: `|---|---|`, `| :--- | ---: |`, `---|---`.
 *
 * Requires BOTH a pipe and a hyphen, which is what separates it from a
 * thematic break (`---`, no pipe) and from a data row (`| a | b |`, letters).
 */
function isTableDelimiterRow(line: string): boolean {
    return line.includes('|') && line.includes('-') && /^[\s|:-]+$/.test(line);
}

/**
 * Flag every line belonging to a GFM table block (header, delimiter, body).
 *
 * Scoping the cell-split to real table blocks is what keeps a pipe inside
 * genuine prose math ("the value $a|b$ holds") intact: with no delimiter row
 * there is no table, so the line is processed as a single span scope.
 */
function markTableRows(lines: string[]): boolean[] {
    const isRow = lines.map(() => false);
    for (let i = 1; i < lines.length; i++) {
        if (!isTableDelimiterRow(lines[i]) || !lines[i - 1].includes('|')) continue;
        isRow[i - 1] = true;
        isRow[i] = true;
        for (let j = i + 1; j < lines.length; j++) {
            if (lines[j].trim() === '' || !lines[j].includes('|')) break;
            isRow[j] = true;
        }
    }
    return isRow;
}

/** Split a table row on unescaped `|` only, so an escaped `\|` stays in-cell. */
const TABLE_CELL_SPLIT_RE = /(?<!\\)\|/;

/**
 * Replace every inline-math span in ONE span scope with the renderer's marker,
 * leaving non-math `$...$` spans untouched.
 *
 * The match regex enforces KaTeX adjacency (no space just inside either
 * delimiter), which kills the "$5 + $5" / "$900 ... + $300" currency
 * false-positives at the source — those spans are never even matched.
 */
function replaceMathSpans(scope: string): string {
    return scope.replace(
        /\$(?=\S)([^$\n]+?)(?<=\S)\$/g,
        (match, p1) => (
            isInlineMathContent(p1, match)
                ? encodeInlineMathMarker(p1.trim())
                : match
        ),
    );
}

/**
 * Replace every inline-math span in a (code-fence-free) markdown segment.
 *
 * Inside a table row each CELL is its own span scope. A `$...$` match is
 * otherwise free to open in one cell and close in the next, and the marker
 * then swallows the separating `|`:
 *
 *   | $99/yr | *$120/yr* |  ->  | ⟨MATH_INLINE_B64:OTkveXIgfCAq⟩120/yr* |
 *
 * The span there is `99/yr | *`, which passes KaTeX adjacency and reads as
 * algebra ("yr" + "/") with no prose words to veto it. marked then pads the
 * short row back out to the header width, so the column COUNT still looks
 * correct while the second cell has silently gone empty and the first holds
 * the merged text.
 *
 * Splitting on unescaped pipes and rejoining with `|` round-trips exactly, so
 * a row containing no math is returned byte-identical.
 */
export function processInlineMath(segment: string): string {
    const lines = segment.split('\n');
    const isRow = markTableRows(lines);
    return lines
        .map((line, i) => (isRow[i]
            ? line.split(TABLE_CELL_SPLIT_RE).map(replaceMathSpans).join('|')
            : replaceMathSpans(line)))
        .join('\n');
}

/**
 * Opaque protect/restore round-trip for inline-math spans.
 *
 * MarkdownRenderer runs `escapeNestedBacktickFences` over the whole document,
 * and that pass would otherwise see — and mangle — delimiters inside math. So
 * every `$...$` span is lifted out behind a placeholder first and put back
 * afterwards. The store makes no judgement about what IS math; classification
 * happens later, in `processInlineMath`. Its only contract is byte-identity.
 *
 * Two properties are load-bearing and neither is visible at the call site,
 * which is why this lives here rather than inline in the renderer:
 *
 *   1. Restore must NOT use a string replacement. The stored content always
 *      begins with a dollar sign, and in a replacement string the sequences
 *      dollar-ampersand, dollar-apostrophe and dollar-backtick are special.
 *      A span like $'a$ was therefore restored as the text FOLLOWING the
 *      match, splicing unrelated document text into the math. split/join
 *      treats the replacement as a literal and removes the whole class.
 *
 *   2. The placeholder must not collide with text that already looks like a
 *      placeholder. A bare __MATH_INLINE_<n>__ did: restore replaced the
 *      FIRST occurrence, so a document containing that token had its math
 *      relocated INTO the token while the real slot kept the placeholder.
 *      Rendering vegaLitePlugin.ts reached exactly this. A per-store token
 *      makes the collision unreachable.
 */
export interface MathPlaceholderStore {
    /** Replace every `$...$` span in one segment with an opaque placeholder. */
    protect(segment: string): string;
    /** Put every protected span back, byte for byte. */
    restore(text: string): string;
}

/**
 * Random per-process component of the placeholder token, combined with a
 * monotonic per-store sequence so two stores alive at once can never mint the
 * same token, while the token stays unguessable from outside.
 */
const PLACEHOLDER_NONCE = Math.random().toString(36).slice(2, 10);
let placeholderStoreSeq = 0;

export function createMathPlaceholderStore(): MathPlaceholderStore {
    const blocks: { placeholder: string; content: string }[] = [];
    const storeId = `${PLACEHOLDER_NONCE}${placeholderStoreSeq++}`;
    let counter = 0;

    return {
        protect(segment: string): string {
            // Negative lookbehind/lookahead keep `$$` display delimiters out.
            return segment.replace(
                /(?<!\$)\$(?!\$)((?:(?!\$).)+?)\$(?!\$)/g,
                match => {
                    const placeholder = `__MATH_INLINE_${storeId}_${counter}__`;
                    blocks.push({ placeholder, content: match });
                    counter++;
                    return placeholder;
                },
            );
        },

        restore(text: string): string {
            let out = text;
            for (const { placeholder, content } of blocks) {
                out = out.split(placeholder).join(content);
            }
            return out;
        },
    };
}