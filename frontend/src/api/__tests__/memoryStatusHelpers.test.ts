/**
 * Tests for the pure Health-tab status helpers in memoryApi.ts:
 * formatLastRunLabel and orphanStatus.
 *
 * These back the Memory Browser's "Organize last ran" / pending-orphan
 * status line.  Extracted from the component's inline IIFE so they can be
 * tested without rendering MemoryBrowser (and its marked/ESM render chain).
 */
import {
  formatLastRunLabel,
  orphanStatus,
  embeddingCoverage,
  AUTO_ORGANIZE_ORPHAN_THRESHOLD,
} from '../memoryApi';

describe('formatLastRunLabel', () => {
  const NOW = 1_000_000_000_000; // fixed epoch-ms for determinism

  test('null / undefined / 0 → "never"', () => {
    expect(formatLastRunLabel(null, NOW)).toBe('never');
    expect(formatLastRunLabel(undefined, NOW)).toBe('never');
    expect(formatLastRunLabel(0, NOW)).toBe('never');
  });

  test('minutes granularity', () => {
    expect(formatLastRunLabel(NOW - 5 * 60_000, NOW)).toBe('5m ago');
    expect(formatLastRunLabel(NOW - 59 * 60_000, NOW)).toBe('59m ago');
  });

  test('hours granularity', () => {
    expect(formatLastRunLabel(NOW - 60 * 60_000, NOW)).toBe('1h ago');
    expect(formatLastRunLabel(NOW - 23 * 60 * 60_000, NOW)).toBe('23h ago');
  });

  test('days granularity', () => {
    expect(formatLastRunLabel(NOW - 24 * 60 * 60_000, NOW)).toBe('1d ago');
    expect(formatLastRunLabel(NOW - 6 * 24 * 60 * 60_000, NOW)).toBe('6d ago');
  });

  test('future timestamp clamps to "just now" (clock skew safety)', () => {
    expect(formatLastRunLabel(NOW + 60_000, NOW)).toBe('just now');
  });

  test('boundary: exactly 60 min rolls to hours', () => {
    expect(formatLastRunLabel(NOW - 60 * 60_000, NOW)).toBe('1h ago');
    expect(formatLastRunLabel(NOW - 59 * 60_000 - 59_000, NOW)).toBe('59m ago');
  });
});

describe('orphanStatus', () => {
  test('below threshold: not at-threshold, plural label', () => {
    const s = orphanStatus(3);
    expect(s.count).toBe(3);
    expect(s.atThreshold).toBe(false);
    expect(s.label).toBe('Currently 3 orphans — auto-organize triggers at 15.');
  });

  test('singular grammar at count 1', () => {
    expect(orphanStatus(1).label).toBe('Currently 1 orphan — auto-organize triggers at 15.');
  });

  test('zero orphans', () => {
    const s = orphanStatus(0);
    expect(s.count).toBe(0);
    expect(s.label).toBe('Currently 0 orphans — auto-organize triggers at 15.');
  });

  test('at exactly the threshold → atThreshold true', () => {
    const s = orphanStatus(AUTO_ORGANIZE_ORPHAN_THRESHOLD);
    expect(s.atThreshold).toBe(true);
  });

  test('above threshold → atThreshold true', () => {
    expect(orphanStatus(20).atThreshold).toBe(true);
  });

  test('null / undefined / negative coerce to 0', () => {
    expect(orphanStatus(null).count).toBe(0);
    expect(orphanStatus(undefined).count).toBe(0);
    expect(orphanStatus(-5).count).toBe(0);
  });

  test('custom threshold respected', () => {
    const s = orphanStatus(8, 10);
    expect(s.atThreshold).toBe(false);
    expect(s.label).toBe('Currently 8 orphans — auto-organize triggers at 10.');
    expect(orphanStatus(10, 10).atThreshold).toBe(true);
  });
});

describe('embeddingCoverage', () => {
  test('null / undefined → disabled', () => {
    const c = embeddingCoverage(null);
    expect(c.enabled).toBe(false);
    expect(c.degraded).toBe(false);
    expect(c.label).toMatch(/disabled/i);
    expect(embeddingCoverage(undefined).enabled).toBe(false);
  });

  test('provider disabled (enabled:false) → disabled label, not degraded', () => {
    const c = embeddingCoverage({ enabled: false, provider: 'none' });
    expect(c.enabled).toBe(false);
    expect(c.degraded).toBe(false);
    expect(c.label).toMatch(/shared-tag overlap/i);
  });

  test('full coverage → 100%, not degraded, green-path label', () => {
    const c = embeddingCoverage({ enabled: true, provider: 'bedrock_titan', total: 40, embedded: 40, missing: 0 });
    expect(c.pct).toBe(100);
    expect(c.degraded).toBe(false);
    expect(c.label).toMatch(/full coverage/i);
  });

  test('partial coverage → degraded, rounded pct, backfill hint', () => {
    const c = embeddingCoverage({ enabled: true, provider: 'bedrock_titan', total: 40, embedded: 30, missing: 10 });
    expect(c.pct).toBe(75);
    expect(c.degraded).toBe(true);
    expect(c.missing).toBe(10);
    expect(c.label).toMatch(/partial/i);
    expect(c.label).toMatch(/backfill/i);
  });

  test('missing derived from total - embedded when missing field absent', () => {
    const c = embeddingCoverage({ enabled: true, provider: 'bedrock_titan', total: 10, embedded: 4 });
    expect(c.missing).toBe(6);
    expect(c.pct).toBe(40);
    expect(c.degraded).toBe(true);
  });

  test('zero total → 100% (vacuously complete), not degraded', () => {
    const c = embeddingCoverage({ enabled: true, provider: 'bedrock_titan', total: 0, embedded: 0, missing: 0 });
    expect(c.pct).toBe(100);
    expect(c.degraded).toBe(false);
  });

  test('pct rounds (not truncates)', () => {
    // 5/6 = 83.33% → 83
    expect(embeddingCoverage({ enabled: true, provider: 'p', total: 6, embedded: 5, missing: 1 }).pct).toBe(83);
    // 5/8 = 62.5% → 63 (round-half-up)
    expect(embeddingCoverage({ enabled: true, provider: 'p', total: 8, embedded: 5, missing: 3 }).pct).toBe(63);
  });
});
