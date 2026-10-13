/**
 * Tests for the pure import gate in memoryApi.ts: importReadiness.
 *
 * The Import button in MemoryImportPanel is enabled only when this says
 * so.  The server enforces the bounds (409 on either fingerprint); this
 * explains "nothing to do" before the click.
 */
import { importReadiness, DEFAULT_IMPORT_OPTIONS } from '../memoryApi';
import type { ImportPreview } from '../memoryApi';

function preview(counts: Record<string, number>, total: number): ImportPreview {
  return {
    bundle_fingerprint: 'b'.repeat(16), store_fingerprint: 's'.repeat(16),
    total, exported_by: 'alice', exported_at: '2026-10-07T00:00:00Z',
    counts, nodes_new: [], nodes_merged: [], warnings: [], sample: [],
  };
}

describe('importReadiness', () => {
  it('blocks without a bundle', () => {
    expect(importReadiness(null).ready).toBe(false);
    expect(importReadiness(null).reason).toMatch(/choose a bundle/i);
  });

  it('blocks while the preview is stale', () => {
    const r = importReadiness(preview({ to_write: 3, new: 3 }, 3), true);
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/refreshing/i);
  });

  it('explains a bundle that is entirely already present', () => {
    const r = importReadiness(preview({ to_write: 0, identical: 2, duplicate_content: 1 }, 3));
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/already in your memory/i);
  });

  it('distinguishes "nothing to write because of options" (all collisions skipped)', () => {
    const r = importReadiness(preview({ to_write: 0, collision_skipped: 2 }, 2));
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/nothing to import with these options/i);
  });

  it('is ready when something will be written', () => {
    expect(importReadiness(preview({ to_write: 1, new: 1, identical: 4 }, 5)))
      .toEqual({ ready: true, reason: null });
  });
});

describe('DEFAULT_IMPORT_OPTIONS', () => {
  it('keeps both on collision and merges the mind-map by default', () => {
    expect(DEFAULT_IMPORT_OPTIONS.on_collision).toBe('rename');
    expect(DEFAULT_IMPORT_OPTIONS.skip_duplicate_content).toBe(true);
    expect(DEFAULT_IMPORT_OPTIONS.include_mindmap).toBe(true);
  });
});
