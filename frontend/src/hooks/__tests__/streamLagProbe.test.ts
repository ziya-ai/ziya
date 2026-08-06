/**
 * Tests for streamLagProbe — the diagnostic that distinguishes a
 * BACKLOGGED task-run event stream (lag grows; fix is batching) from a
 * DROPPING one (lag flat, counts short; fix is transport).
 *
 * The distinction matters because the two have opposite fixes, and the
 * UI symptom ("stale status line, late events") is identical.
 */

import {
  BACKLOG_LAG_THRESHOLD_S,
  classifyLag,
  emptyLagStats,
  eventClockSeconds,
  foldSample,
  formatLagStats,
  isLagProbeEnabled,
  measureLag,
} from '../streamLagProbe';

const NOW_MS = 1_800_000_000_000;
const nowS = NOW_MS / 1000;

describe('eventClockSeconds', () => {
  it('reads task_executor\'s ts field', () => {
    expect(eventClockSeconds({ type: 'task_progress', ts: 123 })).toBe(123);
  });

  it('reads block_executor\'s at field', () => {
    expect(eventClockSeconds({ type: 'block_status', at: 456 })).toBe(456);
  });

  it('prefers ts when both are present', () => {
    expect(eventClockSeconds({ ts: 1, at: 2 })).toBe(1);
  });

  it('returns null for task_text_delta, which carries no clock', () => {
    // This is the whole reason lag is measured only over clocked events:
    // the highest-volume event type is unclocked.  Verified against
    // app/agents/task_executor.py — the task_text_delta emit carries
    // only run_id / block_id / content.
    expect(eventClockSeconds({
      type: 'task_text_delta', run_id: 'r', block_id: 'b', content: 'hi',
    })).toBeNull();
  });

  it('rejects non-numeric and non-finite clocks', () => {
    expect(eventClockSeconds({ ts: 'nope' })).toBeNull();
    expect(eventClockSeconds({ ts: NaN })).toBeNull();
    expect(eventClockSeconds({ at: Infinity })).toBeNull();
  });

  it('tolerates non-object input', () => {
    expect(eventClockSeconds(null)).toBeNull();
    expect(eventClockSeconds('str')).toBeNull();
  });
});

describe('measureLag', () => {
  it('reports elapsed seconds since the server stamp', () => {
    expect(measureLag({ ts: nowS - 42 }, NOW_MS)).toBeCloseTo(42, 5);
  });

  it('clamps a client clock running ahead of the server to zero', () => {
    // Otherwise a skewed client reports negative lag and poisons maxLagS.
    expect(measureLag({ ts: nowS + 100 }, NOW_MS)).toBe(0);
  });

  it('is null when there is no clock', () => {
    expect(measureLag({ type: 'task_text_delta' }, NOW_MS)).toBeNull();
  });
});

describe('foldSample', () => {
  it('counts unclocked events toward volume but not toward lag', () => {
    const s = foldSample(emptyLagStats(),
      { type: 'task_text_delta', content: 'x' }, NOW_MS);
    expect(s.total).toBe(1);
    expect(s.unclocked).toBe(1);
    expect(s.clocked).toBe(0);
    expect(s.lastLagS).toBeNull();
    expect(s.maxLagS).toBeNull();
  });

  it('tracks first, last and max lag across clocked events', () => {
    let s = emptyLagStats();
    s = foldSample(s, { type: 'task_progress', ts: nowS - 1 }, NOW_MS);
    s = foldSample(s, { type: 'task_progress', ts: nowS - 30 }, NOW_MS);
    s = foldSample(s, { type: 'task_progress', ts: nowS - 10 }, NOW_MS);
    expect(s.firstLagS).toBeCloseTo(1, 5);
    expect(s.lastLagS).toBeCloseTo(10, 5);
    expect(s.maxLagS).toBeCloseTo(30, 5);
    expect(s.clocked).toBe(3);
  });

  it('tallies per-type counts', () => {
    let s = emptyLagStats();
    s = foldSample(s, { type: 'task_text_delta' }, NOW_MS);
    s = foldSample(s, { type: 'task_text_delta' }, NOW_MS);
    s = foldSample(s, { type: 'task_tool_call', ts: nowS }, NOW_MS);
    expect(s.byType).toEqual({ task_text_delta: 2, task_tool_call: 1 });
  });

  it('does not mutate the input stats', () => {
    const before = emptyLagStats();
    foldSample(before, { type: 'task_progress', ts: nowS }, NOW_MS);
    expect(before.total).toBe(0);
    expect(before.byType).toEqual({});
  });

  it('buckets untyped events rather than dropping them', () => {
    const s = foldSample(emptyLagStats(), { ts: nowS }, NOW_MS);
    expect(s.byType).toEqual({ '(untyped)': 1 });
  });
});

describe('classifyLag', () => {
  it('is inconclusive with fewer than two clocked samples', () => {
    let s = emptyLagStats();
    expect(classifyLag(s)).toBe('inconclusive');
    s = foldSample(s, { type: 'task_progress', ts: nowS - 600 }, NOW_MS);
    expect(classifyLag(s)).toBe('inconclusive');
  });

  it('calls growing, large lag a backlog', () => {
    let s = emptyLagStats();
    s = foldSample(s, { type: 'task_progress', ts: nowS - 1 }, NOW_MS);
    s = foldSample(s, { type: 'task_progress', ts: nowS - 120 }, NOW_MS);
    expect(classifyLag(s)).toBe('backlog');
  });

  it('calls consistently small lag prompt delivery', () => {
    let s = emptyLagStats();
    s = foldSample(s, { type: 'task_progress', ts: nowS - 0.2 }, NOW_MS);
    s = foldSample(s, { type: 'task_progress', ts: nowS - 0.3 }, NOW_MS);
    expect(classifyLag(s)).toBe('prompt');
  });

  it('does not call a large but SHRINKING lag a backlog', () => {
    // A tab returning from background has one big stale sample then
    // recovers; that is catch-up, not sustained backpressure.
    let s = emptyLagStats();
    s = foldSample(s, { type: 'task_progress', ts: nowS - 300 }, NOW_MS);
    s = foldSample(s, {
      type: 'task_progress', ts: nowS - BACKLOG_LAG_THRESHOLD_S - 1,
    }, NOW_MS);
    expect(classifyLag(s)).not.toBe('backlog');
  });
});

describe('formatLagStats', () => {
  it('renders em-dashes for absent lag instead of "null"', () => {
    expect(formatLagStats(emptyLagStats())).toContain('last=—');
  });

  it('includes the verdict and the busiest event types', () => {
    let s = emptyLagStats();
    s = foldSample(s, { type: 'task_text_delta' }, NOW_MS);
    s = foldSample(s, { type: 'task_progress', ts: nowS - 1 }, NOW_MS);
    const out = formatLagStats(s);
    expect(out).toContain('verdict=');
    expect(out).toContain('task_text_delta=1');
  });
});

describe('isLagProbeEnabled', () => {
  afterEach(() => { try { localStorage.clear(); } catch { /* ignore */ } });

  it('is off by default so the probe costs nothing in normal use', () => {
    expect(isLagProbeEnabled()).toBe(false);
  });

  it('is on when the debug key is exactly "1"', () => {
    localStorage.setItem('ziya.debug.taskRunLag', '1');
    expect(isLagProbeEnabled()).toBe(true);
  });

  it('ignores other truthy-looking values', () => {
    localStorage.setItem('ziya.debug.taskRunLag', 'true');
    expect(isLagProbeEnabled()).toBe(false);
  });
});
