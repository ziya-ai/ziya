/**
 * Shared CommonMark-aware fenced-code-block scanner.
 *
 * MarkdownRenderer's preprocessing pipeline historically had five
 * independent fence-detection passes, each re-deriving "am I inside a
 * fence?" with its own regex. None modeled two CommonMark rules that
 * matter in practice on streamed model output:
 *
 *   1. A backtick fence's info string may NOT contain a backtick.
 *      Otherwise the tail of a wrapped inline-code span (a line that
 *      begins "
 *      sat on the previous line) is misread as a fence opener.
 *
 *   2. A fence opened with backticks is closed only by backticks; a
 *      fence opened with tildes is closed only by tildes. The two are
 *      not interchangeable — a run of backticks inside a ~~~ block is
 *      ordinary content, not a close.
 *
 * Divergence between the five passes (and between them and the marked
 * lexer they feed) was the root cause of a class of "diff renders as
 * raw text mid-block, every subsequent fence inverted" bugs.
 *
 * This module is the single source of truth for fence state. Passes
 * consult classifyFenceLines() instead of matching their own regexes.
 *
 * Scope / known limitation: a fully inline code span that opens AND
 * closes on the same line while beginning at column 0 with >=3
 * backticks is not modeled. Rule 1 covers the phantom-opener shape that
 * actually occurs in streamed output; the column-0 inline-span case is
 * vanishingly rare and intentionally out of scope here.
 */

export type FenceChar = '`' | '~';

/** A line that opens a fenced code block. */
export interface FenceOpen {
    kind: 'open';
    /** Delimiter character of the fence. */
    char: FenceChar;
    /** Number of delimiter characters in the opening run (>= 3). */
    len: number;
    /** Trimmed info string (language tag etc.); '' when bare. */
    info: string;
    /** Leading-space indent of the opener (0-3). */
    indent: number;
}

/** A line that closes the currently-open fenced code block. */
export interface FenceClose {
    kind: 'close';
    char: FenceChar;
    len: number;
}

/** A line inside an open fenced code block (verbatim content). */
export interface FenceContent {
    kind: 'content';
    /** Delimiter of the enclosing fence. */
    char: FenceChar;
    /** Opening-run length of the enclosing fence. */
    len: number;
}

/** A line outside any fenced code block. */
export interface FenceText {
    kind: 'text';
}

export type LineClass = FenceOpen | FenceClose | FenceContent | FenceText;

const OPEN_RE = /^( {0,3})(`{3,}|~{3,})(.*)$/;
const CLOSE_BACKTICK_RE = /^ {0,3}(`{3,})[ \t]*$/;
const CLOSE_TILDE_RE = /^ {0,3}(~{3,})[ \t]*$/;

/**
 * Test whether a line is a valid fence opener. Returns the opener
 * descriptor or null. Applies CommonMark rule 1 (a backtick fence's
 * info string may not contain a backtick), which is what distinguishes
 * a real opener from the tail of a wrapped inline-code span.
 */
export function matchFenceOpen(
    line: string,
): { char: FenceChar; len: number; info: string; indent: number } | null {
    const m = OPEN_RE.exec(line);
    if (!m) return null;
    const indent = m[1].length;
    const run = m[2];
    const char = run[0] as FenceChar;
    const len = run.length;
    const info = m[3];
    if (char === '`' && info.includes('`')) return null;
    return { char, len, info: info.trim(), indent };
}

/**
 * Test whether a line closes the given active fence. A close must use
 * the SAME delimiter character, a run length >= the opener, <=3 spaces
 * indent, and nothing but trailing whitespace after the run.
 */
export function matchFenceClose(
    line: string,
    active: { char: FenceChar; len: number },
): { len: number } | null {
    const re = active.char === '`' ? CLOSE_BACKTICK_RE : CLOSE_TILDE_RE;
    const m = re.exec(line);
    if (!m) return null;
    if (m[1].length < active.len) return null;
    return { len: m[1].length };
}

/**
 * Classify every line of `markdown` as open / close / content / text
 * with respect to fenced code blocks, modeling CommonMark fence rules.
 *
 * The returned array has exactly one entry per input line (split on
 * '\n'), so callers can index it positionally against their own
 * line-split of the same string.
 *
 * An unterminated fence (streaming case) leaves the tail classified as
 * 'content' — the opener is real, the close simply hasn't streamed yet.
 */
export function classifyFenceLines(markdown: string): LineClass[] {
    const lines = markdown.split('\n');
    const out: LineClass[] = [];
    let active: { char: FenceChar; len: number } | null = null;

    for (const line of lines) {
        if (active === null) {
            const open = matchFenceOpen(line);
            if (open) {
                active = { char: open.char, len: open.len };
                out.push({
                    kind: 'open',
                    char: open.char,
                    len: open.len,
                    info: open.info,
                    indent: open.indent,
                });
            } else {
                out.push({ kind: 'text' });
            }
        } else {
            const close = matchFenceClose(line, active);
            if (close) {
                out.push({ kind: 'close', char: active.char, len: close.len });
                active = null;
            } else {
                out.push({ kind: 'content', char: active.char, len: active.len });
            }
        }
    }
    return out;
}

/**
 * Convenience predicate: is the line at `index` inside a fenced code
 * block (classified as 'content' or 'close')? Useful for passes that
 * only need "should I leave this line alone?" semantics.
 */
export function isInsideFence(classes: LineClass[], index: number): boolean {
    const c = classes[index];
    return c !== undefined && (c.kind === 'content' || c.kind === 'close');
}

/**
 * Escape leading backtick-runs (>=3) on lines that are verbatim CONTENT
 * inside a *backtick* fenced block, so a downstream lexer (marked) cannot
 * misread them as a premature closing fence. Lines inside a tilde fence
 * are left untouched (a backtick run there is ordinary content), and
 * open/close/text lines are never escaped.
 *
 * Replaces a former private line-walking scanner in MarkdownRenderer that
 * re-derived fence state with its own regex and diverged from the
 * CommonMark rules in classifyFenceLines.
 */
export function escapeNestedBacktickFences(markdown: string): string {
    const lines = markdown.split('\n');
    const classes = classifyFenceLines(markdown);
    return lines
        .map((line, i) => {
            const c = classes[i];
            if (c && c.kind === 'content' && c.char === '`') {
                return line.replace(/^(`{3,})/, (m) => '&#96;'.repeat(m.length));
            }
            return line;
        })
        .join('\n');
}

/**
 * Strip bare code fences that wrap markdown prose instead of code.
 *
 * Models sometimes emit bare fences as visual section separators, or
 * a hallucinated wide outer fence around a real tagged inner block.
 * The marked tokenizer pairs consecutive bare fences as open/close,
 * so prose between them renders as a code block. This walks the lines,
 * detects fence pairs whose content looks like prose (or whose inner
 * content is itself a real fenced block), and strips the spurious
 * fences.
 *
 * Lang-tagged fence openers are detected via matchFenceOpen (the
 * shared CommonMark rule), so the tail of a wrapped inline-code span
 * is not mistaken for a real language fence opener.
 */
export function stripBareProseFences(markdown: string): string {
    const fenceLines: string[] = markdown.split('\n');
    const fenceOutput: string[] = [];
    let fi: number = 0;
    let insideLangFence: boolean = false;
    let langFenceLen: number = 0;

    while (fi < fenceLines.length) {
        const fLine: string = fenceLines[fi];
        const bareFenceMatch: RegExpMatchArray | null = fLine.match(/^([`]{3,})\s*$/);

        if (!insideLangFence) {
            const opener = matchFenceOpen(fLine);
            const isOpener = opener && opener.char === '`' && opener.indent === 0 && opener.info !== '';
            if (isOpener) {
                insideLangFence = true;
                langFenceLen = opener!.len;
                fenceOutput.push(fLine);
                fi += 1;
                continue;
            }
        }

        const closesLang = bareFenceMatch && insideLangFence && bareFenceMatch[1].length >= langFenceLen;
        if (closesLang) {
            insideLangFence = false;
            langFenceLen = 0;
            fenceOutput.push(fLine);
            fi += 1;
            continue;
        }

        if (bareFenceMatch && !insideLangFence) {
            const fLen: number = bareFenceMatch[1].length;
            let closeIdx: number = -1;
            for (let fj: number = fi + 1; fj < fenceLines.length; fj += 1) {
                const closeMatch = fenceLines[fj].match(/^([`]{3,})\s*$/);
                if (closeMatch && closeMatch[1].length >= fLen) {
                    closeIdx = fj;
                    break;
                }
            }

            if (closeIdx !== -1) {
                const innerLines: string[] = fenceLines.slice(fi + 1, closeIdx);
                const innerContent: string = innerLines.join('\n').trim();

                if (!innerContent) {
                    fi = closeIdx + 1;
                    continue;
                }

                const firstNonBlank: number = innerLines.findIndex((l) => l.trim().length > 0);
                const innerStartsWithFence = firstNonBlank >= 0 && /^[`]{3,}\S/.test(innerLines[firstNonBlank]);
                if (innerStartsWithFence) {
                    fenceOutput.push(...innerLines);
                    fi = closeIdx + 1;
                    continue;
                }

                if (fLen >= 4) {
                    const innerTaggedFence: boolean = innerLines.some((l) => {
                        const m = l.match(/^([`]{3,})[A-Za-z]/);
                        return m !== null && m[1].length < fLen;
                    });
                    if (innerTaggedFence) {
                        fenceOutput.push(...innerLines);
                        fi = closeIdx + 1;
                        continue;
                    }
                }

                const proseRe1 = /\*\*|^#{1,6}\s|^\d+\.|^[-*]\s|^>\s/m;
                const proseRe2 = /\[[^\]]+\]\([^)]+\)/;
                const proseRe3 = /<\/?(?:strong|em|b|i|a|p|br|code|span)\b[^>]*>/i;
                const proseRe4 = /^(?:Title|URL|Description|Source|Link):\s/m;
                const looksLikeMarkdown: boolean = proseRe1.test(innerContent) || proseRe2.test(innerContent) || proseRe3.test(innerContent) || proseRe4.test(innerContent);

                const codeStarters: string[] = ['import ', 'from ', 'def ', 'class ', 'function ', 'const ', 'let ', 'var ', 'return ', 'if (', 'for ', 'while '];
                const diffStarters: string[] = ['diff --git', '--- a/', '+++ b/'];
                const codeRe1 = /^[a-z_]+\s*[=(]/;
                const codeRe2 = /^\s*[{}]\s*$/;
                const looksLikeCode: boolean = innerContent.split('\n').some((l) => {
                    const t: string = l.trimStart();
                    const startsWithCode: boolean = codeStarters.some((p) => t.startsWith(p));
                    const startsWithDiff: boolean = diffStarters.some((p) => t.startsWith(p));
                    return startsWithCode || startsWithDiff || codeRe1.test(t) || codeRe2.test(t);
                });

                if (looksLikeMarkdown && !looksLikeCode) {
                    fenceOutput.push(...innerLines);
                    fi = closeIdx + 1;
                    continue;
                }

                fenceOutput.push(fLine);
                fenceOutput.push(...innerLines);
                fenceOutput.push(fenceLines[closeIdx]);
                fi = closeIdx + 1;
                continue;
            } else {
                const remainingContent: string = fenceLines.slice(fi + 1).join('\n').trim();
                const remainingMdRe = /\*\*|^#{1,6}\s|^\d+\.|^[-*]\s/m;
                const remainingIsMarkdown: boolean = remainingMdRe.test(remainingContent);
                const codeStarters2: string[] = ['import ', 'def ', 'function ', 'const '];
                const remainingIsCode: boolean = fenceLines.slice(fi + 1).some((l) => {
                    const t: string = l.trimStart();
                    return codeStarters2.some((p) => t.startsWith(p));
                });

                if (remainingIsMarkdown && !remainingIsCode) {
                    fi += 1;
                    continue;
                }
            }
        }

        fenceOutput.push(fLine);
        fi += 1;
    }

    return fenceOutput.join('\n');
}

/**
 * Run a text transform over markdown but only on regions outside fenced
 * code blocks. Verbatim fence content and fence-close lines are left
 * untouched, so prose-preprocessing passes cannot corrupt a diff or code
 * sample that lives inside a fenced block.
 */
export function applyOutsideFences(
    markdown: string,
    transform: (segment: string) => string,
): string {
    const lines = markdown.split('\n');
    const classes = classifyFenceLines(markdown);
    const result: string[] = [];
    let buffer: string[] = [];
    const flush = (): void => {
        if (buffer.length === 0) {
            return;
        }
        const transformed = transform(buffer.join('\n')).split('\n');
        for (let j = 0; j < transformed.length; j += 1) {
            result.push(transformed[j]);
        }
        buffer = [];
    };
    for (let i = 0; i < lines.length; i += 1) {
        const c = classes[i];
        if (c !== undefined && (c.kind === 'content' || c.kind === 'close')) {
            flush();
            result.push(lines[i]);
        } else {
            buffer.push(lines[i]);
        }
    }
    flush();
    return result.join('\n');
}

/**
 * Languages whose fenced content is exactly one JSON value (chart and
 * diagram specs). Used by splitJsonSpecTrailingContent.
 */
const JSON_SPEC_LANGS = new Set([
    'plotly', 'vega-lite', 'vega', 'joint', 'jointjs', 'packet',
]);

/**
 * Scan `text` for the end of its first balanced JSON value ({...} or
 * [...]), respecting string literals and escapes. Returns the index just
 * past the closing brace/bracket, or -1 if the text does not begin with
 * a JSON value or the value never balances.
 */
function scanJsonValueEnd(text: string): number {
    let i = 0;
    while (i < text.length && /\s/.test(text[i])) i += 1;
    if (i >= text.length) return -1;
    if (text[i] !== '{' && text[i] !== '[') return -1;
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (; i < text.length; i += 1) {
        const c = text[i];
        if (inStr) {
            if (esc) esc = false;
            else if (c === '\\') esc = true;
            else if (c === '"') inStr = false;
            continue;
        }
        if (c === '"') inStr = true;
        else if (c === '{' || c === '[') depth += 1;
        else if (c === '}' || c === ']') {
            depth -= 1;
            if (depth === 0) return i + 1;
        }
    }
    return -1;
}

/** One pass of the JSON-spec trailing-content splitter. */
function splitJsonSpecOnce(markdown: string): string {
    const lines = markdown.split('\n');
    const classes = classifyFenceLines(markdown);
    const out: string[] = [];
    let i = 0;
    while (i < lines.length) {
        const c = classes[i];
        if (c.kind !== 'open' || !JSON_SPEC_LANGS.has(c.info.toLowerCase())) {
            out.push(lines[i]);
            i += 1;
            continue;
        }
        let j = i + 1;
        while (j < lines.length && classes[j].kind !== 'close') j += 1;
        if (j >= lines.length) {
            // Unterminated fence (streaming) — leave untouched.
            out.push(lines[i]);
            i += 1;
            continue;
        }
        const inner = lines.slice(i + 1, j).join('\n');
        const jsonEnd = scanJsonValueEnd(inner);
        const after = jsonEnd >= 0 ? inner.slice(jsonEnd) : '';
        if (jsonEnd < 0 || after.trim() === '') {
            // No balanced JSON, or nothing trails it — block is fine as-is.
            for (let k = i; k <= j; k += 1) out.push(lines[k]);
            i = j + 1;
            continue;
        }
        // Close the fence at the JSON boundary; re-emit the remainder as
        // ordinary markdown so nested fences inside it lex normally.
        const fence = c.char.repeat(c.len);
        out.push(lines[i]);
        for (const l of inner.slice(0, jsonEnd).split('\n')) out.push(l);
        out.push(fence);
        out.push('');
        const remainder = after.replace(/^[ \t]+/, '');
        for (const l of remainder.split('\n')) out.push(l);
        // Keep the original close line only if the remainder leaves a
        // fence open (it then serves as that fence's closer).
        const remClasses = classifyFenceLines(remainder);
        const last = remClasses[remClasses.length - 1];
        if (last && (last.kind === 'open' || last.kind === 'content')) {
            out.push(lines[j]);
        }
        i = j + 1;
    }
    return out.join('\n');
}

/**
 * Split JSON-spec fenced blocks (plotly, vega-lite, …) whose content has
 * non-whitespace text glued after the end of the JSON value.
 *
 * Models sometimes omit the closing fence and run prose — or an entire
 * second fenced block — directly onto the closing brace of the spec.
 * CommonMark then treats everything up to the NEXT fence line as content
 * of the first block, so the spec fails to parse and any nested block is
 * swallowed. This pass closes the fence at the first balanced JSON
 * boundary and re-emits the trailing content as ordinary markdown.
 *
 * Runs up to a few passes so a nested spec block surfaced by one split
 * can itself be split. Well-formed blocks, unterminated (streaming)
 * blocks, and non-JSON languages are left untouched.
 */
export function splitJsonSpecTrailingContent(markdown: string): string {
    let prev = markdown;
    for (let pass = 0; pass < 3; pass += 1) {
        const next = splitJsonSpecOnce(prev);
        if (next === prev) return next;
        prev = next;
    }
    return prev;
}
