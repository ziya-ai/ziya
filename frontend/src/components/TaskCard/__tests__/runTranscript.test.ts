/**
 * runTranscript: one ordered list of prose and tool calls per iteration.
 *
 * The run views kept a block's text and its tool calls in two separate
 * arrays, so they could only be shown as two lists (the tile's Live and
 * Tools tabs; the dialog's tools-then-text).  The order they happened in
 * was not recoverable.  These tests pin the ordered list, and the seam:
 * accumulateLive builds it from the same events as the existing fields.
 */
import {
  appendTranscriptEvent, formatToolElapsed, liveTail, transcriptTail,
  MAX_SEGMENTS, type ToolSegment, type TranscriptSegment,
} from '../runTranscript';
import { accumulateLive, type LiveTaskState } from '../../../hooks/useTaskRunStream';

type Evt = { type: string; [k: string]: unknown };

const feed = (events: Evt[], start: TranscriptSegment[] = []) =>
  events.reduce<TranscriptSegment[]>(
    (segs, e) => appendTranscriptEvent(segs, e.type, e), start);

const kinds = (segs: TranscriptSegment[]) =>
  segs.map(s => (s.kind === 'text' ? `text:${s.text}` : `tool:${s.toolName}:${s.status}`));

describe('appendTranscriptEvent', () => {
  it('keeps prose and tool calls in the order they happened', () => {
    const segs = feed([
      { type: 'task_text_delta', content: 'before' },
      { type: 'task_tool_start', tool_id: 't1', tool_name: 'rg', header: '-n x', ts: 10 },
      { type: 'task_tool_call', tool_id: 't1', tool_name: 'rg', result_preview: 'hit', ts: 10.5 },
      { type: 'task_text_delta', content: 'after' },
    ]);
    expect(kinds(segs)).toEqual(['text:before', 'tool:rg:done', 'text:after']);
  });

  it('merges adjacent deltas, including the relay replay shape', () => {
    const segs = feed([
      { type: 'task_text_delta', content: 'a' },
      { type: 'task_text_delta', content: 'b' },
      { type: 'task_text_delta_run', content: 'cd' },
    ]);
    expect(kinds(segs)).toEqual(['text:abcd']);
  });

  it('a start shows a running call; its result settles the same entry', () => {
    const started = feed([
      { type: 'task_tool_start', tool_id: 't1', tool_name: 'shell', header: '$ ls', ts: 100 },
    ]);
    expect(kinds(started)).toEqual(['tool:shell:running']);
    const done = feed([
      { type: 'task_tool_call', tool_id: 't1', tool_name: 'shell', result_preview: 'a\nb', ts: 102 },
    ], started);
    expect(done).toHaveLength(1);
    const tool = done[0] as ToolSegment;
    expect(tool.status).toBe('done');
    expect(tool.header).toBe('$ ls');           // kept from the start event
    expect(tool.resultPreview).toBe('a\nb');
    expect(tool.durationMs).toBe(2000);         // from the two server timestamps
  });

  it('prefers the server duration when the result carries one', () => {
    const segs = feed([
      { type: 'task_tool_start', tool_id: 't1', tool_name: 'shell', ts: 100 },
      { type: 'task_tool_call', tool_id: 't1', tool_name: 'shell', duration_ms: 40, ts: 109 },
    ]);
    expect((segs[0] as ToolSegment).durationMs).toBe(40);
  });

  it('a result with no start (older server) is appended as finished', () => {
    const segs = feed([
      { type: 'task_text_delta', content: 'x' },
      { type: 'task_tool_call', tool_name: 'file_read', header: 'a.py' },
    ]);
    expect(kinds(segs)).toEqual(['text:x', 'tool:file_read:done']);
    expect((segs[1] as ToolSegment).header).toBe('a.py');
  });

  it('a result without an id settles the running call of the same name', () => {
    const segs = feed([
      { type: 'task_tool_start', tool_id: 't1', tool_name: 'shell' },
      { type: 'task_tool_call', tool_name: 'shell', result_preview: 'refused', is_error: true },
    ]);
    expect(kinds(segs)).toEqual(['tool:shell:failed']);
  });

  it('ignores a repeated start and unrelated events without copying', () => {
    const one = feed([{ type: 'task_tool_start', tool_id: 't1', tool_name: 'rg' }]);
    expect(appendTranscriptEvent(one, 'task_tool_start', { tool_id: 't1', tool_name: 'rg' })).toBe(one);
    expect(appendTranscriptEvent(one, 'task_progress', { note: 'n' })).toBe(one);
    expect(appendTranscriptEvent(one, 'task_text_delta', { content: '' })).toBe(one);
  });

  it('is bounded', () => {
    const events: Evt[] = [];
    for (let i = 0; i < MAX_SEGMENTS + 50; i++) {
      events.push({ type: 'task_tool_call', tool_id: `t${i}`, tool_name: 'rg' });
    }
    const segs = feed(events);
    expect(segs).toHaveLength(MAX_SEGMENTS);
    expect((segs[segs.length - 1] as ToolSegment).toolId).toBe(`t${MAX_SEGMENTS + 49}`);
  });
});

describe('formatToolElapsed', () => {
  it('counts up while running and reports the duration after', () => {
    const running: ToolSegment = { kind: 'tool', toolName: 'x', status: 'running', startedTs: 1000 };
    expect(formatToolElapsed(running, 5000)).toBe('running 4s');
    expect(formatToolElapsed({ ...running, startedTs: undefined }, 5000)).toBe('running');
    // A client clock behind the server's must not show a negative age.
    expect(formatToolElapsed(running, 500)).toBe('running 0s');
    const done: ToolSegment = { kind: 'tool', toolName: 'x', status: 'done', durationMs: 200 };
    expect(formatToolElapsed(done, 0)).toBe('0.2s');
    expect(formatToolElapsed({ ...done, durationMs: 75_000 }, 0)).toBe('1m 15s');
    expect(formatToolElapsed({ ...done, durationMs: undefined }, 0)).toBe('');
  });
});

describe('transcriptTail / liveTail', () => {
  const segs = feed([
    { type: 'task_text_delta', content: 'first line\nsecond line\n' },
    { type: 'task_tool_call', tool_id: 'a', tool_name: 'rg', header: '-n x' },
    { type: 'task_text_delta', content: 'The enhancer has no collision pass.' },
    { type: 'task_tool_start', tool_id: 'b', tool_name: 'render_diagram', header: 'type=mermaid' },
  ]);

  it('returns the newest entries, oldest first', () => {
    expect(transcriptTail(segs, 2)).toEqual([
      { kind: 'text', text: 'The enhancer has no collision pass.' },
      { kind: 'tool', text: 'render_diagram type=mermaid', status: 'running' },
    ]);
  });

  it('uses the last non-empty line of a text entry', () => {
    expect(transcriptTail(segs.slice(0, 1), 2)).toEqual([
      { kind: 'text', text: 'second line' },
    ]);
  });

  it('picks a running iteration that has output', () => {
    const iterations = [
      { index: 0, blockId: 'done-block', status: 'passed', segments: segs },
      { index: 2, blockId: 'loop', status: 'running', segments: segs },
      { index: 3, blockId: 'loop', status: 'running', segments: [] },
    ];
    const tail = liveTail(iterations, 2);
    expect(tail?.blockId).toBe('loop');
    expect(tail?.index).toBe(2);
    expect(tail?.lines).toHaveLength(2);
    expect(liveTail([iterations[0]], 2)).toBeNull();
  });
});

describe('accumulateLive builds the ordered list', () => {
  const EMPTY: LiveTaskState = {
    text: {}, toolCalls: [], events: [], iterations: [], variables: {}, blockStatuses: {},
  };
  const run = (events: Evt[]): LiveTaskState => {
    let state = EMPTY;
    const setLive = ((u: unknown) => {
      state = typeof u === 'function' ? (u as (p: LiveTaskState) => LiveTaskState)(state) : (u as LiveTaskState);
    }) as React.Dispatch<React.SetStateAction<LiveTaskState>>;
    for (const e of events) accumulateLive(setLive, e);
    return state;
  };

  it('per iteration, in arrival order, alongside the existing fields', () => {
    const live = run([
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'A' },
      { type: 'task_tool_start', block_id: 'loop', index: 0, tool_id: 't', tool_name: 'rg', ts: 1 },
      { type: 'task_tool_call', block_id: 'loop', index: 0, tool_id: 't', tool_name: 'rg', ts: 2 },
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'B' },
    ]);
    expect(live.iterations).toHaveLength(1);
    const it0 = live.iterations[0];
    expect(kinds(it0.segments ?? [])).toEqual(['text:A', 'tool:rg:done', 'text:B']);
    // The fields older views read are unchanged.
    expect(it0.streamText).toBe('A\n\nB');
    expect(it0.toolCalls).toHaveLength(1);
  });

  it('a tool start alone opens the bucket of a bare task', () => {
    const live = run([
      { type: 'task_tool_start', block_id: 'solo', tool_id: 't', tool_name: 'file_read' },
    ]);
    expect(live.iterations).toHaveLength(1);
    expect(live.iterations[0].blockId).toBe('solo');
    expect(kinds(live.iterations[0].segments ?? [])).toEqual(['tool:file_read:running']);
  });

  it('parallel iterations of one loop keep separate lists', () => {
    const live = run([
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'zero' },
      { type: 'task_text_delta', block_id: 'loop', index: 1, content: 'one' },
    ]);
    expect(kinds(live.iterations[0].segments ?? [])).toEqual(['text:zero']);
    expect(kinds(live.iterations[1].segments ?? [])).toEqual(['text:one']);
  });
});
