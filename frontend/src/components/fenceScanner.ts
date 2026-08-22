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

import { LATEX_FENCE_LANGS } from '../constants/latexProfiles';
import { fenceBaseLang } from '../utils/mockupFence';

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
 *
 * Diff-scoped exception: when the active fence is a ```diff opener that
 * began at column 0, only a COLUMN-0 backtick run may close it. Every
 * line of a unified-diff body carries a +/-/space prefix, so a bare ```
 * that is part of the diff content (a fenced block inside the file being
 * patched, carried in as a context/added line) is always indented by at
 * least that prefix. Without this guard, classifyFenceLines accepts that
 * indented body fence as the close and truncates the diff mid-body; the
 * real wrapping close is markdown-level and sits at column 0.
 */
export function matchFenceClose(
    line: string,
    active: { char: FenceChar; len: number; info?: string; indent?: number },
): { len: number } | null {
    const re = active.char === '`' ? CLOSE_BACKTICK_RE : CLOSE_TILDE_RE;
    const m = re.exec(line);
    if (!m) return null;
    if (m[1].length < active.len) return null;
    // A column-0 ```diff fence is only closed by a column-0 backtick run:
    // any indented bare fence inside it is diff content, not the close.
    if (
        active.char === '`' &&
        (active.info || '').toLowerCase() === 'diff' &&
        (active.indent ?? 0) === 0 &&
        /^ /.test(line)
    ) {
        return null;
    }
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
    let active: { char: FenceChar; len: number; info: string; indent: number } | null = null;

    for (const line of lines) {
        if (active === null) {
            const open = matchFenceOpen(line);
            if (open) {
                active = { char: open.char, len: open.len, info: open.info, indent: open.indent };
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
            // Set when the search below is stopped by a real tagged opener
            // rather than by running off the end of the input. That is
            // definitive proof the bare fence is stray (any close found
            // beyond the tagged opener belongs to IT, not to us) -- distinct
            // from simply finding no close before EOF, where the "orphan"
            // interpretation is not yet certain and a markdown-content
            // heuristic decides instead.
            let abortedByTaggedOpener: boolean = false;
            for (let fj: number = fi + 1; fj < fenceLines.length; fj += 1) {
                // A real lang-tagged opener between here and the next bare
                // fence proves the bare fence we're scanning from has no
                // close of its own (it's a stray/orphan): the tagged block's
                // own close belongs to IT, not to us. Stop here rather than
                // consuming that close as if it paired with our bare fence,
                // which would swallow the tagged opener as "inner content"
                // and truncate/mis-pair the tagged block.
                //
                // Only applies when the mid-opener's run is >= our own fence
                // length: a SHORTER nested fence (e.g. ```diff inside an
                // outer ````) is literal content per CommonMark nesting
                // rules and cannot compete for our close, so it must not
                // abort the search.
                const midOpener = matchFenceOpen(fenceLines[fj]);
                if (midOpener && midOpener.char === '`' && midOpener.indent === 0 && midOpener.info !== '' && midOpener.len >= fLen) {
                    abortedByTaggedOpener = true;
                    break;
                }
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
            } else if (abortedByTaggedOpener) {
                // Definitively stray: drop just this bare fence line and
                // let the real tagged block ahead parse normally, without
                // relying on the remaining-content-looks-like-markdown guess.
                fi += 1;
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

/**
 * Upgrade an outer column-0 backtick fence to a longer run when its body
 * contains a backtick run of equal-or-greater length that would prematurely
 * close it. This commonly happens in `diff` blocks that patch a markdown/code
 * file containing its own ```sql / ```json fences, or in `markdown` blocks that
 * quote fenced examples: CommonMark closes the outer fence at the first inner
 * ``` and the remainder spills out as loose text.
 *
 * Outer fences whose body can legitimately wrap nested fences (NESTABLE_OUTER:
 * diff/markdown/md) are scanned with DEPTH PAIRING — a lang-tagged opener
 * descends one level, a bare fence ascends, and the true outer close is the
 * column-0 bare fence at depth 0. Other (non-nestable) outer fences keep the
 * conservative behavior of bailing at the first lang-tagged opener so a missing
 * close does not mis-pair with a later sibling block's bare fence.
 *
 * When a collision is found (an inner run length >= the outer run length) the
 * outer opener and its matched close are widened to maxInnerFence + 1 backticks.
 * Pure (no React) and exported so the behavior is directly unit-testable.
 */
const NESTABLE_OUTER = new Set(['diff', 'markdown', 'md']);

export function upgradeNestedFences(markdown: string): string {
    const lines = markdown.split('\n');
    let i = 0;
    while (i < lines.length) {
        // Gate the opener through the shared CommonMark rule so the tail of a
        // wrapped inline-code span is not taken as a fence opener. Only column-0
        // backtick fences are upgraded.
        const open = matchFenceOpen(lines[i]);
        if (open && open.char === '`' && open.indent === 0) {
            const outerLen = open.len;
            const info = open.info;
            const nestable = NESTABLE_OUTER.has(info.toLowerCase());
            let closeIdx = -1;
            let maxInnerFence = 0;
            let depth = 0;
            for (let j = i + 1; j < lines.length; j++) {
                const line = lines[j];
                if (!nestable) {
                    // Non-nestable outer fence: a lang-tagged opener means we
                    // overshot into the NEXT block; stop rather than mis-pair
                    // with a later bare ``` belonging to a different block.
                    const nextOpener = matchFenceOpen(line);
                    if (nextOpener && nextOpener.info !== '') break;
                    const cl = line.match(/^(`{3,})\s*$/);
                    if (cl && cl[1].length >= outerLen) { closeIdx = j; break; }
                    const innerFence = line.match(/^ {1,3}(`{3,})\s*$/);
                    if (innerFence) {
                        maxInnerFence = Math.max(maxInnerFence, innerFence[1].length);
                    }
                } else {
                    // Nestable outer fence (diff/markdown): depth-pair to find
                    // the true outer close (the column-0 bare fence at depth 0).
                    //
                    // A diff body carries inner fences as diff lines: an added
                    // code block is "+```sql ... +```", a context one is
                    // " ```sql ...  ```". The +/-/space prefix must be stripped
                    // before classifying the fence, otherwise diff-prefixed
                    // CLOSES never decrement depth, the counter never returns to
                    // 0, and the real outer close is consumed as a pop — leaving
                    // closeIdx = -1 and the block un-upgraded (it then truncates
                    // in the renderer at the first stray inner ```).
                    //
                    // True outer close: a bare backtick run at COLUMN 0 (no diff
                    // prefix) of length >= outer, seen at depth 0.
                    const bareCol0 = line.match(/^(`{3,})\s*$/);
                    if (bareCol0 && bareCol0[1].length >= outerLen && depth === 0) {
                        closeIdx = j;
                        break;
                    }
                    // Classify the inner fence shape with any leading +/- diff
                    // marker removed, so prefixed openers AND closes both move
                    // the depth counter symmetrically.
                    const stripped = line.replace(/^[+\-]/, '');
                    const innerOpen = stripped.match(/^\s*(`{3,})(\S.*)?$/);
                    if (innerOpen) {
                        maxInnerFence = Math.max(maxInnerFence, innerOpen[1].length);
                        const hasInfo = !!(innerOpen[2] && innerOpen[2].trim() !== '');
                        if (hasInfo) depth++;
                        else if (depth > 0) depth--;
                    }
                }
            }
            if (closeIdx !== -1 && maxInnerFence >= outerLen) {
                const newFence = '`'.repeat(maxInnerFence + 1);
                lines[i] = newFence + info;
                lines[closeIdx] = newFence;
            }
            // For a nestable block, jump past it so its inner lang-tagged fences
            // are not re-scanned as fresh top-level openers (which would wrongly
            // upgrade them).
            if (nestable && closeIdx !== -1) {
                i = closeIdx + 1;
                continue;
            }
        }
        i++;
    }
    return lines.join('\n');
}

/**
 * Languages whose fenced body is a self-contained diagram/spec/markup
 * source and can therefore NEVER legitimately contain a column-0
 * lang-tagged fence of its own.
 *
 * This property is what makes repairAtomicFenceRuns safe: encountering
 * such an opener while one of these blocks is open is positive proof the
 * close is missing, not evidence of legal nesting. Languages whose bodies
 * CAN carry fences (diff, markdown, md, and any prose/code language) are
 * deliberately absent and are skipped wholesale by the pass.
 *
 * LaTeX languages are spread in from the shared registry rather than listed,
 * so a new profile cannot miss the repair pass.  The remaining literals still
 * need manual sync with the diagram cases in determineTokenType — a language
 * present there but missing here only loses the repair, never renders wrongly.
 */
const ATOMIC_FENCE_LANGS = new Set([
    'mermaid', 'graphviz', 'dot',
    'vega-lite', 'vegalite', 'vega', 'plotly',
    'drawio', 'draw.io', 'designinspector',
    'packet', 'packet-diagram', 'bytefield',
    'music', 'sheet-music', 'vexflow',
    'joint', 'jointjs', 'diagram', 'd2', 'chord',
    'force-directed', 'forcedirected', 'network', 'd3',
    'html-mockup', 'ui-mockup', 'mockup',
    ...LATEX_FENCE_LANGS.filter(l => l !== 'latex'),
    'slidecast', 'slideshow', 'framechain',
    'basic-chart', 'chart',
]);

/** A column-0 backtick fence carrying a language tag, or null. */
function col0TaggedOpener(
    line: string,
): { char: FenceChar; len: number; info: string; indent: number } | null {
    const o = matchFenceOpen(line);
    if (!o || o.char !== '`' || o.indent !== 0 || o.info === '') return null;
    return o;
}

/**
 * A column-0 opener whose info string is PLAUSIBLE as a language tag: a
 * short token, optionally followed by simple word modifiers
 * ("html-mockup figure").
 *
 * The repair heuristics below infer "the close is missing" from finding an
 * opener inside an atomic block. That inference is only sound for a line
 * that could actually BE an opener. A mermaid node label or html-mockup
 * body line may quote a fence marker in order to discuss it: such a line
 * sits at column 0 and matches the CommonMark opener shape, but its info
 * string carries markup, quotes and punctuation. Accepting it made
 * repairAtomicFenceRuns synthesize a close mid-diagram and promote the
 * label line to an opener, destroying a block CommonMark had parsed
 * correctly (node A rendered alone, the diagram tail spilled out as a
 * separate code block).
 *
 * Scoped to the repair passes on purpose: classifyFenceLines keeps strict
 * CommonMark semantics, where an odd info string is still a valid opener.
 * Mirrors _LANG_TAG_RE in app/streaming_tool_executor.py.
 */
const PLAUSIBLE_LANG_RE = /^[a-zA-Z][a-zA-Z0-9+#.\-_]{0,30}$/;
const PLAUSIBLE_MODIFIER_RE = /^[a-zA-Z][a-zA-Z0-9_-]{0,20}$/;

function col0PlausibleOpener(
    line: string,
): { char: FenceChar; len: number; info: string; indent: number } | null {
    const o = col0TaggedOpener(line);
    if (!o) return null;
    const tokens = o.info.trim().split(/\s+/);
    if (!PLAUSIBLE_LANG_RE.test(tokens[0])) return null;
    for (let k = 1; k < tokens.length; k += 1) {
        if (!PLAUSIBLE_MODIFIER_RE.test(tokens[k])) return null;
    }
    return o;
}

/** A column-0 bare backtick run (a fence with no info string). */
function isBareCol0Fence(line: string): boolean {
    return /^`{3,}[ \t]*$/.test(line);
}

/**
 * Repair a run of atomic (diagram/mockup) fenced blocks in which an
 * opener's closing fence is missing.
 *
 * Models emitting several visualization blocks in sequence sometimes drop
 * one closing fence and leave stray bare fences between the blocks. Under
 * CommonMark that single omission inverts every fence that follows: the
 * NEXT block's opener is absorbed as content, its close is consumed as the
 * unterminated block's close, and the following bare fence becomes an
 * opener. One missing line silently destroys an unbounded number of
 * downstream diagrams — the packet block reports "Invalid JSON" because it
 * is being handed the tail of an html-mockup.
 *
 * Two repairs, both driven by the atomic property above:
 *
 *   1. An atomic block interrupted by another column-0 lang-tagged opener
 *      gets a synthesized close inserted immediately before that opener.
 *   2. An orphan bare fence left between a now-closed atomic block and the
 *      next lang-tagged opener is dropped, since it can only be the
 *      residue of the same mis-pairing.
 *
 * Deliberately conservative:
 *   - Non-atomic blocks (diff, markdown, python, …) are skipped whole, so
 *     a column-0 fence inside a diff body is never misread as an opener.
 *   - An atomic block with no close AND no interrupting opener is the
 *     streaming case and is left untouched, so a diagram mid-stream is not
 *     repeatedly closed and reopened as it arrives.
 *   - Orphan removal requires a following lang-tagged opener, so a bare
 *     fence that legitimately opens an untagged code block survives.
 */
export function repairAtomicFenceRuns(markdown: string): string {
    const lines = markdown.split('\n');
    const out: string[] = [];
    let i = 0;

    while (i < lines.length) {
        const open = col0TaggedOpener(lines[i]);
        if (!open) {
            out.push(lines[i]);
            i += 1;
            continue;
        }

        const active = { char: open.char, len: open.len, info: open.info, indent: 0 };

        // Non-atomic opener: its body may legally contain fences. Copy the
        // whole block through untouched so nothing inside it is examined.
        // Matched on the base language: an info string may carry a variant
        // modifier ("html-mockup figure"), and treating that as a non-atomic
        // language would silently forfeit the repair for exactly the blocks
        // most likely to appear several-in-a-row.
        if (!ATOMIC_FENCE_LANGS.has(fenceBaseLang(open.info))) {
            let j = i + 1;
            while (j < lines.length && !matchFenceClose(lines[j], active)) j += 1;
            if (j >= lines.length) {
                // Unterminated non-atomic block (streaming): emit the opener
                // and continue scanning normally from the next line.
                out.push(lines[i]);
                i += 1;
                continue;
            }
            for (let k = i; k <= j; k += 1) out.push(lines[k]);
            i = j + 1;
            continue;
        }

        // Atomic opener: find its close, or the opener that proves the close
        // is missing, whichever comes first.
        let closeIdx = -1;
        let interruptIdx = -1;
        for (let j = i + 1; j < lines.length; j += 1) {
            if (matchFenceClose(lines[j], active)) {
                closeIdx = j;
                break;
            }
            if (col0PlausibleOpener(lines[j])) {
                interruptIdx = j;
                break;
            }
        }

        if (interruptIdx !== -1) {
            // Repair 1: synthesize the missing close before the next opener.
            for (let k = i; k < interruptIdx; k += 1) out.push(lines[k]);
            out.push(open.char.repeat(open.len));
            i = interruptIdx;
            continue;
        }

        if (closeIdx === -1) {
            // Streaming tail — the close simply has not arrived yet.
            out.push(lines[i]);
            i += 1;
            continue;
        }

        for (let k = i; k <= closeIdx; k += 1) out.push(lines[k]);
        i = closeIdx + 1;

        // Repair 2: drop an orphan bare fence sitting between this closed
        // block and the next lang-tagged opener.
        let p = i;
        while (p < lines.length && lines[p].trim() === '') p += 1;
        if (p < lines.length && isBareCol0Fence(lines[p])) {
            let q = p + 1;
            while (q < lines.length && lines[q].trim() === '') q += 1;
            if (q < lines.length && col0PlausibleOpener(lines[q])) {
                for (let k = i; k < p; k += 1) out.push(lines[k]);
                i = p + 1;
            }
        }
    }

    return out.join('\n');
}
/**
 * Inline code-span scanning.
 *
 * This module's block-level model (classifyFenceLines and friends) covers
 * fenced blocks; the header above explicitly scopes inline code spans out. But
 * the preprocessing passes that consult this module also need to avoid
 * rewriting the interior of a `` `...` `` span — math preprocessing wrote its
 * marker inside code spans, so a user who typed `` `$x$` `` saw renderer
 * internals instead of literal text.
 *
 * findCodeSpans is the inline analogue of applyOutsideFences, and it implements
 * the CommonMark rule rather than approximating it with /`[^`]*`/:
 *
 *   - A span opens with a run of N backticks and closes with the next run of
 *     EXACTLY N backticks. A longer or shorter run is span content, which is
 *     what makes ``a ` b`` a single span rather than two.
 *   - A run with no matching close is literal text. Treating it as an open
 *     span would swallow the remainder of the document.
 *   - A span may contain a newline, so this cannot be done line-by-line.
 *
 * Agreement with marked's own inline tokenizer is asserted case-by-case in
 * components/__tests__/codeSpanScanner.test.ts; that equivalence is the
 * correctness criterion, since these spans are handed to marked afterwards.
 */

/**
 * Locate every inline code span, as [startIndex, endIndexExclusive] pairs
 * covering the span INCLUDING its backtick delimiters. Pairs are
 * non-overlapping and in ascending order.
 */
export function findCodeSpans(text: string): Array<[number, number]> {
    const spans: Array<[number, number]> = [];
    let i = 0;
    while (i < text.length) {
        if (text[i] !== '`') {
            i += 1;
            continue;
        }
        // Length of the opening backtick run.
        let openLen = 0;
        while (text[i + openLen] === '`') openLen += 1;
        const afterOpen = i + openLen;

        // Scan for a closing run of exactly openLen backticks. Runs of a
        // different length are content and are skipped whole, so the scan
        // cannot mistake part of a longer run for a close.
        let j = afterOpen;
        let closeStart = -1;
        while (j < text.length) {
            if (text[j] !== '`') {
                j += 1;
                continue;
            }
            let runLen = 0;
            while (text[j + runLen] === '`') runLen += 1;
            if (runLen === openLen) {
                closeStart = j;
                break;
            }
            j += runLen;
        }

        if (closeStart === -1) {
            // Unmatched opening run: literal text. Resume after the run so a
            // later, properly matched span is still found.
            i = afterOpen;
            continue;
        }

        spans.push([i, closeStart + openLen]);
        i = closeStart + openLen;
    }
    return spans;
}

/**
 * Does a multi-line inline `text` token hold preformatted block content
 * (stripped-fence or indented code) rather than soft-wrapped prose?
 *
 * The renderer's 'text' case wraps such tokens in a <pre> to keep newlines.
 * The original test was merely "contains a newline", which also matched
 * ordinary soft-wrapped prose: when a list fails to start (no blank line
 * before "4.", and CommonMark only lets a list interrupt a paragraph at
 * "1."), the whole group lexes as ONE paragraph, and interleaved codespans
 * split it into text fragments that each begin with "\n". Those fragments
 * were then boxed as monospace <pre> mid-sentence — and a block <pre>
 * nested inside <p> also breaks the surrounding layout.
 *
 * Preformatted content keeps its leading whitespace on every continuation
 * line (that indentation is what made it a code block in the first place);
 * soft-wrapped prose does not. Requiring indentation on ALL continuation
 * lines separates the two without a lexer change.
 */
export function isPreformattedTextToken(text: string): boolean {
    if (!text.includes('\n')) return false;
    const lines = text.split('\n');
    // Need at least two lines with content: a lone "\n" or a trailing
    // newline is a separator, not a block.
    if (lines.filter(l => l.trim() !== '').length < 2) return false;
    const continuations = lines.slice(1).filter(l => l.trim() !== '');
    if (continuations.length === 0) return false;
    return continuations.every(l => /^\s/.test(l));
}

/**
 * Apply transform to the regions of text OUTSIDE inline code spans, leaving
 * span interiors (and their delimiters) byte-identical.
 *
 * Intended to wrap a preprocessing pass that rewrites markdown text, so that
 * the pass cannot corrupt content the user marked as code.
 */
export function applyOutsideCodeSpans(
    text: string,
    transform: (segment: string) => string,
): string {
    const spans = findCodeSpans(text);
    if (spans.length === 0) {
        return transform(text);
    }
    let out = '';
    let pos = 0;
    for (let k = 0; k < spans.length; k += 1) {
        const [start, end] = spans[k];
        out += transform(text.slice(pos, start));
        out += text.slice(start, end);
        pos = end;
    }
    out += transform(text.slice(pos));
    return out;
}

/**
 * Repair fence openers a model glued directly onto the end of a prose line
 * (a sentence ending in '.' or ':' immediately followed by a fence opener
 * with no intervening newline), which CommonMark does not recognise as a
 * fence at all.
 *
 * Must iterate.  A glued opener inverts fence parity for everything after
 * it: the block's real closing fence is read as an OPENER, so the remainder
 * of the message classifies as fence content and applyOutsideFences
 * declines to transform it -- correctly, given the state it was handed.
 * A single pass therefore repaired only the FIRST glued opener in a
 * message; in a long multi-chart answer every later one survived and
 * rendered as literal prose, and the resulting one-backtick offset
 * re-paired every inline code span in the paragraphs downstream.
 *
 * Iteration does not weaken the inside-fence guard: every pass
 * re-classifies, so a glued fence quoted inside a genuine code block stays
 * untouched however many passes run.
 */
export function repairGluedFenceOpeners(markdown: string): string {
    let current = markdown;
    // One pass can repair several openers (one per outside-fence segment);
    // the cap only bounds pathological input.
    for (let pass = 0; pass < 32; pass += 1) {
        const next = applyOutsideFences(current, (s) =>
            s.replace(/([^\n`])(`{3,}[a-zA-Z][a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2'),
        );
        if (next === current) {
            return current;
        }
        current = next;
    }
    return current;
}