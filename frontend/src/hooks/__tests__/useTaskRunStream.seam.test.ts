/**
 * Text-run seam at tool-call boundaries — ``accumulateLive``.
 *
 * The backend emits ``task_text_delta`` only for ``ctype === "text"``
 * chunks; a tool call switches the stream to ``tool_display``
 * (app/agents/task_executor.py), which emits ``task_tool_call`` and NO
 * text.  The reducer concatenates raw deltas, so without a seam the
 * sentence before a tool call is welded onto the sentence after it:
 *
 *     "...to place them below.The root cause is clear from VexFlow"
 *
 * The load-bearing diagnostic: a following ``## Summary`` renders as
 * literal text rather than an <h2>, because a markdown heading only
 * parses at line start.  That rules out every downstream suspect —
 * marked's ``breaks`` option, sanitizers, the streaming optimizer — all
 * of which preserve newlines that exist.  There was no newline to lose;
 * the character was never present.
 *
 * Note the asymmetry with the missing-space symptom: with
 * ``breaks: false`` a real ``\n`` survives as a SPACE ("one. two."), so
 * "one.two" with no space is proof of absence rather than of collapsing.
 *
 * The backend fix repairs ``full_text`` / the persisted artifact summary.
 * These tests cover the live surface, which is accumulated here from raw
 * deltas and needs its own break.
 */

import { accumulateLive, type LiveTaskState } from '../useTaskRunStream';

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

/** Drive the reducer synchronously and return the final state. */
function reduce(events: ReadonlyArray<unknown>): LiveTaskState {
  let state = EMPTY;
  const setLive: any = (updater: any) => {
    state = typeof updater === 'function' ? updater(state) : updater;
  };
  for (const e of events) accumulateLive(setLive, e);
  return state;
}

const delta = (content: string, block_id = 'b1') =>
  ({ type: 'task_text_delta', block_id, content });
const toolCall = (block_id = 'b1', tool_name = 'file_read') =>
  ({ type: 'task_tool_call', block_id, tool_name });

describe('accumulateLive — tool-boundary seam (flat text)', () => {
  it('does not weld prose across a tool call', () => {
    const out = reduce([
      delta('Let me inspect the placement API.'),
      toolCall(),
      delta('The root cause is clear.'),
    ]);
    expect(out.text.b1).not.toContain('API.The');
    expect(out.text.b1).toBe(
      'Let me inspect the placement API.\n\nThe root cause is clear.',
    );
  });

  it('leaves a heading after a tool call at line start', () => {
    // The reported symptom: "## Summary" rendered as literal text.
    const out = reduce([
      delta('The setLine chain is valid.'),
      toolCall(),
      delta('## Summary\n\nDefect taken.'),
    ]);
    expect(out.text.b1).not.toContain('valid.## Summary');
    expect(out.text.b1.split('\n')).toContain('## Summary');
  });

  it('does not double a newline the model already emitted', () => {
    const out = reduce([
      delta('Checking the config.\n'),
      toolCall(),
      delta('Found the issue.'),
    ]);
    expect(out.text.b1).toBe('Checking the config.\nFound the issue.');
    expect(out.text.b1).not.toContain('\n\n\n');
  });

  it('leaves an existing blank line alone', () => {
    const out = reduce([
      delta('Reading the file.\n\n'),
      toolCall(),
      delta('Done.'),
    ]);
    expect(out.text.b1).toBe('Reading the file.\n\nDone.');
    expect(out.text.b1).not.toContain('\n\n\n');
  });

  it('adds no leading whitespace when a tool call comes first', () => {
    const out = reduce([
      toolCall(),
      delta('Starting analysis.'),
    ]);
    expect(out.text.b1).toBe('Starting analysis.');
    expect(out.text.b1.startsWith('\n')).toBe(false);
  });

  it('adds only one break for consecutive tool calls', () => {
    const out = reduce([
      delta('Checking both files.'),
      toolCall(), toolCall(), toolCall(),
      delta('Both look correct.'),
    ]);
    expect(out.text.b1).toBe('Checking both files.\n\nBoth look correct.');
    expect(out.text.b1).not.toContain('\n\n\n');
  });

  it('bridges every seam across a long alternating run', () => {
    const events: unknown[] = [];
    for (let i = 0; i < 5; i++) {
      events.push(delta(`Step ${i} prose.`));
      events.push(toolCall());
    }
    events.push(delta('Final answer.'));
    const out = reduce(events);
    expect(out.text.b1).not.toContain('prose.Step');
    expect(out.text.b1).not.toContain('prose.Final');
    expect(out.text.b1).not.toContain('\n\n\n');
    expect(out.text.b1.split('\n\n')).toHaveLength(6);
  });

  it('keeps newlines inside a contiguous text run untouched', () => {
    const out = reduce([delta('Line one.\nLine two.\nLine three.')]);
    expect(out.text.b1).toBe('Line one.\nLine two.\nLine three.');
  });

  it('is unaffected by how providers fragment the deltas', () => {
    const whole = reduce([
      delta('Reading the config file now.'),
      toolCall(),
      delta('It parses cleanly.'),
    ]);
    const fragmented = reduce([
      delta('Reading the '), delta('config file '), delta('now.'),
      toolCall(),
      delta('It parses '), delta('cleanly.'),
    ]);
    expect(fragmented.text.b1).toBe(whole.text.b1);
  });

  it('keys the seam on the text END, not on any newline in the run', () => {
    const out = reduce([
      delta('Intro line.\nSecond line.'),
      toolCall(),
      delta('After the call.'),
    ]);
    expect(out.text.b1).toBe('Intro line.\nSecond line.\n\nAfter the call.');
    expect(out.text.b1).not.toContain('line.After');
  });

  it('treats a newline-only delta as a satisfied seam', () => {
    const out = reduce([
      delta('Prose.'), delta('\n'),
      toolCall(),
      delta('More.'),
    ]);
    expect(out.text.b1).toBe('Prose.\nMore.');
    expect(out.text.b1).not.toContain('\n\n');
  });

  it('seams each block independently', () => {
    const out = reduce([
      delta('Block one prose.', 'b1'),
      delta('Block two prose.', 'b2'),
      toolCall('b1'),
      delta('B1 after.', 'b1'),
      delta('B2 continues.', 'b2'),
    ]);
    // Only b1 crossed a tool boundary.
    expect(out.text.b1).toBe('Block one prose.\n\nB1 after.');
    expect(out.text.b2).toBe('Block two prose.B2 continues.');
  });

  it('does not create an entry for a block with no prior text', () => {
    const out = reduce([toolCall('b9')]);
    expect(out.text.b9).toBeUndefined();
  });

  it('ignores a tool call with no block_id', () => {
    const out = reduce([
      delta('Some prose.'),
      { type: 'task_tool_call', tool_name: 'file_read' },
      delta('More prose.'),
    ]);
    // No block_id to attribute the seam to; text is unchanged by it.
    expect(out.text.b1).toBe('Some prose.More prose.');
  });

  it('still records the tool call itself', () => {
    // The seam must not displace the toolCalls bookkeeping.
    const out = reduce([
      delta('Prose.'),
      toolCall('b1', 'run_shell_command'),
    ]);
    expect(out.toolCalls).toHaveLength(1);
    expect(out.toolCalls[0].tool_name).toBe('run_shell_command');
  });
});

describe('accumulateLive — tool-boundary seam (per-iteration buckets)', () => {
  it('seams streamText in the iteration bucket', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      delta('Inspecting the API.'),
      toolCall(),
      delta('Found the default.'),
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0].streamText).toBe(
      'Inspecting the API.\n\nFound the default.',
    );
    expect(out.iterations[0].streamText).not.toContain('API.Found');
  });

  it('does not double an existing newline in a bucket', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      delta('Reading.\n'),
      toolCall(),
      delta('Done.'),
    ]);
    expect(out.iterations[0].streamText).toBe('Reading.\nDone.');
    expect(out.iterations[0].streamText).not.toContain('\n\n\n');
  });

  it('adds no leading break when a bucket opens on a tool call', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      toolCall(),
      delta('First prose.'),
    ]);
    expect(out.iterations[0].streamText).toBe('First prose.');
    expect(out.iterations[0].streamText.startsWith('\n')).toBe(false);
  });

  it('seams independently per iteration of a repeat block', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      delta('Iter zero prose.'),
      toolCall(),
      delta('Iter zero after.'),
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'b1', index: 1 },
      delta('Iter one prose.'),
      toolCall(),
      delta('Iter one after.'),
      { type: 'iteration_completed', block_id: 'b1', index: 1, status: 'passed' },
    ]);
    expect(out.iterations).toHaveLength(2);
    expect(out.iterations[0].streamText)
      .toBe('Iter zero prose.\n\nIter zero after.');
    expect(out.iterations[1].streamText)
      .toBe('Iter one prose.\n\nIter one after.');
  });

  it('keeps the bucket seam consistent with the flat text seam', () => {
    // Both surfaces accumulate the same deltas; a divergence would mean
    // the Live tab and the focused-block panel disagree.
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      delta('Alpha.'),
      toolCall(),
      delta('Beta.'),
      toolCall(),
      delta('Gamma.'),
    ]);
    expect(out.iterations[0].streamText).toBe(out.text.b1);
  });

  it('records tool calls in the bucket alongside the seam', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      delta('Prose.'),
      toolCall('b1', 'file_read'),
    ]);
    expect(out.iterations[0].toolCalls).toHaveLength(1);
    expect(out.iterations[0].toolCalls[0].tool_name).toBe('file_read');
    expect(out.iterations[0].streamText).toBe('Prose.\n\n');
  });
});
