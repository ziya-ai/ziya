/**
 * Shape digest for tool results.
 *
 * The collapsed tool block needs something better than the first 200 bytes
 * of the payload, which for JSON is a lone "{" or a run of keys and for a
 * shell transcript is the command echoed back.  This module derives a
 * one-line headline, status chips, key/value facts and (for JSON) a table or
 * tree from the payload's *shape* alone.  It knows no tool names: a wrapper
 * with `total` and an array is a collection, an object whose only payload is
 * a long string is a message, and so on.  Everything here is pure and must
 * never throw -- a digest that fails falls back to plain text.
 */
import { parsePythonLiteral } from './mcpFormatter';

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'neutral';
export interface DigestChip { label: string; tone: Tone; }
export interface DigestFact { key: string; value: string; }
export interface DigestTable {
    columns: string[];
    hidden: string[];
    rows: Array<Record<string, unknown>>;
}
export type DigestKind = 'collection' | 'record' | 'message' | 'shell' | 'text' | 'diff' | 'json-unparsed';

export interface ToolDigest {
    kind: DigestKind;
    /** One sentence answering "what did this return?" */
    headline: string;
    /** Header-right summary, e.g. "102 KB · JSON" or "125 lines · 3.5 KB". */
    byline: string;
    chips: DigestChip[];
    facts: DigestFact[];
    /** Output lines for shell / text previews (already trimmed). */
    preview: string[];
    moreLines?: number;
    /** collection: first and last identifier. */
    range?: { first: string; last: string };
    table?: DigestTable;
    /** Parsed JSON value for the Tree viewer. */
    tree?: unknown;
    /** record: scalar fields not shown as facts. */
    moreFields?: number;
    /** message: the unwrapped prose, with escapes resolved. */
    text?: string;
    link?: { href: string; label: string };
    warning?: string;
    /** json-unparsed: keys seen in the prefix. */
    keys?: string[];
    /** collection: rebuilt from a payload that was cut off mid-object. */
    truncated?: boolean;
}

export function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    const units = ['KB', 'MB', 'GB'];
    let v = n / 1024;
    let u = 0;
    while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
    return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[u]}`;
}

const ID_KEYS = ['key', 'id', 'name', 'title', 'path', 'filepath', 'ticketId', 'issueId', 'conversationId'];
const TITLE_KEYS = ['name', 'title', 'summary', 'displayName', 'display_name', 'label', 'groupName', 'group_name', 'subject'];
const LABEL_KEYS = ['summary', 'title', 'name', 'description', 'message', 'text'];
const ENUM_PREFERENCE = ['status', 'state', 'type', 'priority', 'severity', 'kind', 'level', 'result'];
const PERSON_KEYS: Array<[string, string]> = [
    ['assignee', 'assignees'], ['owner', 'owners'], ['author', 'authors'], ['user', 'users'], ['reporter', 'reporters'],
];
const MESSAGE_KEYS = ['message', 'text', 'output', 'result', 'content', 'summary', 'detail', 'details', 'response'];
const FACT_PRIORITY = ['id', 'key', 'status', 'state', 'type', 'owner', 'secondaryOwner', 'primaryOwner', 'email',
    'url', 'path', 'count', 'total', 'memberCount', 'description'];
const COUNT_KEYS = ['total', 'totalCount', 'total_count', 'count', 'totalResults', 'total_results', 'numFound', 'hits'];
const MAX_FACTS = 6;
const MAX_FACT_VALUE = 80;
const MAX_COLUMNS = 5;
const PREVIEW_LINES = 4;
const URL_RE = /https?:\/\/[^\s)"'<>]+/g;
const WARNING_RE = /\b(could not|cannot|can't|couldn't|not found|failed|failure|warning|unable|error|denied|refused|missing)\b/i;
const EXIT_RE = /^\[Exit code: (\d+)\]$/;
/** An entry in a list-shaped message: "[id] ...", "- ...", "1. ...". */
const RECORD_RE = /^(\[[^\]\n]{1,40}\]|[-*•]\s|\d+[.)]\s)/;
/** A line with at least one word; "/**", "{" and "*" have none. */
const MEANINGFUL_RE = /[A-Za-z0-9]{3,}/;
/** Truncation marker a server appends after cutting a payload. */
const TRUNC_TAIL_RE = /\s*(?:\.\.\.|…)?\s*\[[^\]\n]*truncated[^\]\n]*\]\s*$/i;
const DIFF_HEAD_RE = /^(diff --git |--- (a\/|\/dev\/null))/;

const isScalar = (v: unknown): v is string | number | boolean =>
    typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean';
const isRecord = (v: unknown): v is Record<string, unknown> =>
    !!v && typeof v === 'object' && !Array.isArray(v);
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

function parseJsonish(raw: string): { value: unknown; ok: boolean; looksJson: boolean } {
    const t = raw.trim();
    const looksJson = t.startsWith('{') || t.startsWith('[');
    if (!looksJson) return { value: undefined, ok: false, looksJson };
    try { return { value: JSON.parse(t), ok: true, looksJson }; } catch { /* fall through */ }
    try { return { value: parsePythonLiteral(t), ok: true, looksJson }; } catch { /* truncated or not a literal */ }
    return { value: undefined, ok: false, looksJson };
}

/** The largest array of objects reachable at depth 1, with its wrapper count. */
function findCollection(value: unknown): { items: Record<string, unknown>[]; total: number; noun: string } | null {
    const objects = (a: unknown): a is Record<string, unknown>[] =>
        Array.isArray(a) && a.length > 0 && a.every(isRecord);
    if (objects(value)) return { items: value, total: value.length, noun: 'item' };
    if (!isRecord(value)) return null;
    let best: { key: string; items: Record<string, unknown>[] } | null = null;
    for (const [k, v] of Object.entries(value)) {
        if (objects(v) && (!best || v.length > best.items.length)) best = { key: k, items: v };
    }
    if (!best) return null;
    let total = best.items.length;
    for (const ck of COUNT_KEYS) {
        const c = value[ck];
        if (typeof c === 'number' && c >= best.items.length) { total = c; break; }
    }
    const noun = best.key.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().replace(/s$/, '');
    return { items: best.items, total, noun };
}

function idField(items: Record<string, unknown>[]): string | null {
    for (const k of ID_KEYS) {
        const hits = items.filter(i => typeof i[k] === 'string' || typeof i[k] === 'number').length;
        if (hits >= items.length * 0.8) return k;
    }
    return null;
}

function enumChips(items: Record<string, unknown>[]): DigestChip[] {
    if (items.length < 4) return [];
    const candidates: Array<{ key: string; counts: Map<string, number> }> = [];
    const keys = new Set<string>();
    items.forEach(i => Object.keys(i).forEach(k => keys.add(k)));
    for (const k of keys) {
        const counts = new Map<string, number>();
        let strings = 0;
        for (const i of items) {
            const v = i[k];
            if (typeof v !== 'string') continue;
            strings++;
            counts.set(v, (counts.get(v) ?? 0) + 1);
        }
        if (strings < items.length * 0.8) continue;
        if (counts.size < 2 || counts.size > 8 || counts.size > items.length / 2) continue;
        candidates.push({ key: k, counts });
    }
    candidates.sort((a, b) => {
        const ia = ENUM_PREFERENCE.indexOf(a.key), ib = ENUM_PREFERENCE.indexOf(b.key);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    const chips: DigestChip[] = [];
    for (const c of candidates.slice(0, 2)) {
        const sorted = [...c.counts.entries()].sort((a, b) => b[1] - a[1]);
        for (const [v, n] of sorted) chips.push({ label: `${clip(v, 24)} ${n}`, tone: 'info' });
    }
    for (const [k, word] of PERSON_KEYS) {
        const vals = new Set(items.map(i => i[k]).filter(v => typeof v === 'string'));
        if (vals.size > 1) { chips.push({ label: `${vals.size} ${word}`, tone: 'neutral' }); break; }
    }
    return chips;
}

function tableFor(items: Record<string, unknown>[], id: string | null): DigestTable {
    const freq = new Map<string, number>();
    items.forEach(i => Object.keys(i).forEach(k => freq.set(k, (freq.get(k) ?? 0) + 1)));
    const scalarish = (k: string) => items.some(i => isScalar(i[k]));
    const ordered = [...freq.keys()].filter(scalarish).sort((a, b) => (freq.get(b)! - freq.get(a)!));
    const columns: string[] = [];
    const take = (k: string) => { if (ordered.includes(k) && !columns.includes(k) && columns.length < MAX_COLUMNS) columns.push(k); };
    if (id) take(id);
    LABEL_KEYS.forEach(take);
    ENUM_PREFERENCE.forEach(take);
    PERSON_KEYS.forEach(([k]) => take(k));
    ordered.forEach(take);
    const hidden = [...freq.keys()].filter(k => !columns.includes(k));
    return { columns, hidden, rows: items };
}

function digestCollection(value: unknown, raw: string, truncated = false): ToolDigest | null {
    const c = findCollection(value);
    if (!c) return null;
    const id = idField(c.items);
    const shown = c.items.length;
    const headline = c.total > shown
        ? `${c.total} ${c.noun}s matched, showing ${shown}`
        : plural(shown, c.noun);
    const chips = enumChips(c.items);
    if (truncated) chips.unshift({ label: 'output truncated', tone: 'warn' });
    const d: ToolDigest = {
        kind: 'collection', headline, byline: `${formatBytes(raw.length)} · JSON${truncated ? ' (truncated)' : ''}`,
        chips, facts: [], preview: [], truncated: truncated || undefined,
        table: tableFor(c.items, id), tree: value,
    };
    if (id) d.range = { first: String(c.items[0][id]), last: String(c.items[shown - 1][id]) };
    return d;
}

function digestMessage(value: unknown, raw: string): ToolDigest | null {
    if (!isRecord(value)) return null;
    const entries = Object.entries(value);
    const strings = entries.filter(([, v]) => typeof v === 'string' && (v as string).length >= 40);
    if (strings.length !== 1) return null;
    const [msgKey, msg] = strings[0] as [string, string];
    if (!MESSAGE_KEYS.includes(msgKey) && entries.length > 1) return null;
    if (!entries.every(([k, v]) => k === msgKey || isScalar(v) || v == null)) return null;

    const text = msg.replace(/\r\n/g, '\n').trim();
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
    // A message that is a list of entries ("[id] ...", "- ...") is data, not
    // a status report: count it, preview its first entries, flag nothing.
    const paras = text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
    const records = paras.filter(p => RECORD_RE.test(p));
    if (records.length >= 2 && records.length >= paras.length * 0.6) {
        const n = typeof value.count === 'number' ? value.count : records.length;
        const preview = records.slice(0, 3).map(p => clip(p.split('\n')[0], 160));
        return {
            kind: 'message', headline: `${n} ${n === 1 ? 'entry' : 'entries'}`,
            byline: `${formatBytes(raw.length)} · list`, chips: [], facts: [], preview,
            moreLines: Math.max(0, records.length - preview.length), text, tree: value,
        };
    }
    const urls = text.match(URL_RE) ?? [];
    let link: ToolDigest['link'];
    if (urls[0]) {
        const idMatch = urls[0].match(/\b([A-Z][A-Z0-9]+-\d+)\b/);
        let label = idMatch ? idMatch[1] : '';
        if (!label) { try { label = new URL(urls[0]).host; } catch { label = clip(urls[0], 40); } }
        link = { href: urls[0], label };
    }
    // Only the opening lines report status; a failure word deeper in the
    // text is content.  (URL_RE is global, so .test() would carry lastIndex.)
    const warning = lines.slice(0, 2).find(l => WARNING_RE.test(l) && !/https?:\/\//.test(l));
    const headline = lines.filter(l => l !== warning && !/^https?:\/\//.test(l) && !/^\*\s+[0-9a-f]{7,}/.test(l)).pop()
        ?? lines[0] ?? '';
    const chips: DigestChip[] = [];
    const success = value.success ?? value.ok;
    if (success === true) chips.push({ label: 'success', tone: 'ok' });
    if (success === false) chips.push({ label: 'failed', tone: 'err' });
    return {
        kind: 'message', headline: clip(headline, 160), byline: `${formatBytes(raw.length)} · message`,
        chips, facts: [], preview: [], text, link, warning: warning ? clip(warning, 200) : undefined, tree: value,
    };
}

function digestRecord(value: unknown, raw: string): ToolDigest | null {
    if (!isRecord(value)) return null;
    const entries = Object.entries(value);
    const titleKey = TITLE_KEYS.find(k => typeof value[k] === 'string' && (value[k] as string).length <= 120);
    const headline = titleKey ? (value[titleKey] as string) : plural(entries.length, 'field');
    const scalars = entries.filter(([k, v]) => k !== titleKey && isScalar(v) && String(v).length <= MAX_FACT_VALUE);
    scalars.sort((a, b) => {
        const ia = FACT_PRIORITY.indexOf(a[0]), ib = FACT_PRIORITY.indexOf(b[0]);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    const facts = scalars.slice(0, MAX_FACTS).map(([k, v]) => ({ key: k, value: String(v) }));
    const chips: DigestChip[] = [];
    for (const k of ['status', 'state']) {
        if (typeof value[k] === 'string') { chips.push({ label: value[k] as string, tone: 'info' }); break; }
    }
    return {
        kind: 'record', headline: clip(headline, 160), byline: `${formatBytes(raw.length)} · ${plural(entries.length, 'field')}`,
        chips, facts, preview: [], tree: value,
        moreFields: entries.length - (titleKey ? 1 : 0) - facts.length,
    };
}

/**
 * Close a JSON prefix that was cut off mid-payload, after its last complete
 * array element.  Prefers the shallowest such point, so a partial last item
 * (one whose nested array happened to close) is dropped rather than kept.
 */
export function salvageTruncatedJson(raw: string): unknown | undefined {
    const s = raw.replace(TRUNC_TAIL_RE, '');
    const stack: string[] = [];
    let inStr = false;
    let esc = false;
    let cut = -1;
    let cutStack = '';
    let cutDepth = Infinity;
    for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (inStr) {
            if (esc) esc = false;
            else if (ch === '\\') esc = true;
            else if (ch === '"') inStr = false;
            continue;
        }
        if (ch === '"') inStr = true;
        else if (ch === '{' || ch === '[') stack.push(ch);
        else if (ch === '}' || ch === ']') {
            stack.pop();
            if (stack[stack.length - 1] === '[' && stack.length <= cutDepth) {
                cut = i + 1;
                cutStack = stack.join('');
                cutDepth = stack.length;
            }
        }
    }
    if (cut < 0) return undefined;
    const closers = cutStack.split('').reverse().map(c => (c === '{' ? '}' : ']')).join('');
    try { return JSON.parse(s.slice(0, cut) + closers); } catch { return undefined; }
}

/** A unified diff: file name(s), +/- counts, and the first changed lines. */
function digestDiff(raw: string): ToolDigest | null {
    const lines = raw.replace(/\r\n/g, '\n').trim().split('\n');
    if (!DIFF_HEAD_RE.test(lines[0] ?? '')) return null;
    const files: string[] = [];
    const changed: string[] = [];
    let add = 0;
    let del = 0;
    for (const l of lines) {
        if (l.startsWith('+++ ')) {
            const p = l.slice(4).replace(/^b\//, '').trim();
            if (p !== '/dev/null' && !files.includes(p)) files.push(p);
        } else if (/^(--- |diff --git |@@|index |new file mode|deleted file mode)/.test(l)) {
            continue;
        } else if (l.startsWith('+')) {
            add++; changed.push(l);
        } else if (l.startsWith('-')) {
            del++; changed.push(l);
        }
    }
    if (!add && !del) return null;
    const preview = changed.slice(0, PREVIEW_LINES).map(l => clip(l, 160));
    const chips: DigestChip[] = [];
    if (add) chips.push({ label: `+${add}`, tone: 'ok' });
    if (del) chips.push({ label: `−${del}`, tone: 'err' });
    return {
        kind: 'diff',
        headline: files.length > 1 ? `${files.length} files changed` : (files[0] ?? 'diff'),
        byline: `${plural(add + del, 'changed line')} · ${formatBytes(raw.length)}`,
        chips, facts: [], preview, moreLines: Math.max(0, changed.length - preview.length),
    };
}

function digestUnparsedJson(raw: string): ToolDigest {
    const keys: string[] = [];
    const seen = new Set<string>();
    const re = /"([A-Za-z_][\w-]*)"\s*:/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(raw)) && keys.length < 12) {
        if (!seen.has(m[1])) { seen.add(m[1]); keys.push(m[1]); }
    }
    const chips: DigestChip[] = [];
    for (const ck of COUNT_KEYS) {
        const t = raw.match(new RegExp(`"${ck}"\\s*:\\s*(\\d+)`));
        if (t) { chips.push({ label: `${ck} ${t[1]}`, tone: 'info' }); break; }
    }
    return {
        kind: 'json-unparsed',
        headline: `JSON (truncated) · ${plural(keys.length, 'key')} seen`,
        byline: `${formatBytes(raw.length)} · JSON (truncated)`,
        chips, facts: [], preview: [], keys,
    };
}

function digestShell(raw: string): ToolDigest {
    const all = raw.replace(/\r\n/g, '\n').split('\n');
    const chips: DigestChip[] = [];
    const output: string[] = [];
    for (const line of all) {
        if (/^\$ /.test(line)) continue;
        const exit = line.trim().match(EXIT_RE);
        if (exit) { chips.push({ label: `exit ${exit[1]}`, tone: 'err' }); continue; }
        output.push(line);
    }
    while (output.length && !output[output.length - 1].trim()) output.pop();
    while (output.length && !output[0].trim()) output.shift();
    const preview = output.slice(0, PREVIEW_LINES).map(l => clip(l, 160));
    return {
        kind: 'shell',
        headline: output.length ? `${plural(output.length, 'line')} of output` : 'no output',
        byline: `${plural(output.length, 'line')} · ${formatBytes(raw.length)}`,
        chips, facts: [], preview, moreLines: Math.max(0, output.length - preview.length),
    };
}

function digestText(raw: string): ToolDigest {
    const lines = raw.replace(/\r\n/g, '\n').split('\n');
    const nonEmpty = lines.map(l => l.trim()).filter(Boolean);
    // Skip lines with no words ("/**", "{", "*") and drop heading / comment
    // markers, so a file or markdown body headlines its first real line.
    const useful = nonEmpty
        .map(l => l.replace(/^#{1,6}\s+/, '').replace(/^(\/\*+|\*\/?(?=\s|$)|\/\/+)\s*/, '').trim())
        .filter(l => MEANINGFUL_RE.test(l));
    const first = useful[0] ?? nonEmpty[0] ?? '';
    const sentence = first.match(/^(.{20,}?[.!?])(\s|$)/);
    const headline = clip(sentence ? sentence[1] : first, 160);
    const rest = useful.slice(1, PREVIEW_LINES).map(l => clip(l, 160));
    return {
        kind: 'text', headline, byline: `${plural(lines.length, 'line')} · ${formatBytes(raw.length)}`,
        chips: [], facts: [], preview: rest, moreLines: Math.max(0, useful.length - 1 - rest.length),
    };
}

/**
 * Digest a tool result from its raw text.  `toolName` only selects the shell
 * transcript path; every other decision is made from the payload's shape.
 */
export function digestToolResult(toolName: string, raw: string): ToolDigest {
    try {
        if (/run_shell_command$/.test(toolName)) return digestShell(raw);
        const diff = digestDiff(raw);
        if (diff) return diff;
        const { value, ok, looksJson } = parseJsonish(raw);
        if (looksJson && !ok) {
            // Output cut off mid-payload: keep every complete element.
            const salvaged = salvageTruncatedJson(raw);
            return (salvaged !== undefined ? digestCollection(salvaged, raw, true) : null)
                ?? digestUnparsedJson(raw);
        }
        if (ok) {
            return digestCollection(value, raw)
                ?? digestMessage(value, raw)
                ?? digestRecord(value, raw)
                ?? digestText(raw);
        }
    } catch { /* never let a digest failure reach the renderer */ }
    return digestText(raw);
}
