'use strict';

/**
 * The single source of truth for LaTeX normalisation applied before KaTeX.
 *
 * Two independent code paths render the same math and must agree:
 *   - the browser (MarkdownRenderer's MathRenderer, and the HTML-string path
 *     used by reasoning/"thinking" blocks), which imports this module;
 *   - the HTML/paste exporter (app/utils/conversation_exporter.py), which has
 *     no browser and shells out to Node, requiring this file directly.
 *
 * Any correction added on one side only produces an export that disagrees with
 * what the user saw on screen, which is the exact defect class this module
 * exists to eliminate.  Written as dependency-free CommonJS so `node -e` can
 * require it without a build step (`.js` in src is established precedent --
 * see utils/d3Plugins/vexflowStub.js -- and eslint-config-react-app sets
 * `commonjs: true, node: true`, so module/require are in scope).
 */

/**
 * KaTeX paints an unresolvable token in this colour under
 * `throwOnError: false`.  Exported so the render options, and the screenshot
 * harness that greps for the failure, share one value instead of three
 * hardcoded copies.
 *
 * This is the LIGHT-theme value.  #cc0000 clears the 4.5:1 text floor on the
 * white chat surface (5.89:1 on #ffffff) but only ~2.8:1 on the app's dark
 * chat surfaces (#1f1f1f / #141414) — below even the 3:1 graphical floor — so
 * a failed math token is effectively invisible in dark mode.  There is NO
 * single red that clears 4.5:1 on BOTH a white and a near-black background
 * (they demand opposite lightness), so the error colour must be RESOLVED from
 * the active theme, not swapped for another constant.  See katexRenderOptions.
 */
const KATEX_ERROR_COLOR = '#cc0000';

/**
 * The DARK-theme KaTeX error colour.
 *
 * #ff6b6b is a light, unmistakably-red error tint that clears the 4.5:1 text
 * floor on both dark chat surfaces (5.94:1 on #1f1f1f, 6.64:1 on #141414) while
 * staying below 4.5:1 on white (2.78:1) — the two backgrounds have opposite
 * requirements, which is exactly why the value is theme-resolved rather than a
 * single constant shared by both paths.
 */
const KATEX_ERROR_COLOR_DARK = '#ff6b6b';

/**
 * amsmath environments the bundled KaTeX cannot parse, mapped to their closest
 * supported equivalent.  multline's distinguishing feature -- last line
 * flush-right -- is not expressible in KaTeX; `gathered` centres every line,
 * which is strictly better than a red error.
 */
const UNSUPPORTED_MATH_ENV_ALIASES = {
    'multline': 'gathered',
    'multline*': 'gathered',
    'multlined': 'gathered',
};

/**
 * Alias unsupported-but-standard amsmath environments to a supported form.
 *
 * Keys off the environment NAME -- a fixed capability gap in the bundled KaTeX
 * -- never off prose, and returns natively-supported environments
 * (gather/aligned/split/cases/array/matrix...) byte-unchanged.  The
 * multline-only hints \shoveright{X}/\shoveleft{X} would still error under
 * `gathered`, so the command token is dropped while its braced argument
 * survives as a plain group.
 *
 * The lookup is guarded by hasOwnProperty: a bare `ALIASES[name]` inherits
 * from Object.prototype, so `\begin{constructor}` resolved to the Object
 * constructor and rewrote itself to `\begin{function Object() { [native code]
 * }}`.  Every non-alias environment must be returned untouched, including one
 * that happens to share a name with a prototype member.
 */
function normalizeUnsupportedMathEnvironments(math) {
    const out = String(math).replace(
        /\\(begin|end)\{([^}]+)\}/g,
        function (whole, beginEnd, envName) {
            if (!Object.prototype.hasOwnProperty.call(UNSUPPORTED_MATH_ENV_ALIASES, envName)) {
                return whole;
            }
            return '\\' + beginEnd + '{' + UNSUPPORTED_MATH_ENV_ALIASES[envName] + '}';
        }
    );
    return out.replace(/\\shove(?:right|left)\s*(?=\{)/g, '');
}

/** Text-mode commands whose argument is prose, where `_` is a literal. */
const TEXT_MODE_COMMANDS =
    'text|texttt|textbf|textit|textrm|textsf|textmd|mathrm|operatorname';

/**
 * Escape bare underscores inside text-mode commands.
 *
 * KaTeX treats `_` as the subscript operator even inside \text{}, so an
 * identifier like \text{ct_id_field} is a parse error.  An already-escaped
 * `\_` is left alone so the transform is idempotent.
 */
function escapeUnderscoresInTextCommands(math) {
    return String(math).replace(
        new RegExp('\\\\(' + TEXT_MODE_COMMANDS + ')\\{([^}]*)\\}', 'g'),
        function (_match, cmd, content) {
            return '\\' + cmd + '{' + content.replace(/(?<!\\)_/g, '\\_') + '}';
        }
    );
}

/**
 * Repair a literal semicolon used as visual spacing before a command.
 *
 * Models write '; \command' where LaTeX wants the '\;' thick space.  Only a
 * bare semicolon immediately preceding a backslash command is rewritten.
 */
function normalizeSemicolonSpacing(math) {
    return String(math).replace(/(?<!\\);\s*(\\[a-zA-Z])/g, function (_m, cmd) {
        return '\\; ' + cmd;
    });
}

/**
 * Undo markdown escaping that leaked into a math span.
 *
 * Inside $...$ / $$...$$ the markdown emphasis rules no longer apply, so the
 * `\*` a model writes out of habit when it means a literal asterisk is not an
 * escape -- it is an undefined control sequence.  KaTeX 0.16.x recovers from
 * that PER TOKEN rather than failing the expression: under
 * `throwOnError: false` it emits NO katex-error span, it paints just the
 * offending token in KATEX_ERROR_COLOR.  So `x^\*` typesets correctly except
 * for a red `\*` glyph where the star belonged.
 *
 * ONLY `\*` is corrected.  Every other backslash-punctuation pair markdown
 * escapes -- `\_`, `\#`, `\{`, `\}`, `\&`, `\%`, `\$`, `\\` -- is a legitimate
 * LaTeX escape that must survive byte-unchanged.  A `\*` whose backslash is
 * itself preceded by a backslash is skipped so the `\\*` line break (a `\\`
 * followed by `*`), which KaTeX renders cleanly, is not corrupted.
 */
function normalizeMarkdownEscapesInMath(math) {
    return String(math).replace(/(?<!\\)\\\*/g, '*');
}

/**
 * The complete normalisation applied to LaTeX before handing it to KaTeX.
 *
 * Order matters: the text-mode escape runs first so a `_` inside \text{} is
 * protected before any later pass can see it, and the environment alias runs
 * last so it operates on final environment names.
 */
function sanitizeMathForKatex(math) {
    let sanitized = escapeUnderscoresInTextCommands(math);
    sanitized = normalizeSemicolonSpacing(sanitized);
    sanitized = normalizeMarkdownEscapesInMath(sanitized);
    return normalizeUnsupportedMathEnvironments(sanitized);
}

/**
 * Build the KaTeX render options for a given theme.
 *
 * `output` is deliberately absent: the browser wants the default
 * htmlAndMathml, while the exporter forces "mathml" so the standalone
 * document carries no external font or stylesheet dependency.  That is a
 * genuine per-path difference; everything governing how LaTeX is INTERPRETED
 * lives here so the two cannot drift.
 *
 * `errorColor` is the ONE option that must vary by theme: the colour of a
 * failed math token has to satisfy contrast against whichever surface it is
 * painted on, and no single red clears 4.5:1 on both a white and a near-black
 * background.  Pass `isDark` (default false → the light export/default path)
 * to resolve it; every other option is theme-independent.
 */
function katexRenderOptions(isDark) {
    return {
        throwOnError: false,
        strict: false,
        errorColor: isDark ? KATEX_ERROR_COLOR_DARK : KATEX_ERROR_COLOR,
        macros: {
            '\\f': '#1f(#2)',
        },
    };
}

/**
 * The light-theme render options, kept as a named export for the exporter and
 * every existing caller that renders on a light surface.  Derived from the
 * factory so the two cannot drift.
 */
const KATEX_RENDER_OPTIONS = katexRenderOptions(false);

module.exports = {
    KATEX_ERROR_COLOR,
    KATEX_ERROR_COLOR_DARK,
    KATEX_RENDER_OPTIONS,
    katexRenderOptions,
    UNSUPPORTED_MATH_ENV_ALIASES,
    escapeUnderscoresInTextCommands,
    normalizeSemicolonSpacing,
    normalizeMarkdownEscapesInMath,
    normalizeUnsupportedMathEnvironments,
    sanitizeMathForKatex,
};
