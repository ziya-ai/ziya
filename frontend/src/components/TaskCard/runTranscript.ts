/**
 * runTranscript — a block's output as one ordered list of prose and tool
 * calls.
 *
 * The live state kept an iteration's text (``streamText``) and its tool
 * calls (``toolCalls``) in two arrays, so a view could only show two
 * lists and the order they happened in was lost.  This module builds the
 * ordered list from the same events, so a run reads the way a chat turn
 * does: a sentence, the call it led to, the next sentence.
 *
 * Events (app/agents/task_executor.py):
 *   task_text_delta / task_text_delta_run  -> text
 *   task_tool_start                        -> a tool call in flight
 *   task_tool_call                         -> that call's result
 *
 * A run recorded before ``task_tool_start`` existed has only the result
 * event; it is appended as a finished call.
 */
import { stripTaskMetaTags } from './completionCheck';

export interface TextSegment {
  kind: 'text';
  text: string;
}

export interface ToolSegment {
  kind: 'tool';
  toolId?: string;
  toolName: string;
  /** What it was called with: a command, a path, a query. */
  header?: string;
  status: 'running' | 'done' | 'failed';
  resultPreview?: string;
  /** Epoch ms, server clock. */
  startedTs?: number;
  endedTs?: number;
  durationMs?: number;
}

export type TranscriptSegment = TextSegment | ToolSegment;

/** Oldest entries are dropped past this, as the flat buffers are. */
export const MAX_SEGMENTS = 400;

type Evt = { [k: string]: unknown };

const str = (v: unknown): string | undefined =>
  (typeof v === 'string' && v ? v : undefined);
/** The wire carries seconds; everything here is epoch ms. */
const tsMs = (e: Evt): number | undefined =>
  (typeof e.ts === 'number' ? e.ts * 1000 : undefined);
const cap = (segs: TranscriptSegment[]): TranscriptSegment[] =>
  (segs.length > MAX_SEGMENTS ? segs.slice(segs.length - MAX_SEGMENTS) : segs);

/**
 * Fold one event into the list.  Returns the SAME array when the event
 * does not concern the transcript, so a caller can skip a state update.
 */
export function appendTranscriptEvent(
  segments: ReadonlyArray<TranscriptSegment>, type: string, e: Evt,
): TranscriptSegment[] {
  const segs = segments as TranscriptSegment[];

  if (type === 'task_text_delta' || type === 'task_text_delta_run') {
    const content = typeof e.content === 'string' ? e.content : '';
    if (!content) return segs;
    const last = segs[segs.length - 1];
    if (last && last.kind === 'text') {
      return [...segs.slice(0, -1), { kind: 'text', text: last.text + content }];
    }
    return cap([...segs, { kind: 'text', text: content }]);
  }

  if (type === 'task_tool_start') {
    const toolId = str(e.tool_id);
    if (toolId && segs.some(s => s.kind === 'tool' && s.toolId === toolId)) {
      return segs;
    }
    return cap([...segs, {
      kind: 'tool', toolId, toolName: str(e.tool_name) ?? 'tool',
      header: str(e.header), status: 'running', startedTs: tsMs(e),
    }]);
  }

  if (type === 'task_tool_call') {
    const toolId = str(e.tool_id);
    const toolName = str(e.tool_name) ?? 'tool';
    // The call this result settles: same id, or, for a result that
    // carries none (a refusal before dispatch), the running call of the
    // same name.
    let at = -1;
    for (let i = segs.length - 1; i >= 0; i--) {
      const s = segs[i];
      if (s.kind !== 'tool' || s.status !== 'running') continue;
      if (toolId ? s.toolId === toolId : s.toolName === toolName) { at = i; break; }
    }
    const prev = at >= 0 ? (segs[at] as ToolSegment) : undefined;
    const endedTs = tsMs(e);
    const durationMs = typeof e.duration_ms === 'number'
      ? e.duration_ms
      : (prev?.startedTs != null && endedTs != null
        ? Math.max(0, Math.round(endedTs - prev.startedTs)) : undefined);
    const done: ToolSegment = {
      kind: 'tool', toolId: toolId ?? prev?.toolId, toolName,
      header: str(e.header) ?? prev?.header,
      status: e.is_error === true ? 'failed' : 'done',
      resultPreview: str(e.result_preview),
      startedTs: prev?.startedTs, endedTs, durationMs,
    };
    if (at >= 0) {
      const out = [...segs];
      out[at] = done;
      return out;
    }
    return cap([...segs, done]);
  }

  return segs;
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

/** "running 4s" while in flight, the duration after, "" when unknown. */
export function formatToolElapsed(seg: ToolSegment, nowMs: number): string {
  if (seg.status === 'running') {
    if (seg.startedTs == null) return 'running';
    // startedTs is the server's clock and nowMs the browser's.
    return `running ${Math.round(Math.max(0, nowMs - seg.startedTs) / 1000)}s`;
  }
  return seg.durationMs != null ? formatMs(seg.durationMs) : '';
}

export interface TailLine {
  kind: 'text' | 'tool';
  text: string;
  status?: ToolSegment['status'];
}

const TAIL_LINE_MAX = 200;

/** The newest ``n`` entries as one line each, oldest first. */
export function transcriptTail(
  segments: ReadonlyArray<TranscriptSegment>, n = 2,
): TailLine[] {
  const out: TailLine[] = [];
  for (let i = segments.length - 1; i >= 0 && out.length < n; i--) {
    const s = segments[i];
    if (s.kind === 'tool') {
      out.unshift({
        kind: 'tool', status: s.status,
        text: (s.header ? `${s.toolName} ${s.header}` : s.toolName).slice(0, TAIL_LINE_MAX),
      });
      continue;
    }
    const lines = stripTaskMetaTags(s.text).split('\n')
      .map(l => l.trim()).filter(Boolean);
    const line = lines[lines.length - 1];
    if (line) out.unshift({ kind: 'text', text: line.slice(0, TAIL_LINE_MAX) });
  }
  return out;
}

interface TailBucket {
  index: number;
  blockId?: string;
  status: string;
  segments?: ReadonlyArray<TranscriptSegment>;
}

/** The tail of a running iteration that has output; null when none has. */
export function liveTail(
  iterations: ReadonlyArray<TailBucket>, n = 2,
): { blockId: string; index: number; lines: TailLine[] } | null {
  for (let i = iterations.length - 1; i >= 0; i--) {
    const it = iterations[i];
    if (it.status !== 'running' || !it.blockId || !it.segments?.length) continue;
    const lines = transcriptTail(it.segments, n);
    if (lines.length) return { blockId: it.blockId, index: it.index, lines };
  }
  return null;
}
