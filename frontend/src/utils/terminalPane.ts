/**
 * Terminal pane model: one shape for a local shell transcript and a shadow
 * terminal result (components/TerminalPane.tsx renders it).
 *
 * Both are "a command ran somewhere and this is what the terminal showed",
 * so they get the same header (where · command · status chips) and body (the
 * transcript).  The differences are an indicator colour, a badge, and a seq
 * gutter on the remote side, where every line comes from a numbered journal
 * record.  Fields the model does not need to see at a glance -- ok, idle,
 * next_seq, lease provenance -- stay out of the pane; the raw JSON remains
 * reachable through the generic block's Raw view when a result does not
 * parse here.
 *
 * Pure: strings in, model out, never throws.
 */
import { coerceToolResultObject, stripRedundantCdForDisplay } from './mcpFormatter';
import { looksLikeBlockedCommand } from './blockedCommand';

export type PaneKind = 'local' | 'remote';
export type PaneTool = 'shell' | 'shadow_send' | 'shadow_wait' | 'shadow_read';
export type PaneLineKind = 'cmd' | 'output' | 'exit' | 'meta';
export type ChipTone = 'ok' | 'warn' | 'err' | 'info' | 'neutral';

export interface PaneLine {
    kind: PaneLineKind;
    /** May contain newlines: one journal record, or the whole local output. */
    text: string;
    /** Journal seq (remote only); drawn in the gutter. */
    seq?: number;
}

export interface PaneChip { label: string; tone: ChipTone; title?: string; }

export interface TerminalPaneModel {
    kind: PaneKind;
    tool: PaneTool;
    /** Header: the command that ran, or what the tool did ("wait → exit"). */
    command: string;
    /** Working directory, when it could be determined; null leaves the slot empty. */
    cwd: string | null;
    session?: string;
    sessionLabel?: string;
    chips: PaneChip[];
    lines: PaneLine[];
    /** Journal events folded out of the body, described one per entry. */
    hidden: string[];
    seqRange?: { from: number; to: number };
    /** Footer status word, e.g. "idle". */
    status?: string;
    /** The tool's own "what to call next" hint. */
    next?: string;
    outputLineCount: number;
}

const TERMINAL_TOOL_RE = /^(?:mcp_)?(run_shell_command|shadow_send|shadow_wait|shadow_read)$/;

export function isTerminalTool(toolName: string): boolean {
    return TERMINAL_TOOL_RE.test(toolName);
}

// Anchored block detection lives in blockedCommand.ts (a result that merely
// quotes the sentinel is a normal result); re-exported for callers and tests.
export { looksLikeBlockedCommand };

/**
 * The display header from the fence's `|`-separated segments after the tool
 * name: a trailing short alphanumeric segment is the language hint, and a
 * shell header ("Shell: $ a | b") may itself contain pipes.  Mirrors the
 * reconstruction GenericToolBlock does.
 */
export function toolHeaderFromParts(parts: string[]): string {
    if (parts.length > 1 && /^[a-zA-Z][a-zA-Z0-9]{0,19}$/.test(parts[parts.length - 1].trim())) {
        return parts.slice(0, -1).join('|');
    }
    return parts.join('|');
}

// ── prompts ─────────────────────────────────────────────────────────────

const PATH = String.raw`(?:~|/)[^\s\]]*`;

/**
 * Prompt shapes a working directory is read from, most specific first.  A
 * path must start with `~` or `/`; anything else is not confidently a cwd
 * and the slot is left empty.  Grown as prompts turn up.
 */
const PROMPT_FORMATS: Array<{ re: RegExp; cwd: number; exit?: number }> = [
    // "(26-10-09 20:42:48) <0> [~/workplace/x]": last exit status then the path.
    { re: new RegExp(String.raw`<(\d+)>\s*\[(${PATH})\]\s*$`), cwd: 2, exit: 1 },
    // "[~/x]$" / "[~/x]" alone on the line.
    { re: new RegExp(String.raw`^\s*\[(${PATH})\]\s*[$#%>]?\s*$`), cwd: 1 },
    // "user@host:~/x$ " (bash default).
    { re: new RegExp(String.raw`^[\w.-]+@[\w.-]+:(${PATH})\s*[$#%>]\s*$`), cwd: 1 },
    // "[user@host ~/x]$ " (RHEL default).
    { re: new RegExp(String.raw`^\[[\w.-]+@[\w.-]+\s+(${PATH})\]\s*[$#%>]\s*$`), cwd: 1 },
    // "~/x $ ", "~/x ❯ ", "/srv/app> ".
    { re: new RegExp(String.raw`^(${PATH})\s*[$#%>❯›]\s*$`), cwd: 1 },
];

export interface PromptInfo { cwd: string; exitCode?: number; }

/** The working directory (and exit status) a shell prompt line reports, or null. */
export function parsePromptLine(line: string): PromptInfo | null {
    const t = line.replace(/\x1b\[[0-9;]*m/g, '').trimEnd();
    if (!t) return null;
    for (const f of PROMPT_FORMATS) {
        const m = f.re.exec(t);
        if (!m) continue;
        const info: PromptInfo = { cwd: m[f.cwd] };
        if (f.exit !== undefined) info.exitCode = Number(m[f.exit]);
        return info;
    }
    return null;
}

const CD_PREFIX_RE = /^\s*cd\s+("([^"]*)"|'([^']*)'|[^\s&;|]+)\s*&&\s*(\S[\s\S]*)$/;

/** `cd X && rest` → { cwd: X, command: rest }; otherwise cwd null. */
export function splitCdPrefix(command: string): { cwd: string | null; command: string } {
    const m = CD_PREFIX_RE.exec(command || '');
    if (!m) return { cwd: null, command };
    const cwd = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[1];
    return { cwd: cwd.replace(/\/+$/, '') || '/', command: m[4].trim() };
}

/** `/Users/me/x` and `/home/me/x` read as `~/x`. */
export function abbreviateHome(path: string): string {
    return path.replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, '~');
}

// ── journal transcripts ─────────────────────────────────────────────────

const RECORD_RE = /^\[(\d+)\] (.*)$/;
const TRUNC_RE = /^…\s*\[transcript truncated\]\s*…$/;

interface JournalRecord { seq?: number; kind: PaneLineKind; text: string; exitCode?: number | null; }

/** Records of a `format_records` transcript: `[seq] …` starts one, five-space indents continue it. */
export function parseTranscript(text: string): JournalRecord[] {
    const out: JournalRecord[] = [];
    let cur: { seq?: number; text: string } | null = null;
    const flush = () => { if (cur) out.push(classify(cur)); cur = null; };
    for (const line of text.replace(/\r\n/g, '\n').split('\n')) {
        const m = RECORD_RE.exec(line);
        if (m) { flush(); cur = { seq: Number(m[1]), text: m[2] }; continue; }
        if (TRUNC_RE.test(line.trim())) { flush(); out.push({ kind: 'meta', text: line.trim() }); continue; }
        if (cur) { cur.text += '\n' + (line.startsWith('     ') ? line.slice(5) : line); continue; }
        if (line.trim()) cur = { text: line };
    }
    flush();
    return out;
}

function classify(r: { seq?: number; text: string }): JournalRecord {
    const t = r.text;
    if (t.startsWith('$ ')) return { seq: r.seq, kind: 'cmd', text: t.slice(2) };
    if (t.startsWith('→ ')) {
        const m = /^→ exit (-?\d+)/.exec(t);
        return { seq: r.seq, kind: 'exit', text: t, exitCode: m ? Number(m[1]) : null };
    }
    if (/^[⏺●]/.test(t)) return { seq: r.seq, kind: 'meta', text: t };
    return { seq: r.seq, kind: 'output', text: t };
}

/** Lease / attach bookkeeping and the wrapper's own overlay echo (●) are folded away. */
const HIDDEN_META_RE = /^●|control_(send|acquire|release)|\blease\b|^⏺ (attach|detach)/i;

// ── chips ───────────────────────────────────────────────────────────────

function exitChip(code: number | null | undefined, fromPrompt = false): PaneChip | null {
    if (code === undefined) return null;
    const title = fromPrompt ? 'exit status read from the prompt line' : undefined;
    if (code === null) return { label: 'done · exit code unknown', tone: 'neutral' };
    return code === 0
        ? { label: '✓ exit 0', tone: 'ok', title }
        : { label: `✗ exit ${code}`, tone: 'err', title };
}

export function formatElapsed(ms: number): string {
    if (ms < 1000) return `${ms} ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
    const m = Math.floor(ms / 60_000);
    const s = Math.round((ms % 60_000) / 1000);
    return s ? `${m} min ${s} s` : `${m} min`;
}

const countLines = (lines: PaneLine[]) =>
    lines.filter(l => l.kind === 'output').reduce((n, l) => n + l.text.split('\n').length, 0);

// ── builders ────────────────────────────────────────────────────────────

function buildLocal(content: string, header: string): TerminalPaneModel {
    // The formatter's fade markup may be persisted with the transcript.
    const text = content.replace(/<span style="[^"]*">/g, '').replace(/<\/span>/g, '').replace(/\r\n/g, '\n');
    const rows = text.split('\n');
    let command = (/\$\s+(.*)$/.exec(header) || [])[1] || '';
    if (rows[0]?.startsWith('$ ')) command = rows.shift()!.slice(2);
    while (rows.length && !rows[rows.length - 1].trim()) rows.pop();
    let exitCode = 0;
    const exitM = rows.length ? /^\[Exit code: (-?\d+)\]$/.exec(rows[rows.length - 1].trim()) : null;
    if (exitM) { exitCode = Number(exitM[1]); rows.pop(); }
    while (rows.length && !rows[rows.length - 1].trim()) rows.pop();

    const projectPath: string | null = typeof window !== 'undefined'
        ? ((window as any).__ZIYA_CURRENT_PROJECT_PATH__ || null) : null;
    let cwd = projectPath;
    const split = splitCdPrefix(command);
    if (split.cwd !== null) {
        const stripped = stripRedundantCdForDisplay(command);
        if (stripped !== command) {
            command = stripped;                 // cd to the project root: a no-op
        } else {
            cwd = /^[~/]/.test(split.cwd) ? split.cwd
                : projectPath ? `${projectPath}/${split.cwd.replace(/^\.\//, '')}` : split.cwd;
            command = split.command;
        }
    }
    const lines: PaneLine[] = rows.length ? [{ kind: 'output', text: rows.join('\n') }] : [];
    const chips: PaneChip[] = [exitChip(exitCode)!, { label: `${countLines(lines)} lines`, tone: 'neutral' }];
    return {
        kind: 'local', tool: 'shell', command, cwd: cwd ? abbreviateHome(cwd) : null,
        chips, lines, hidden: [], outputLineCount: countLines(lines),
    };
}

interface RemoteParse { lines: PaneLine[]; hidden: string[]; prompt: PromptInfo | null; exit: JournalRecord | null; }

function digestRecords(records: JournalRecord[], cmdSeq: number | null, sent: string | null): RemoteParse {
    const lines: PaneLine[] = [];
    const hidden: string[] = [];
    let prompt: PromptInfo | null = null;
    let exit: JournalRecord | null = null;
    let echoSeen = false;
    for (const r of records) {
        if (r.kind === 'meta' && HIDDEN_META_RE.test(r.text)) { hidden.push(r.text.split('\n')[0]); continue; }
        if (r.kind === 'exit') {
            if (cmdSeq !== null && r.seq !== undefined && r.seq < cmdSeq) { hidden.push(`earlier: ${r.text}`); continue; }
            exit = r;
            lines.push({ kind: 'exit', text: r.text, seq: r.seq });
            continue;
        }
        if (r.kind === 'output') {
            const rows = r.text.split('\n');
            while (rows.length && !rows[rows.length - 1].trim()) rows.pop();
            const last = rows[rows.length - 1];
            const p = last !== undefined ? parsePromptLine(last) : null;
            if (p) { prompt = p; rows.pop(); hidden.push(`prompt: ${last.trim()}`); }
            while (rows.length && !rows[rows.length - 1].trim()) rows.pop();
            if (!echoSeen && sent && rows[0]?.trim() === sent.trim()) {
                rows.shift(); echoSeen = true; hidden.push('echoed command line');
            }
            while (rows.length && !rows[0].trim()) rows.shift();
            if (!rows.length) continue;
            lines.push({ kind: 'output', text: rows.join('\n'), seq: r.seq });
            continue;
        }
        lines.push({ kind: r.kind, text: r.text, seq: r.seq });
    }
    return { lines, hidden, prompt, exit };
}

function remoteCwd(sent: string | null, prompt: PromptInfo | null): { cwd: string | null; command: string } {
    if (sent) {
        const split = splitCdPrefix(sent);
        if (split.cwd && /^[~/]/.test(split.cwd)) return { cwd: split.cwd, command: split.command };
        return { cwd: prompt?.cwd ?? null, command: sent };
    }
    return { cwd: prompt?.cwd ?? null, command: '' };
}

function buildRemote(tool: PaneTool, obj: Record<string, any>): TerminalPaneModel | null {
    const transcript = tool === 'shadow_wait' ? obj.tail : obj.transcript;
    if (typeof transcript !== 'string') return null;
    const sent = typeof obj.sent === 'string' ? obj.sent : null;
    const cmdSeq = typeof obj.cmd_seq === 'number' ? obj.cmd_seq : null;
    const parsed = digestRecords(parseTranscript(transcript), cmdSeq, sent);
    const { cwd, command: sentCommand } = remoteCwd(sent, parsed.prompt);
    const chips: PaneChip[] = [];
    let status: string | undefined;
    let command = sentCommand;

    // Exit status: the tool's own field, then the transcript's exit record,
    // then the prompt's "<N>" when the shell prints one.
    let exit: PaneChip | null = null;
    if (tool === 'shadow_wait' && 'exit_code' in obj) {
        exit = exitChip(obj.exit_code === null ? (parsed.prompt?.exitCode ?? null) : obj.exit_code,
                        obj.exit_code === null && parsed.prompt?.exitCode !== undefined);
    } else if (parsed.exit) {
        exit = parsed.exit.exitCode === null && parsed.prompt?.exitCode !== undefined
            ? exitChip(parsed.prompt.exitCode, true) : exitChip(parsed.exit.exitCode);
    } else if (parsed.prompt?.exitCode !== undefined && (tool !== 'shadow_send' || !obj.still_running)) {
        exit = exitChip(parsed.prompt.exitCode, true);
    }
    if (exit) chips.push(exit);

    if (tool === 'shadow_send') {
        if (obj.still_running) chips.push({ label: '⏱ still running', tone: 'warn' });
        else if (obj.timed_out) chips.push({ label: 'timeout', tone: 'warn' });
        if (obj.idle === true && !obj.still_running) status = 'idle';
    } else if (tool === 'shadow_wait') {
        const reason = typeof obj.reason === 'string' ? obj.reason : '';
        command = `wait → ${reason || '?'}`;
        if (reason === 'timeout') chips.push({ label: obj.still_running ? '⏱ timeout · still running' : '⏱ timeout', tone: 'warn' });
        else if (reason === 'pattern') chips.push({ label: obj.matched ? `matched ${String(obj.matched)}` : 'matched', tone: 'info' });
        else if (reason === 'ask') chips.push({ label: 'human asked', tone: 'info' });
        else if (reason && reason !== 'exit') chips.push({ label: reason, tone: 'neutral' });
        if (obj.still_running && reason !== 'timeout') chips.push({ label: '⏱ still running', tone: 'warn' });
        if (obj.tail_truncated) chips.push({ label: 'tail truncated', tone: 'warn' });
    } else {
        command = `read · ${obj.mode || 'tail'}`;
        if (obj.pending_ask) chips.push({ label: 'ask pending', tone: 'info' });
        if (typeof obj.record_count === 'number') chips.push({ label: `${obj.record_count} records`, tone: 'neutral' });
    }
    const n = countLines(parsed.lines);
    const elapsed = typeof obj.elapsed_ms === 'number' ? ` · ${formatElapsed(obj.elapsed_ms)}` : '';
    chips.push({ label: `${tool === 'shadow_wait' ? '+' : ''}${n} lines${elapsed}`, tone: 'neutral' });

    const seqs = parsed.lines.map(l => l.seq).filter((s): s is number => typeof s === 'number');
    const from = cmdSeq ?? (typeof obj.from_seq === 'number' ? obj.from_seq : seqs[0]);
    const to = typeof obj.next_seq === 'number' ? obj.next_seq
        : typeof obj.last_seq === 'number' ? obj.last_seq : seqs[seqs.length - 1];
    return {
        kind: 'remote', tool, command, cwd,
        session: obj.session_id != null ? String(obj.session_id) : undefined,
        sessionLabel: typeof obj.label === 'string' && obj.label !== obj.session_id ? obj.label : undefined,
        chips, lines: parsed.lines, hidden: parsed.hidden,
        seqRange: from !== undefined && to !== undefined ? { from, to } : undefined,
        status, next: typeof obj.next === 'string' ? obj.next : undefined, outputLineCount: n,
    };
}

/**
 * The pane model for a terminal tool's fence, or null when the content is
 * not something the pane can show (an error result, unparseable JSON): the
 * caller then falls back to the generic tool block.
 */
export function buildTerminalPaneModel(
    toolName: string, content: string, headerParts: string[] = [],
): TerminalPaneModel | null {
    try {
        const m = TERMINAL_TOOL_RE.exec(toolName);
        if (!m) return null;
        if (m[1] === 'run_shell_command') return buildLocal(content, toolHeaderFromParts(headerParts));
        const obj = coerceToolResultObject(content);
        if (!obj || obj.ok !== true) return null;
        return buildRemote(m[1] as PaneTool, obj);
    } catch {
        return null;
    }
}
