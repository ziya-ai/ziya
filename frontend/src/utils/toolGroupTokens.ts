/**
 * Collect the members of a parallel tool batch into one token.
 *
 * chatApi brackets a group with TOOL_GROUP_START / TOOL_GROUP_END comments
 * (utils/toolGroupDisplay.ts).  marked lexes each as an html token of its
 * own; groupToolTokens replaces the run between them with a synthetic
 * 'tool_group' token that renderTokens draws as a <ToolGroup> container.
 *
 * A group with no END is still running (or was stopped): it takes what
 * follows until `size` members have arrived, then closes there.  Without
 * that cap a lost END (the relay once dropped tool_group_end) drew the rest
 * of the message inside the group.  A new START before an END closes the
 * previous group unfinished.  An END outside any group is dropped.
 */
export interface ToolGroupEndInfo {
    group_id: string;
    size: number;
    wall_ms: number;
    serial_ms: number;
}

export interface ToolGroupToken {
    type: 'tool_group';
    /** Stable while members grow, so the React key does not change mid-stream. */
    text: string;
    raw: string;
    groupId: string;
    size: number;
    end: ToolGroupEndInfo | null;
    tokens: any[];
}

const START_RE = /^\s*<!--\s*TOOL_GROUP_START:([\w-]+)\|(\d+)\s*-->\s*$/;
const END_RE = /^\s*<!--\s*TOOL_GROUP_END:([\w-]+)\|(\d+)\|(\d+)\|(\d+)\s*-->\s*$/;

const htmlText = (t: any): string | null =>
    t && t.type === 'html' && typeof t.text === 'string' ? t.text : null;

export function parseGroupStart(t: any): { id: string; size: number } | null {
    const m = START_RE.exec(htmlText(t) ?? '');
    return m ? { id: m[1], size: Number(m[2]) } : null;
}

export function parseGroupEnd(t: any): ToolGroupEndInfo | null {
    const m = END_RE.exec(htmlText(t) ?? '');
    return m ? { group_id: m[1], size: Number(m[2]), wall_ms: Number(m[3]), serial_ms: Number(m[4]) } : null;
}

/** Same array when there is no START marker. */
export function groupToolTokens<T>(tokens: T[]): Array<T | ToolGroupToken> {
    if (!tokens.some(t => parseGroupStart(t))) return tokens;
    const out: Array<T | ToolGroupToken> = [];
    let i = 0;
    while (i < tokens.length) {
        const start = parseGroupStart(tokens[i]);
        if (!start) {
            if (!parseGroupEnd(tokens[i])) out.push(tokens[i]);
            i++;
            continue;
        }
        const inner: T[] = [];
        let end: ToolGroupEndInfo | null = null;
        let j = i + 1;
        let members = 0;
        for (; j < tokens.length; j++) {
            if (parseGroupStart(tokens[j])) break;
            const e = parseGroupEnd(tokens[j]);
            if (e && e.group_id === start.id) { end = e; j++; break; }
            if (e) continue;
            if (members >= start.size) {
                // Complete: look past spacing for this group's END, but stop
                // at the first real token after the last member.
                if ((tokens[j] as any)?.type === 'space') continue;
                break;
            }
            inner.push(tokens[j]);
            if (isMember(tokens[j])) members++;
        }
        const key = 'tool_group:' + start.id;
        out.push({
            type: 'tool_group', text: key, raw: key,
            groupId: start.id, size: start.size, end, tokens: inner,
        });
        i = j;
    }
    return out;
}

const isMember = (t: any): boolean =>
    (t?.type === 'code' && typeof t.lang === 'string' && t.lang.startsWith('tool:'))
    || (t?.type === 'html' && typeof t.text === 'string' && t.text.includes('TOOL_BLOCK_START:'));

/**
 * How many members have arrived and how many have a result.  A member still
 * shows chatApi's "⏳ Running..." body until its result replaces it.
 */
export function groupMemberProgress(tokens: any[], size: number): { seen: number; done: number } {
    const members = tokens.filter(isMember);
    const running = members.filter(t => /⏳ Running/.test(t.text || '')).length;
    return {
        seen: members.length,
        done: Math.min(size, Math.max(0, members.length - running)),
    };
}
