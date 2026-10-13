/**
 * Tests for the pure export gate in memoryApi.ts: exportReadiness.
 *
 * The Export button in MemoryExportPanel is enabled only when this says
 * so.  The server enforces the same bounds (409 stale fingerprint, 412
 * unacknowledged warnings); this is the client-side explanation of them.
 */
import { exportReadiness, DEFAULT_EXPORT_SELECTION } from '../memoryApi';
import type { ExportPreview } from '../memoryApi';

function preview(overrides: Partial<ExportPreview> = {}): ExportPreview {
  return {
    memory_ids: ['m_1'],
    fingerprint: 'abcd1234abcd1234',
    total: 1,
    held_back: {},
    counts: { by_layer: { decision: 1 }, by_domain: [], by_project: [], nodes: 0 },
    warnings: [],
    node_ids: [],
    sample: [],
    ...overrides,
  };
}

describe('exportReadiness', () => {
  it('blocks without a preview', () => {
    const r = exportReadiness(null, false);
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/preview/i);
  });

  it('blocks while the preview is stale, even if it would otherwise be ready', () => {
    const r = exportReadiness(preview(), true, true);
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/refreshing/i);
  });

  it('blocks an empty selection', () => {
    const r = exportReadiness(preview({ total: 0, memory_ids: [] }), false);
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/nothing selected/i);
  });

  it('blocks unacknowledged warnings and counts them', () => {
    const p = preview({
      warnings: [
        { memory_id: 'm_1', kind: 'credential:aws-access-key', match: '', snippet: '' },
        { memory_id: 'm_1', kind: 'local_path', match: '/Users/x', snippet: '' },
      ],
    });
    const r = exportReadiness(p, false);
    expect(r.ready).toBe(false);
    expect(r.reason).toMatch(/2 warnings/);
  });

  it('singularises one warning', () => {
    const p = preview({ warnings: [{ memory_id: null, kind: 'provenance_included', match: '', snippet: '' }] });
    expect(exportReadiness(p, false).reason).toMatch(/1 warning to export/);
  });

  it('is ready once warnings are acknowledged', () => {
    const p = preview({ warnings: [{ memory_id: 'm_1', kind: 'email', match: 'a@b.c', snippet: '' }] });
    expect(exportReadiness(p, true)).toEqual({ ready: true, reason: null });
  });

  it('is ready with a clean, non-empty preview', () => {
    expect(exportReadiness(preview(), false)).toEqual({ ready: true, reason: null });
  });
});

describe('DEFAULT_EXPORT_SELECTION', () => {
  it('defaults to the safe choices', () => {
    expect(DEFAULT_EXPORT_SELECTION.strip_provenance).toBe(true);
    expect(DEFAULT_EXPORT_SELECTION.anonymize_paths).toBe(true);
    expect(DEFAULT_EXPORT_SELECTION.statuses).toEqual(['active']);
    expect(DEFAULT_EXPORT_SELECTION.domains).toEqual([]);
  });
});
