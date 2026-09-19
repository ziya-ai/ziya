import { formatLastActivity, STALE_AFTER_S } from '../liveActivity';
import { accumulateLive, LiveTaskState } from '../../../hooks/useTaskRunStream';

const NOW_MS = 1_800_000_000_000; // fixed clock
// Every timestamp the tile handles is epoch MILLISECONDS (run record
// schema 2).  The helper takes ms too; a seconds offset is scaled here.
const ago = (s: number) => NOW_MS - s * 1000;

describe('formatLastActivity', () => {
  it('reads "active now" under 10s', () => {
    expect(formatLastActivity(ago(3), NOW_MS)).toEqual({ label: 'active now', stale: false });
  });

  it('formats seconds under a minute', () => {
    expect(formatLastActivity(ago(42), NOW_MS)).toEqual({ label: '42s ago', stale: false });
  });

  it('formats minutes under an hour', () => {
    expect(formatLastActivity(ago(300), NOW_MS)).toEqual({ label: '5m ago', stale: true });
  });

  it('formats hours', () => {
    expect(formatLastActivity(ago(7200), NOW_MS)).toEqual({ label: '2h ago', stale: true });
  });

  it('flags stale exactly at the threshold', () => {
    expect(formatLastActivity(ago(STALE_AFTER_S), NOW_MS).stale).toBe(true);
    expect(formatLastActivity(ago(STALE_AFTER_S - 1), NOW_MS).stale).toBe(false);
  });

  it('clamps future timestamps to zero age', () => {
    expect(formatLastActivity(ago(-100), NOW_MS)).toEqual({ label: 'active now', stale: false });
  });
});

/**
 * Coarse buckets past a day.  The deck's run history calls this with
 * `created_at`, so a card run last spring rendered as "3020h ago": true,
 * unreadable, and precisely the number a history list exists to save the
 * reader from converting.  Hours remain the unit up to 23h so the
 * heartbeat label on a long-running task is unaffected.
 */
describe('formatLastActivity coarse buckets', () => {
  const H = 3600, D = 86400;
  const ago = (s: number) => formatLastActivity(NOW_MS - s * 1000, NOW_MS).label;

  it('still reads in hours just below the day boundary', () => {
    expect(ago(22.9 * H)).toBe('23h ago');
  });

  it('switches to days at 23h', () => {
    expect(ago(23 * H)).toBe('1d ago');
    expect(ago(24 * H)).toBe('1d ago');
    expect(ago(3 * D)).toBe('3d ago');
  });

  it('switches to weeks at 7d', () => {
    expect(ago(7 * D)).toBe('1w ago');
    expect(ago(21 * D)).toBe('3w ago');
  });

  it('switches to months at 30d', () => {
    expect(ago(30 * D)).toBe('1mo ago');
    expect(ago(200 * D)).toBe('7mo ago');
  });

  it('switches to years at 365d', () => {
    expect(ago(365 * D)).toBe('1y ago');
    expect(ago(800 * D)).toBe('2y ago');
  });

  it('never reports an unconverted hour count past a day', () => {
    // The defect verbatim: 3020h is ~4 months.  Paired with a positive
    // assertion so this cannot pass by the function returning nothing.
    const label = ago(3020 * H);
    expect(label).not.toMatch(/\d{3,}h/);
    expect(label).toBe('4mo ago');
  });

  it('keeps everything past an hour flagged stale', () => {
    // The unit changes how the age reads; it must not change the
    // judgement the running-tile surface keys on.
    for (const s of [2 * H, 2 * D, 2 * 7 * D, 60 * D, 400 * D]) {
      expect(formatLastActivity(NOW_MS - s * 1000, NOW_MS).stale).toBe(true);
    }
  });
});

// Minimal harness: run accumulateLive's functional updater against a
// plain previous state, mirroring accumulateLive.test.ts conventions.
const EMPTY: LiveTaskState = { text: {}, toolCalls: [], events: [], iterations: [], variables: {}, blockStatuses: {} };
function apply(prev: LiveTaskState, evt: unknown): LiveTaskState {
  let out = prev;
  const setLive = (f: any) => { out = typeof f === 'function' ? f(prev) : f; };
  accumulateLive(setLive as any, evt);
  return out;
}

describe('accumulateLive task_progress handling', () => {
  it('captures the note from a task_progress event', () => {
    const out = apply(EMPTY, {
      type: 'task_progress', block_id: 'b1',
      note: 'ran run_shell_command: git status', ts: 123_000,
    });
    expect(out.progressNote).toBe('ran run_shell_command: git status');
    // The wire carries the server clock in epoch MS (every emitter is
    // `now_ms()`), the same unit as run.last_activity_at (record schema
    // 2), so the hook passes it through unconverted.
    expect(out.lastActivityTs).toBe(123_000);
  });

  it('any event updates lastActivityTs but not the note', () => {
    const prev: LiveTaskState = { ...EMPTY, progressNote: 'ran x' };
    const out = apply(prev, {
      type: 'task_text_delta', block_id: 'b1', content: 'hi',
    });
    expect(out.progressNote).toBe('ran x');       // preserved
    // Fell back to the client clock — which must also be ms, or a run
    // record stamped in ms would always out-rank the live stream.
    expect(out.lastActivityTs).toBeGreaterThan(1e11);
  });

  it('later task_progress overwrites the note (last-write-wins)', () => {
    let s = apply(EMPTY, { type: 'task_progress', block_id: 'b1', note: 'ran a', ts: 1_000 });
    s = apply(s, { type: 'task_progress', block_id: 'b1', note: 'ran b', ts: 2_000 });
    expect(s.progressNote).toBe('ran b');
    expect(s.lastActivityTs).toBe(2_000);
  });

  it('a live ts is comparable with a run record last_activity_at (ms)', () => {
    // The seam the unit change exists for: before schema 2 the run side
    // was seconds and this comparison silently preferred whichever unit
    // happened to be larger.  A wire event 5s after the record must win.
    const recordMs = 1_789_601_617_000;
    const out = apply(EMPTY, {
      type: 'task_progress', block_id: 'b1', note: 'newer', ts: recordMs + 5_000,
    });
    expect(out.lastActivityTs!).toBeGreaterThan(recordMs);
    expect(out.lastActivityTs! - recordMs).toBe(5_000);
  });

  it('empty or non-string note is ignored', () => {
    const prev: LiveTaskState = { ...EMPTY, progressNote: 'ran x' };
    expect(apply(prev, { type: 'task_progress', note: '' }).progressNote).toBe('ran x');
    expect(apply(prev, { type: 'task_progress', note: 42 }).progressNote).toBe('ran x');
  });

  it('event ts is preferred over wall clock when present', () => {
    const out = apply(EMPTY, { type: 'task_tool_call', block_id: 'b1', tool_name: 't', ts: 555_500 });
    // Wire ms passes through unchanged.
    expect(out.lastActivityTs).toBe(555_500);
  });
});
