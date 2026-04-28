/**
 * Convert ANSI SGR (Select Graphic Rendition) escape sequences to HTML spans.
 *
 * Shell commands frequently emit ANSI color codes for test results, linting
 * output, etc.  This utility maps the standard 8/16/256/RGB color codes to
 * CSS so the web UI renders them faithfully instead of showing raw escape
 * literals like `[91mFAIL[0m`.
 *
 * Only SGR sequences (ESC [ <params> m) are handled.  All other escape
 * sequences (cursor movement, etc.) are stripped.
 */

 export type AnsiTheme = 'dark' | 'light';

 // Dark-mode palette: bright, saturated colors readable on a dark background.
 const ANSI_FG_COLORS_DARK: Record<number, string> = {
    30: '#4d4d4d', 31: '#cc0000', 32: '#00cc00', 33: '#cccc00',
    34: '#5c5cff', 35: '#cc00cc', 36: '#00cccc', 37: '#cccccc',
    90: '#666666', 91: '#ff3333', 92: '#33ff33', 93: '#ffff33',
    94: '#3333ff', 95: '#ff33ff', 96: '#33ffff', 97: '#ffffff',
};

 const ANSI_BG_COLORS_DARK: Record<number, string> = {
    40: '#000000', 41: '#cc0000', 42: '#00cc00', 43: '#cccc00',
    44: '#0000cc', 45: '#cc00cc', 46: '#00cccc', 47: '#cccccc',
    100: '#666666', 101: '#ff3333', 102: '#33ff33', 103: '#ffff33',
    104: '#3333ff', 105: '#ff33ff', 106: '#33ffff', 107: '#ffffff',
};

 // Light-mode palette: darker, more saturated values so text stays legible
 // on a white background. Bright variants are shifted toward their normal
 // counterparts rather than being lightened further.
 const ANSI_FG_COLORS_LIGHT: Record<number, string> = {
     30: '#000000', 31: '#c91b00', 32: '#00a600', 33: '#a67f00',
     34: '#0225c7', 35: '#a700a7', 36: '#00939e', 37: '#5c5c5c',
     90: '#4d4d4d', 91: '#e50000', 92: '#00bc00', 93: '#b58900',
     94: '#1a4bd6', 95: '#c700c7', 96: '#009ca8', 97: '#737373',
 };

 const ANSI_BG_COLORS_LIGHT: Record<number, string> = {
     40: '#000000', 41: '#c91b00', 42: '#00a600', 43: '#a67f00',
     44: '#0225c7', 45: '#a700a7', 46: '#00939e', 47: '#cccccc',
     100: '#4d4d4d', 101: '#e50000', 102: '#00bc00', 103: '#b58900',
     104: '#1a4bd6', 105: '#c700c7', 106: '#009ca8', 107: '#d9d9d9',
 };

 function fgPalette(theme: AnsiTheme): Record<number, string> {
     return theme === 'light' ? ANSI_FG_COLORS_LIGHT : ANSI_FG_COLORS_DARK;
 }

 function bgPalette(theme: AnsiTheme): Record<number, string> {
     return theme === 'light' ? ANSI_BG_COLORS_LIGHT : ANSI_BG_COLORS_DARK;
 }

// 256-color palette helper
 function color256ToHex(n: number, theme: AnsiTheme = 'dark'): string | null {
    if (n < 0 || n > 255) return null;
     const fg = fgPalette(theme);
     if (n < 8) return fg[30 + n] ?? null;
     if (n < 16) return fg[82 + n] ?? null;
    if (n < 232) {
        const idx = n - 16;
        const b = idx % 6, g = Math.floor(idx / 6) % 6, r = Math.floor(idx / 36);
        const toHex = (v: number) => (v === 0 ? 0 : 55 + v * 40).toString(16).padStart(2, '0');
        return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
    }
    const level = 8 + (n - 232) * 10;
    const hex = level.toString(16).padStart(2, '0');
    return `#${hex}${hex}${hex}`;
}

interface AnsiState {
    fg: string | null;  bg: string | null;
    bold: boolean;      dim: boolean;       italic: boolean;
    underline: boolean; strikethrough: boolean;
}

function emptyState(): AnsiState {
    return { fg: null, bg: null, bold: false, dim: false, italic: false, underline: false, strikethrough: false };
}

function stateToStyle(s: AnsiState): string {
    const parts: string[] = [];
    if (s.fg) parts.push(`color:${s.fg}`);
    if (s.bg) parts.push(`background-color:${s.bg}`);
    if (s.bold) parts.push('font-weight:bold');
    if (s.dim) parts.push('opacity:0.6');
    if (s.italic) parts.push('font-style:italic');
    if (s.underline) parts.push('text-decoration:underline');
    if (s.strikethrough) parts.push('text-decoration:line-through');
    return parts.join(';');
}

function hasStyle(s: AnsiState): boolean {
    return !!(s.fg || s.bg || s.bold || s.dim || s.italic || s.underline || s.strikethrough);
}

/** Quick check — use to skip conversion for strings without ANSI. */
export function containsAnsi(text: string): boolean {
    return text.includes('\x1b[');
}

/** HTML-escape text for safe insertion into innerHTML. */
function escapeHtml(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Convert ANSI escape sequences in `text` to HTML with inline styles.
 *
 * Returns HTML-entity-escaped text.  When no ANSI codes are present the
 * string is simply entity-escaped (no wrapping spans).
 */
 export function ansiToHtml(text: string, theme: AnsiTheme = 'dark'): string {
    if (!containsAnsi(text)) return escapeHtml(text);

    const CSI_RE = /\x1b\[([0-9;]*)([a-zA-Z])/g;
    const state = emptyState();
    let result = '';
    let spanOpen = false;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = CSI_RE.exec(text)) !== null) {
        const before = text.slice(lastIndex, match.index);
        if (before) result += escapeHtml(before);
        lastIndex = match.index + match[0].length;

        if (match[2] !== 'm') continue; // strip non-SGR sequences

        const params = match[1] === '' ? [0] : match[1].split(';').map(Number);
        let i = 0;
        while (i < params.length) {
            const c = params[i];
            if (c === 0) Object.assign(state, emptyState());
            else if (c === 1) state.bold = true;
            else if (c === 2) state.dim = true;
            else if (c === 3) state.italic = true;
            else if (c === 4) state.underline = true;
            else if (c === 9) state.strikethrough = true;
            else if (c === 22) { state.bold = false; state.dim = false; }
            else if (c === 23) state.italic = false;
            else if (c === 24) state.underline = false;
            else if (c === 29) state.strikethrough = false;
            else if (c >= 30 && c <= 37) state.fg = fgPalette(theme)[c] ?? null;
            else if (c === 38) {
                if (params[i + 1] === 5 && i + 2 < params.length) { state.fg = color256ToHex(params[i + 2], theme); i += 2; }
                else if (params[i + 1] === 2 && i + 4 < params.length) {
                    state.fg = `#${params[i+2].toString(16).padStart(2,'0')}${params[i+3].toString(16).padStart(2,'0')}${params[i+4].toString(16).padStart(2,'0')}`;
                    i += 4;
                }
            }
            else if (c === 39) state.fg = null;
            else if (c >= 40 && c <= 47) state.bg = bgPalette(theme)[c] ?? null;
            else if (c === 48) {
                if (params[i + 1] === 5 && i + 2 < params.length) { state.bg = color256ToHex(params[i + 2], theme); i += 2; }
                else if (params[i + 1] === 2 && i + 4 < params.length) {
                    state.bg = `#${params[i+2].toString(16).padStart(2,'0')}${params[i+3].toString(16).padStart(2,'0')}${params[i+4].toString(16).padStart(2,'0')}`;
                    i += 4;
                }
            }
            else if (c === 49) state.bg = null;
            else if (c >= 90 && c <= 97) state.fg = fgPalette(theme)[c] ?? null;
            else if (c >= 100 && c <= 107) state.bg = bgPalette(theme)[c] ?? null;
            i++;
        }

        if (spanOpen) { result += '</span>'; spanOpen = false; }
        if (hasStyle(state)) { result += `<span style="${stateToStyle(state)}">`; spanOpen = true; }
    }

    if (lastIndex < text.length) result += escapeHtml(text.slice(lastIndex));
    if (spanOpen) result += '</span>';
    return result;
}

/**
 * Strip ANSI escape sequences, returning plain text.
 * Useful when ANSI rendering is not desired (e.g. PDF export).
 */
export function stripAnsi(text: string): string {
    if (!containsAnsi(text)) return text;
    return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
}
