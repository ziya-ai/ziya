/**
 * Types for the shared math sanitizer.
 *
 * mathSanitizer.js is plain CommonJS because the Python exporter's `node -e`
 * subprocess requires it directly, with no build step available.  This
 * declaration gives the TypeScript callers real types anyway; TS prefers a
 * sibling .d.ts over inference from the .js.
 */
export declare const KATEX_ERROR_COLOR: string;
export declare const KATEX_ERROR_COLOR_DARK: string;
export declare function katexRenderOptions(isDark?: boolean): {
    throwOnError: boolean;
    strict: boolean;
    errorColor: string;
    macros: Record<string, string>;
};
export declare const KATEX_RENDER_OPTIONS: {
    throwOnError: boolean;
    strict: boolean;
    errorColor: string;
    macros: Record<string, string>;
};
export declare const UNSUPPORTED_MATH_ENV_ALIASES: Record<string, string>;
export declare function escapeUnderscoresInTextCommands(math: string): string;
export declare function normalizeSemicolonSpacing(math: string): string;
export declare function normalizeMarkdownEscapesInMath(math: string): string;
export declare function normalizeUnsupportedMathEnvironments(math: string): string;
export declare function sanitizeMathForKatex(math: string): string;
