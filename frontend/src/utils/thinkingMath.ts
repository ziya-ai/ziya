/**
 * Math handling for reasoning ("thinking") blocks.
 *
 * ThinkingBlock renders its body by calling `marked.parse` directly instead of
 * going through MarkdownRenderer's preprocessing pipeline.  The pipeline step
 * that lifts `$...$` spans out for KaTeX therefore never ran on reasoning
 * content, and math showed up as literal dollar-delimited source.
 *
 * Both halves here are pure so they can be tested without mounting the
 * component or loading KaTeX: `protectThinkingMath` decides WHAT is math and
 * hides it from the markdown parser; `restoreThinkingMath` substitutes rendered
 * output back in.  The KaTeX call itself is injected by the caller, which is
 * also what lets the caller degrade to literal text when KaTeX fails to load.
 */
import { applyOutsideFences, applyOutsideCodeSpans } from '../components/fenceScanner';
import {
    processInlineMath,
    decodeInlineMathMarker,
    MATH_INLINE_MARKER_PREFIX,
    MATH_INLINE_MARKER_SPLIT_RE,
} from './inlineMathClassifier';

/**
 * Placeholder standing in for one display-math span while markdown is parsed.
 *
 * Mathematical angle brackets (U+27E8/U+27E9) are neither markdown- nor
 * HTML-significant, so the marker survives the parser as plain text — the same
 * reasoning behind the inline-math and thinking-position markers.
 */
const DISPLAY_MARKER_PREFIX = '\u27E8THINKING_DISPLAY_MATH:';
const DISPLAY_MARKER_RE = new RegExp(`${DISPLAY_MARKER_PREFIX}(\\d+)\u27E9`, 'g');

export interface ProtectedThinkingMath {
    /** Markdown source with every math span replaced by an opaque marker. */
    source: string;
    /** LaTeX of each display-math span, indexed by its marker number. */
    displayMath: string[];
}

/**
 * Replace every math span outside code with an opaque marker.
 *
 * Inline spans reuse `processInlineMath`, so reasoning content is subject to
 * exactly the same currency/prose false-positive rules as answer text rather
 * than a second, divergent notion of what counts as math.
 */
export function protectThinkingMath(source: string): ProtectedThinkingMath {
    const displayMath: string[] = [];
    const protectedSource = applyOutsideFences(source, segment =>
        applyOutsideCodeSpans(segment, scope => {
            // Display math must go first.  The inline pattern cannot open on
            // the `$$` of `$$x$$` (its content class excludes `$`), but it DOES
            // match the inner `$x$` one character later, which would consume
            // the span and leave a stray delimiter on each side.
            const withDisplay = scope.replace(
                /\$\$([\s\S]+?)\$\$/g,
                (_match, latex) => {
                    displayMath.push(latex);
                    return `${DISPLAY_MARKER_PREFIX}${displayMath.length - 1}\u27E9`;
                },
            );
            return processInlineMath(withDisplay);
        }),
    );
    return { source: protectedSource, displayMath };
}

/** Renders one LaTeX span to an HTML string. */
export type ThinkingMathRenderer = (latex: string, displayMode: boolean) => string;

/**
 * Substitute rendered HTML for every marker left by `protectThinkingMath`.
 *
 * A marker whose payload does not decode is left in place rather than dropped,
 * so corruption stays visible instead of silently deleting content.
 */
export function restoreThinkingMath(
    html: string,
    displayMath: string[],
    render: ThinkingMathRenderer,
): string {
    const withDisplay = html.replace(DISPLAY_MARKER_RE, (match, index) => {
        const latex = displayMath[Number(index)];
        return latex === undefined ? match : render(latex, true);
    });
    return withDisplay.replace(MATH_INLINE_MARKER_SPLIT_RE, marker => {
        const latex = decodeInlineMathMarker(marker);
        return latex === null ? marker : render(latex, false);
    });
}

/**
 * Whether any marker is still present, so a caller can skip loading KaTeX for
 * the common case of reasoning that contains no math at all.
 *
 * Uses a substring check rather than MATH_INLINE_MARKER_SPLIT_RE.test(): that
 * regex is global, and `test` on a global regex advances lastIndex, so
 * successive calls against similar strings alternate true/false.
 */
export function hasThinkingMathMarkers(html: string, displayMath: string[]): boolean {
    return displayMath.length > 0 || html.includes(MATH_INLINE_MARKER_PREFIX);
}
