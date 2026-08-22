/**
 * mermaidThemeNormalize — force baked dark mermaid themes to light.
 *
 * SHARED export-fidelity infrastructure (Card I PDF + Card II HTML).
 *
 * WHY.  A mermaid diagram can carry an inline directive
 *   %%{init: {'theme':'dark'}}%%
 * at the top of its definition.  That per-diagram `init` directive OVERRIDES
 * whatever `mermaid.initialize({ theme: ... })` requested (see
 * `plugins/d3/mermaidPlugin.ts`), so even though the /print route forces the
 * app into light mode and calls `initialize({ theme: 'default' })`, a spec with
 * a baked `theme:'dark'` renders DARK — the diagram's darkness is intrinsic to
 * its own `<style>`, baked at render time, and survives the forced-light page.
 * `styles/mermaid-theme.css` only recolours under a `.dark` ancestor that the
 * /print route deliberately strips, so it cannot repaint a theme-baked SVG
 * either.  The result is user defect #6: dark rectangles/bands where diagrams
 * are, sitting on the otherwise-white printed page.
 *
 * The correct, format-neutral fix is to normalize the SPEC before it reaches
 * the renderer, in the SHARED /print render path, so BOTH the PDF capture and
 * Card II's `extract_html()` inherit light diagrams.  This is a source-level
 * transform (not a CSS overlay or a PDF-only post-process), so it works no
 * matter how the rendered DOM is later consumed.
 *
 * Scope: this only rewrites the THEME selection inside `%%{init ...}%%`
 * directives (and drops any baked dark `themeVariables`).  It does not touch
 * diagram structure, labels, layout directives (flowchart/sequence/gantt
 * config), or non-mermaid content, and it is idempotent — a light spec, or a
 * spec already normalized, passes through unchanged.
 */

// Dark-ish mermaid built-in themes we normalize to the light 'default'.
// (Mermaid's light themes are 'default', 'base', 'neutral', 'forest'; of these
// 'forest'/'neutral'/'base' can still be darker than the plain light page, and
// 'dark' is the explicit dark theme.  For a white printed page we want the
// plain light 'default' regardless.)
const DARK_THEME_NAMES = 'dark|forest|neutral|base';

// Matches theme: 'dark'  /  theme:"dark"  (single or double quoted) inside a
// directive body.  Captures the `theme:` lead + the quote char so we can
// re-emit `theme:'default'` preserving spacing.
const THEME_RE = new RegExp(
    "(['\"]?theme['\"]?\\s*:\\s*)(['\"])(?:" + DARK_THEME_NAMES + ")\\2",
    'gi',
);

// Matches a `themeVariables: { ... }` object (one level of nesting) inside a
// directive body, including a leading comma if present, so we can drop a baked
// dark palette wholesale.  Mermaid's own init themeVariables are flat, so a
// single non-nested `{ ... }` match is sufficient for the directive case.
const THEME_VARS_RE = /,?\s*['"]?themeVariables['"]?\s*:\s*\{[^{}]*\}/gi;

// Matches a whole `%%{ ... }%%` mermaid init directive (non-greedy).
const INIT_DIRECTIVE_RE = /%%\{[\s\S]*?\}%%/g;

/**
 * Rewrite any dark mermaid `%%{init ...}%%` theme to the light 'default' theme
 * and strip baked dark `themeVariables`.  Returns the content unchanged when it
 * contains no directive to normalize.
 *
 * Operates on arbitrary message content (markdown), so it safely no-ops on text
 * with no mermaid directives.  It rewrites directives wherever they appear —
 * inside a ```mermaid fence or a bare diagram string alike.
 */
export function normalizeMermaidThemeToLight(content: string): string {
    if (!content || content.indexOf('%%{') === -1) return content;
    return content.replace(INIT_DIRECTIVE_RE, (directive) => {
        // Only touch `init` directives; leave e.g. %%{wrap}%% alone.
        if (!/init/i.test(directive)) return directive;
        let d = directive;
        d = d.replace(THEME_RE, "$1'default'");
        d = d.replace(THEME_VARS_RE, '');
        return d;
    });
}

export default normalizeMermaidThemeToLight;
