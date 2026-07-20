import { formatLastActivity, STALE_AFTER_S } from '../liveActivity';
import { accumulateLive, LiveTaskState } from '../../../hooks/useTaskRunStream';

const NOW_MS = 1_800_000_000_000; // fixed clock
const nowS = NOW_MS / 1000;

describe('formatLastActivity', () => {
  it('reads "active now" under 10s', () => {
    expect(formatLastActivity(nowS - 3, NOW_MS)).toEqual({ label: 'active now', stale: false });
  });

  it('formats seconds under a minute', () => {
    expect(formatLastActivity(nowS - 42, NOW_MS)).toEqual({ label: '42s ago', stale: false });
  });

  it('formats minutes under an hour', () => {
    expect(formatLastActivity(nowS - 300, NOW_MS)).toEqual({ label: '5m ago', stale: true });
  });

  it('formats hours', () => {
    expect(formatLastActivity(nowS - 7200, NOW_MS)).toEqual({ label: '2h ago', stale: true });
  });

  it('flags stale exactly at the threshold', () => {
    expect(formatLastActivity(nowS - STALE_AFTER_S, NOW_MS).stale).toBe(true);
    expect(formatLastActivity(nowS - (STALE_AFTER_S - 1), NOW_MS).stale).toBe(false);
  });

  it('clamps future timestamps to zero age', () => {
    expect(formatLastActivity(nowS + 100, NOW_MS)).toEqual({ label: 'active now', stale: false });
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
      note: 'ran run_shell_command: git status', ts: 123.0,
    });
    expect(out.progressNote).toBe('ran run_shell_command: git status');
    expect(out.lastActivityTs).toBe(123.0);
  });

  it('any event updates lastActivityTs but not the note', () => {
    const prev: LiveTaskState = { ...EMPTY, progressNote: 'ran x' };
    const out = apply(prev, {
      type: 'task_text_delta', block_id: 'b1', content: 'hi',
    });
    expect(out.progressNote).toBe('ran x');       // preserved
    expect(out.lastActivityTs).toBeGreaterThan(0); // fell back to now
  });

  it('later task_progress overwrites the note (last-write-wins)', () => {
    let s = apply(EMPTY, { type: 'task_progress', block_id: 'b1', note: 'ran a', ts: 1 });
    s = apply(s, { type: 'task_progress', block_id: 'b1', note: 'ran b', ts: 2 });
    expect(s.progressNote).toBe('ran b');
    expect(s.lastActivityTs).toBe(2);
  });

  it('empty or non-string note is ignored', () => {
    const prev: LiveTaskState = { ...EMPTY, progressNote: 'ran x' };
    expect(apply(prev, { type: 'task_progress', note: '' }).progressNote).toBe('ran x');
    expect(apply(prev, { type: 'task_progress', note: 42 }).progressNote).toBe('ran x');
  });

  it('event ts is preferred over wall clock when present', () => {
    const out = apply(EMPTY, { type: 'task_tool_call', block_id: 'b1', tool_name: 't', ts: 555.5 });
    expect(out.lastActivityTs).toBe(555.5);
  });
});
