/**
 * Unit tests for ``eventLog.ts`` — the pure helpers backing the
 * TaskRunInspector Events tab.  No React/DOM/WebSocket here; just
 * data transformations.
 *
 * Covers (per d3 post-mortem):
 *  - delta collapsing folds adjacent same-block deltas
 *  - run boundaries respect block_id changes and intervening events
 *  - char/count totals are accurate across a run
 *  - input order is preserved (not reversed)
 *  - pagination clamps out-of-range pages and produces correct slices
 */
import { collapseEventRuns, pageEvents } from '../eventLog';
import type { RawEvent, DeltaRun, DisplayEvent } from '../eventLog';

const delta = (
  blockId: string, content: string, ts?: number,
): RawEvent => ({
  type: 'task_text_delta',
  block_id: blockId,
  content,
  ts,
});

const tool = (
  blockId: string, name: string, ts?: number,
): RawEvent => ({
  type: 'task_tool_call',
  block_id: blockId,
  tool_name: name,
  ts,
});

describe('collapseEventRuns', () => {
  it('returns empty array for empty input', () => {
    expect(collapseEventRuns([])).toEqual([]);
  });

  it('folds adjacent same-block deltas into one run', () => {
    const result = collapseEventRuns([
      delta('b1', 'hello', 1),
      delta('b1', ' ', 2),
      delta('b1', 'world', 3),
    ]);
    expect(result).toHaveLength(1);
    const run = result[0] as DeltaRun;
    expect(run.type).toBe('task_text_delta_run');
    expect(run.block_id).toBe('b1');
    expect(run.count).toBe(3);
    expect(run.totalChars).toBe(11); // "hello" + " " + "world"
    expect(run.ts).toBe(1);
    expect(run.endTs).toBe(3);
    expect(run.rawEvents).toHaveLength(3);
  });

  it('starts a new run when block_id changes', () => {
    const result = collapseEventRuns([
      delta('b1', 'a'),
      delta('b1', 'b'),
      delta('b2', 'c'),
      delta('b2', 'd'),
    ]);
    expect(result).toHaveLength(2);
    expect((result[0] as DeltaRun).block_id).toBe('b1');
    expect((result[0] as DeltaRun).count).toBe(2);
    expect((result[1] as DeltaRun).block_id).toBe('b2');
    expect((result[1] as DeltaRun).count).toBe(2);
  });

  it('starts a new run when a non-delta event interrupts', () => {
    const result = collapseEventRuns([
      delta('b1', 'a'),
      delta('b1', 'b'),
      tool('b1', 'shell'),
      delta('b1', 'c'),
      delta('b1', 'd'),
    ]);
    expect(result).toHaveLength(3);
    expect((result[0] as DeltaRun).count).toBe(2);
    expect(result[1].type).toBe('task_tool_call');
    expect((result[2] as DeltaRun).count).toBe(2);
  });

  it('passes non-delta events through unchanged', () => {
    const events: RawEvent[] = [
      { type: 'run_started', ts: 1 },
      tool('b1', 'shell', 2),
      { type: 'block_completed', block_id: 'b1', ts: 3 },
    ];
    const result = collapseEventRuns(events);
    expect(result).toEqual(events);
  });

  it('preserves chronological order (does not reverse)', () => {
    const events: RawEvent[] = [
      { type: 'run_started', ts: 1 },
      delta('b1', 'first', 2),
      tool('b1', 'shell', 3),
      delta('b1', 'second', 4),
      { type: 'run_completed', ts: 5 },
    ];
    const result = collapseEventRuns(events);
    expect(result.map(e => e.ts)).toEqual([1, 2, 3, 4, 5]);
  });

  it('handles single delta with no others', () => {
    const result = collapseEventRuns([delta('b1', 'solo', 7)]);
    expect(result).toHaveLength(1);
    const run = result[0] as DeltaRun;
    expect(run.count).toBe(1);
    expect(run.totalChars).toBe(4);
    expect(run.ts).toBe(7);
    expect(run.endTs).toBe(7);
  });

  it('treats deltas with missing block_id as separate from those with one', () => {
    // Empty string block_id and undefined are both treated as no-id.
    // Two empty-id deltas should still fold (same "block" of "").
    const result = collapseEventRuns([
      { type: 'task_text_delta', content: 'a' } as RawEvent,
      { type: 'task_text_delta', content: 'b' } as RawEvent,
    ]);
    expect(result).toHaveLength(1);
    expect((result[0] as DeltaRun).count).toBe(2);
    expect((result[0] as DeltaRun).block_id).toBe('');
  });

  it('handles delta with missing/non-string content (treats length as 0)', () => {
    const result = collapseEventRuns([
      { type: 'task_text_delta', block_id: 'b1' } as RawEvent,
      { type: 'task_text_delta', block_id: 'b1', content: 42 } as RawEvent,
      delta('b1', 'real'),
    ]);
    expect(result).toHaveLength(1);
    const run = result[0] as DeltaRun;
    expect(run.count).toBe(3);
    expect(run.totalChars).toBe(4); // only "real" contributes
  });

  it('reproduces the d3 post-mortem scenario at scale', () => {
    // Simulate 50 deltas for a single sentence, the way the d3 events
    // log looked.  Should collapse to one run.
    const events: RawEvent[] = [];
    for (let i = 0; i < 50; i++) {
      events.push(delta('b-15e03964', `word${i} `, 1000 + i));
    }
    const result = collapseEventRuns(events);
    expect(result).toHaveLength(1);
    const run = result[0] as DeltaRun;
    expect(run.count).toBe(50);
    expect(run.ts).toBe(1000);
    expect(run.endTs).toBe(1049);
  });
});

describe('pageEvents', () => {
  const items = Array.from({ length: 25 }, (_, i) => `item${i}`);

  it('returns first page by default for page=0', () => {
    const w = pageEvents(items, 0, 10);
    expect(w.items).toEqual(items.slice(0, 10));
    expect(w.page).toBe(0);
    expect(w.pageCount).toBe(3);
    expect(w.total).toBe(25);
  });

  it('returns middle page correctly', () => {
    const w = pageEvents(items, 1, 10);
    expect(w.items).toEqual(items.slice(10, 20));
    expect(w.page).toBe(1);
  });

  it('returns last (partial) page correctly', () => {
    const w = pageEvents(items, 2, 10);
    expect(w.items).toEqual(items.slice(20, 25));
    expect(w.items).toHaveLength(5);
    expect(w.page).toBe(2);
  });

  it('clamps page above pageCount-1 to last page', () => {
    const w = pageEvents(items, 99, 10);
    expect(w.page).toBe(2);
    expect(w.items).toEqual(items.slice(20, 25));
  });

  it('clamps negative page to 0', () => {
    const w = pageEvents(items, -5, 10);
    expect(w.page).toBe(0);
    expect(w.items).toEqual(items.slice(0, 10));
  });

  it('handles empty input', () => {
    const w = pageEvents([], 0, 10);
    expect(w.items).toEqual([]);
    expect(w.page).toBe(0);
    expect(w.pageCount).toBe(1);
    expect(w.total).toBe(0);
  });

  it('clamps non-integer page to floor', () => {
    const w = pageEvents(items, 1.7, 10);
    expect(w.page).toBe(1);
    expect(w.items).toEqual(items.slice(10, 20));
  });

  it('treats pageSize<1 as 1', () => {
    const w = pageEvents(items.slice(0, 3), 0, 0);
    expect(w.pageCount).toBe(3);
    expect(w.items).toEqual(['item0']);
  });

  it('handles exact-fit boundary (count == pageSize)', () => {
    const ten = items.slice(0, 10);
    const w = pageEvents(ten, 0, 10);
    expect(w.pageCount).toBe(1);
    expect(w.items).toEqual(ten);
  });

  it('handles single-item case', () => {
    const w = pageEvents(['only'], 0, 10);
    expect(w.items).toEqual(['only']);
    expect(w.pageCount).toBe(1);
    expect(w.total).toBe(1);
  });
});

describe('integration — collapse then paginate', () => {
  it('paginates over collapsed runs, not raw deltas', () => {
    // 100 deltas in 5 alternating blocks → should collapse to 5 runs
    // (one per block transition, since they alternate).  Wait — that's
    // wrong; alternating means each delta is its own run.  Let me build
    // 5 runs of 20 deltas each, same block within each run.
    const events: RawEvent[] = [];
    for (let block = 0; block < 5; block++) {
      for (let i = 0; i < 20; i++) {
        events.push(delta(`b${block}`, `chunk${i}`));
      }
    }
    const collapsed = collapseEventRuns(events);
    expect(collapsed).toHaveLength(5);

    const page = pageEvents(collapsed, 0, 3);
    expect(page.items).toHaveLength(3);
    expect(page.pageCount).toBe(2);
    expect((page.items[0] as DeltaRun).block_id).toBe('b0');
    expect((page.items[2] as DeltaRun).block_id).toBe('b2');
  });
});

// ──────────────────────────────────────────────────────────────
// bucketEventsByIteration
// ──────────────────────────────────────────────────────────────

import { bucketEventsByIteration } from '../eventLog';
import type { EventBucket } from '../eventLog';

const iterStarted = (
  blockId: string, index: number, ts?: number,
): RawEvent => ({
  type: 'iteration_started',
  block_id: blockId,
  index,
  ts,
});

const iterCompleted = (
  blockId: string, index: number, status: 'passed' | 'failed' = 'passed', ts?: number,
): RawEvent => ({
  type: 'iteration_completed',
  block_id: blockId,
  index,
  status,
  ts,
});

const runEvent = (type: string, ts?: number): RawEvent => ({ type, ts });

describe('bucketEventsByIteration', () => {
  it('returns lifecycle-only when no iteration events present', () => {
    const events: RawEvent[] = [
      runEvent('run_started', 1),
      runEvent('run_completed', 2),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets).toHaveLength(1);
    expect(buckets[0].kind).toBe('lifecycle');
    expect(buckets[0].events).toHaveLength(2);
  });

  it('always returns lifecycle bucket even when input is empty', () => {
    const buckets = bucketEventsByIteration([]);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]).toEqual({ kind: 'lifecycle', events: [] });
  });

  it('opens iteration bucket on iteration_started, closes on completed', () => {
    const events: RawEvent[] = [
      runEvent('run_started', 1),
      iterStarted('b1', 0, 2),
      delta('b1', 'hello', 3),
      iterCompleted('b1', 0, 'passed', 4),
      runEvent('run_completed', 5),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets).toHaveLength(2);
    const [lifecycle, iter0] = buckets;
    expect(lifecycle.kind).toBe('lifecycle');
    expect(lifecycle.events.map(e => e.type)).toEqual(['run_started', 'run_completed']);
    expect(iter0.kind).toBe('iteration');
    expect(iter0.index).toBe(0);
    expect(iter0.blockId).toBe('b1');
    expect(iter0.status).toBe('passed');
    expect(iter0.events.map(e => e.type)).toEqual([
      'iteration_started', 'task_text_delta', 'iteration_completed',
    ]);
  });

  it('marks unfinished iteration as running and includes trailing events', () => {
    const events: RawEvent[] = [
      iterStarted('b1', 0, 1),
      delta('b1', 'partial', 2),
      tool('b1', 'fs_read', 3),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets).toHaveLength(2);
    expect(buckets[1].status).toBe('running');
    expect(buckets[1].events).toHaveLength(3);
  });

  it('preserves source order across multiple iterations', () => {
    const events: RawEvent[] = [
      iterStarted('b1', 0),
      delta('b1', 'one'),
      iterCompleted('b1', 0),
      iterStarted('b1', 1),
      delta('b1', 'two'),
      iterCompleted('b1', 1, 'failed'),
      iterStarted('b1', 2),
      delta('b1', 'three'),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets).toHaveLength(4); // lifecycle + 3 iterations
    expect(buckets[1].index).toBe(0);
    expect(buckets[1].status).toBe('passed');
    expect(buckets[2].index).toBe(1);
    expect(buckets[2].status).toBe('failed');
    expect(buckets[3].index).toBe(2);
    expect(buckets[3].status).toBe('running');
  });

  it('routes pre-iteration events into lifecycle bucket', () => {
    const events: RawEvent[] = [
      runEvent('run_started', 1),
      delta('b1', 'before-iteration', 2),       // unusual but possible
      iterStarted('b1', 0, 3),
      delta('b1', 'inside', 4),
      iterCompleted('b1', 0, 'passed', 5),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets[0].kind).toBe('lifecycle');
    expect(buckets[0].events.map(e => e.type)).toEqual(['run_started', 'task_text_delta']);
    expect(buckets[1].events.map(e => e.type)).toEqual([
      'iteration_started', 'task_text_delta', 'iteration_completed',
    ]);
  });

  it('always routes run_* events to lifecycle even mid-iteration', () => {
    const events: RawEvent[] = [
      iterStarted('b1', 0),
      delta('b1', 'x'),
      runEvent('run_failed'),     // mid-iteration run-scope event
      delta('b1', 'y'),
      iterCompleted('b1', 0),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets[0].events.map(e => e.type)).toEqual(['run_failed']);
    expect(buckets[1].events.map(e => e.type)).toEqual([
      'iteration_started', 'task_text_delta', 'task_text_delta', 'iteration_completed',
    ]);
  });

  it('drops orphan iteration_completed into lifecycle (defensive)', () => {
    const events: RawEvent[] = [
      runEvent('run_started'),
      iterCompleted('b1', 0),     // no started
      runEvent('run_completed'),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets).toHaveLength(1);
    expect(buckets[0].kind).toBe('lifecycle');
    expect(buckets[0].events.map(e => e.type)).toEqual([
      'run_started', 'iteration_completed', 'run_completed',
    ]);
  });

  it('infers index from emit order when payload omits it', () => {
    const events: RawEvent[] = [
      { type: 'iteration_started', block_id: 'b1' } as RawEvent,    // no index
      { type: 'iteration_completed', block_id: 'b1' } as RawEvent,
      { type: 'iteration_started', block_id: 'b1' } as RawEvent,
      { type: 'iteration_completed', block_id: 'b1' } as RawEvent,
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets[1].index).toBe(0);
    expect(buckets[2].index).toBe(1);
  });

  it('defaults to passed status when iteration_completed has unknown status', () => {
    const events: RawEvent[] = [
      iterStarted('b1', 0),
      { type: 'iteration_completed', block_id: 'b1', index: 0 } as RawEvent,
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets[1].status).toBe('passed');
  });

  it('handles back-to-back iteration_started without intervening completion', () => {
    // Defensive: a malformed stream where started fires twice.
    // The first iteration is left in 'running' state; the second
    // opens normally.  Events between the two starts go into the first.
    const events: RawEvent[] = [
      iterStarted('b1', 0),
      delta('b1', 'one'),
      iterStarted('b1', 1),
      delta('b1', 'two'),
      iterCompleted('b1', 1, 'passed'),
    ];
    const buckets = bucketEventsByIteration(events);
    expect(buckets).toHaveLength(3);
    expect(buckets[1].index).toBe(0);
    expect(buckets[1].status).toBe('running'); // never completed
    expect(buckets[1].events.map(e => e.type)).toEqual(['iteration_started', 'task_text_delta']);
    expect(buckets[2].index).toBe(1);
    expect(buckets[2].status).toBe('passed');
  });
});

