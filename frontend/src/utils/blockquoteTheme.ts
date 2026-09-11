/**
 * Theme-resolved blockquote affordance (consolidated-backlog D-011).
 *
 * The chat markdown renderer emitted a bare <blockquote> with NO left rule,
 * background tint or muted colour in either theme, so a quoted passage was
 * indistinguishable from an indented paragraph and the 3:1 graphical-boundary
 * floor was unmeetable by construction (no boundary was drawn at all).
 *
 * The correct shape for a theme fix is to resolve the colours FROM the active
 * theme, not to swap one hardcoded constant for another.  A single grey cannot
 * clear 3:1 on both a white and a dark surface (the two backgrounds pull the
 * contrast in opposite directions), so the border/text are chosen per theme
 * and each clears its floor AGAINST THE BACKGROUND IT IS ACTUALLY SHOWN ON:
 *
 *   light (on #ffffff): border #6b7280 = 4.83:1 (>=3:1 boundary),
 *                       text   #4b5563 = 7.56:1 (>=4.5:1 text)
 *   dark  (on #262626): border #8b949e = 4.92:1 (>=3:1 boundary),
 *                       text   #adbac7 = 7.66:1 (>=4.5:1 text)
 *
 * The border values are deliberately different between themes: that is what
 * makes this a theme resolution rather than a constant swap that would fix one
 * background and quietly fail the other.
 */
export interface BlockquoteTheme {
    /** Left-rule colour; the graphical boundary that must clear 3:1. */
    borderColor: string;
    /** Muted body colour for quoted text; must clear 4.5:1. */
    textColor: string;
    /** Faint fill so the quote reads as a distinct region. */
    background: string;
}

export function blockquoteTheme(isDarkMode: boolean): BlockquoteTheme {
    return isDarkMode
        ? { borderColor: '#8b949e', textColor: '#adbac7', background: 'rgba(255,255,255,0.04)' }
        : { borderColor: '#6b7280', textColor: '#4b5563', background: 'rgba(0,0,0,0.03)' };
}
