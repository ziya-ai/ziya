/**
 * Health-tab seam helpers in memoryApi.ts.
 *
 * normalizeReview: GET /api/v1/memory/review returns ``stale_memories`` /
 * ``orphan_memories`` / ``large_nodes`` (app/memory/maintenance.py
 * get_review_summary) while the Health tab reads ``stale`` / ``orphans`` /
 * ``oversized_nodes``.  Nothing renamed them, so every section rendered
 * "all healthy" and the orphan-threshold line read 0 whatever the store
 * held.  The normaliser is the one place the two shapes meet.
 *
 * hydrateReview: the backend review entries are slices (id, 100-char
 * content, tags — orphans carry no layer/importance/last_accessed) and
 * MemoryCard renders those fields; hydration swaps in the full record the
 * browser already holds.
 *
 * memoryPauseLabel: banner text for the pause written by
 * app/utils/memory_pause.py, surfaced with the Resume button.
 */
import {
  normalizeReview,
  hydrateReview,
  memoryPauseLabel,
  type MemoryItem,
} from '../memoryApi';

const mem = (id: string, extra: Partial<MemoryItem> = {}): MemoryItem => ({
  id, content: `content ${id}`, layer: 'decision', tags: ['t'],
  learned_from: 'x', created: '2026-09-01', last_accessed: '2026-09-02',
  status: 'active', importance: 0.7, ...extra,
});

describe('normalizeReview', () => {
  test('maps the backend field names onto the Health-tab shape', () => {
    const out = normalizeReview({
      stale_memories: [{ id: 'm1', content: 'c', layer: 'decision', last_accessed: '2026-01-01', tags: [] } as any],
      orphan_memories: [{ id: 'm2', content: 'c', tags: [] } as any, { id: 'm3', content: 'c', tags: [] } as any],
      large_nodes: [{ id: 'domain_x', handle: 'X', memory_count: 14 }],
    } as any);
    expect(out.stale.map(m => m.id)).toEqual(['m1']);
    expect(out.orphans.map(m => m.id)).toEqual(['m2', 'm3']);
    expect(out.oversized_nodes).toEqual([{ node_id: 'domain_x', memory_count: 14 }]);
  });

  test('accepts an already-normalised payload unchanged', () => {
    const already = { stale: [mem('a')], orphans: [], oversized_nodes: [{ node_id: 'n', memory_count: 12 }] };
    expect(normalizeReview(already)).toEqual(already);
  });

  test('null / empty payload yields empty lists, never undefined', () => {
    for (const raw of [null, undefined, {}]) {
      const out = normalizeReview(raw as any);
      expect(out.stale).toEqual([]);
      expect(out.orphans).toEqual([]);
      expect(out.oversized_nodes).toEqual([]);
    }
  });
});

describe('hydrateReview', () => {
  test('replaces backend slices with the full record when known', () => {
    const full = mem('m2', { layer: 'lexicon', importance: 0.9 });
    const out = hydrateReview(
      { stale: [], orphans: [{ id: 'm2', content: 'trunc', tags: ['t'] } as any], oversized_nodes: [] },
      [mem('m1'), full],
    );
    expect(out.orphans[0]).toBe(full);
  });

  test('keeps the slice when no full record is known', () => {
    const slice = { id: 'ghost', content: 'c', tags: [] } as any;
    const out = hydrateReview({ stale: [slice], orphans: [], oversized_nodes: [] }, [mem('m1')]);
    expect(out.stale[0]).toBe(slice);
  });
});

describe('memoryPauseLabel', () => {
  const NOW = 1_000_000_000_000;

  test('null record → null (no banner)', () => {
    expect(memoryPauseLabel(null, NOW)).toBeNull();
    expect(memoryPauseLabel(undefined, NOW)).toBeNull();
  });

  test('names the kind, the passes that failed, and how long ago', () => {
    const label = memoryPauseLabel({
      paused_at: NOW - 2 * 3_600_000, kind: 'authentication_error',
      where: ['extraction', 'lifecycle'], repeat_count: 3, error: 'expired',
    }, NOW)!;
    expect(label).toContain('2h ago');
    expect(label).toContain('authentication_error');
    expect(label).toContain('extraction, lifecycle');
    expect(label).toContain('3 further failures');
  });

  test('degrades gracefully on a sparse record', () => {
    const label = memoryPauseLabel({ paused_at: null }, NOW)!;
    expect(label).toMatch(/^Memory paused/);
    expect(label).not.toContain('undefined');
    expect(label).not.toContain('further failure');
  });
});
