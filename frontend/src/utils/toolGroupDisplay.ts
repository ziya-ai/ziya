/**
 * Group chrome for parallel tool batches.
 *
 * The batch runner (app/tool_batch.py) runs the calls of one assistant
 * message in groups.  For a group of two or more it tags each member's
 * tool_start with `batch_group = {id, size, index}` and, after the (issue-
 * ordered) results, sends one `tool_group_end` carrying wall vs. serial time.
 *
 * The chat brackets the members with two HTML comments: a START marker
 * written before the first member to arrive, and an END marker, carrying the
 * timing, appended when the group ends.  renderTokens collects what lies
 * between into one <ToolGroup> container (utils/toolGroupTokens.ts).  Each
 * member's header also gets the ⫽ mark.  A lone call never reaches any of
 * this and renders exactly as before.
 *
 * Messages recorded before the container carry a legacy
 * `<!-- TOOL_GROUP:id -->` comment and a `tool:mcp_parallel` fence.  The
 * renderer hides the comment; the legacy helpers below are kept for them.
 */

export const GROUP_TOOL_NAME = 'mcp_parallel';
export const MEMBER_PREFIX = '⫽ ';

export interface BatchGroupTag {
    id: string;
    size: number;
    /** Issue order within the group; 0 opens the group header. */
    index: number;
}

export interface ToolGroupEnd {
    type: 'tool_group_end';
    group_id: string;
    size: number;
    wall_ms: number;
    serial_ms: number;
    tool_ids?: string[];
}

const FENCE = '````';

export function isGroupLeader(tag?: BatchGroupTag | null): boolean {
    return !!tag && tag.size >= 2 && tag.index === 0;
}

/**
 * True for the first event of a >= 2 group seen in this stream, which opens
 * its container; records the id in `opened`.  Members run concurrently, so
 * the first to arrive is not necessarily index 0.
 */
export function opensGroup(tag: BatchGroupTag | null | undefined, opened: Set<string>): boolean {
    if (!tag || tag.size < 2 || opened.has(tag.id)) return false;
    opened.add(tag.id);
    return true;
}

/** Mark a member's header; a lone call's header is returned verbatim. */
export function memberHeader(header: string, tag?: BatchGroupTag | null): string {
    if (!tag || tag.size < 2) return header;
    return header.startsWith(MEMBER_PREFIX) ? header : MEMBER_PREFIX + header;
}

/** Legacy placeholder anchor (pre-container messages); see spliceGroupEnd. */
export function groupMarker(groupId: string): string {
    return `<!-- TOOL_GROUP:${groupId} -->`;
}

// Ids come from the server (uuid hex); strip anything that could end the comment.
const safeId = (id: string): string => String(id).replace(/[^\w-]/g, '');

/** Opens a group's container; renderTokens collects what follows. */
export function groupStartMarker(tag: BatchGroupTag): string {
    return `<!-- TOOL_GROUP_START:${safeId(tag.id)}|${tag.size} -->`;
}

/** Closes it and carries the timing the container's header shows. */
export function groupEndMarker(end: ToolGroupEnd): string {
    return `<!-- TOOL_GROUP_END:${safeId(end.group_id)}|${end.size}|${Math.round(end.wall_ms)}|${Math.round(end.serial_ms)} -->`;
}

/** Blank lines around the comment make it an HTML block token of its own. */
export function groupStartDisplay(tag: BatchGroupTag): string {
    return `\n\n${groupStartMarker(tag)}\n\n`;
}

/**
 * Close the container.  Appended at the end: the runner sends tool_group_end
 * after every member's result, so each member block already sits above it.
 */
export function appendGroupEnd(content: string, end: ToolGroupEnd): string {
    return `${content}\n\n${groupEndMarker(end)}\n\n`;
}

const fmtS = (ms: number): string => `${(ms / 1000).toFixed(1)} s`;

/** Timing half of the verdict.  A saving under 100 ms is reported as none
 *  rather than as a rounding artefact. */
export function groupTiming(end: Pick<ToolGroupEnd, 'wall_ms' | 'serial_ms'>): string {
    const saved = end.serial_ms - end.wall_ms;
    const wall = `${fmtS(end.wall_ms)} wall`;
    if (saved < 100 || end.wall_ms <= 0) {
        return `${wall} · no saving vs serial (${fmtS(end.serial_ms)})`;
    }
    const ratio = (end.serial_ms / end.wall_ms).toFixed(1);
    return `${wall} · saved ${fmtS(saved)} vs serial (${fmtS(end.serial_ms)}, ${ratio}×)`;
}

/** One-line verdict. */
export function groupSummary(end: ToolGroupEnd): string {
    return `✓ ${end.size} calls in parallel · ${groupTiming(end)}`;
}

export function groupEndDisplay(end: ToolGroupEnd): string {
    return `\n\n${FENCE}tool:${GROUP_TOOL_NAME}|${groupSummary(end)}|text\n`
        + `${end.size} calls overlapped; wall ${fmtS(end.wall_ms)}, serial ${fmtS(end.serial_ms)}.\n${FENCE}\n\n`;
}

/**
 * Replace the group placeholder (marker through its closing fence) with the
 * summary block.  Member fences after it are untouched.  If the marker is
 * missing the summary is appended rather than dropped.
 */
export function spliceGroupEnd(content: string, end: ToolGroupEnd): string {
    const marker = groupMarker(end.group_id);
    const idx = content.indexOf(marker);
    if (idx === -1) return content + groupEndDisplay(end);
    const after = content.substring(idx);
    const close = after.match(/\n````\n/);
    if (!close || close.index === undefined) return content + groupEndDisplay(end);
    let endIdx = idx + close.index + close[0].length;
    while (endIdx < content.length && content[endIdx] === '\n') endIdx++;
    return content.substring(0, idx) + groupEndDisplay(end).replace(/^\n+/, '') + content.substring(endIdx);
}
