/**
 * Normalise the LaTeX bracket/paren math delimiter dialects to the dollar
 * dialects the chat markdown pipeline understands (D-017 w3-07).
 *
 * KaTeX's auto-render (and every other renderer that follows the TeX
 * convention) treats `\[ ... \]` as DISPLAY math and `\( ... \)` as INLINE
 * math.  The chat renderer's math preprocessor, however, only ever recognised
 * `$$...$$`, `$...$`, and ```` ```math ````/```` ```latex ```` fences — so a
 * model that emitted `\(a^2+b^2\)` or `\[E=mc^2\]` had its LaTeX source leak
 * through verbatim instead of being typeset.
 *
 * This transform rewrites those two delimiter pairs to the equivalent dollar
 * forms so the existing extraction/KaTeX passes pick them up unchanged:
 *   \[ X \]  ->  $$ X $$   (display, block-separated so it lexes as a block)
 *   \( X \)  ->  $ X $     (inline)
 *
 * It is delimiter-only and content-agnostic: the captured body is emitted
 * byte-for-byte, so a `_`, `&`, `\\` etc. inside the span reaches the same
 * sanitizer it would have via the dollar dialects.
 *
 * A delimiter whose backslash is itself escaped (`\\[`, `\\(`) is left alone:
 * `\\` is a TeX line break, so `\\[2ex]` (a break with spacing) or a `\\`
 * ending a line immediately before a literal `(` must not be misread as an
 * opener.  The closing delimiter is guarded the same way.
 *
 * The caller is responsible for applying this OUTSIDE fenced code blocks and
 * inline code spans (a literal "\[" written in code must survive), and BEFORE
 * the markdown link/bracket ReDoS guards, so no stray "[" remains for them to
 * escape.  Keeping the fence-awareness in the caller (which already owns the
 * CommonMark-aware scanner) keeps this helper pure and unit-testable.
 */

const DISPLAY_DIALECT = /(?<!\\)\\\[([\s\S]+?)(?<!\\)\\\]/g;
const INLINE_DIALECT = /(?<!\\)\\\(([\s\S]+?)(?<!\\)\\\)/g;

export function convertLatexDelimiterDialects(segment: string): string {
    if (typeof segment !== 'string' || segment.indexOf('\\') === -1) {
        return segment;
    }
    return segment
        // Display first: \[ ... \] -> $$ ... $$  (blank lines so marked lexes
        // it as its own block, matching how a standalone $$...$$ is handled).
        .replace(DISPLAY_DIALECT, (_m, inner) => `\n\n$$${inner}$$\n\n`)
        // Then inline: \( ... \) -> $ ... $
        .replace(INLINE_DIALECT, (_m, inner) => `$${inner}$`);
}
