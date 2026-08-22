/**
 * Regression tests for Issue 35 — packet renderer crash on sections keyed with
 * `name` (instead of `label`) and object-shape rows (`{fields:[...]}` instead
 * of tuple arrays).
 *
 * Imports the REAL module under test (no re-implementation) so the test cannot
 * drift from shipped logic.
 *
 * Pre-fix behavior these pin against:
 *   - sectionLabel / normalizeSectionRows / normalizeSection did NOT exist
 *     (import would fail) → non-vacuous.
 *   - normalizePacketSpec returned null for a spec with no top-level title even
 *     when it had a valid `sections` array, so the raw name-keyed / object-row
 *     spec reached the renderer unnormalized and `sec.label.split` threw.
 */
import {
  sectionLabel,
  normalizeSectionRows,
  normalizeSection,
  normalizePacketSpec,
} from '../../../utils/d3Plugins/packetPlugin';

describe('sectionLabel (name/title alias + never-undefined)', () => {
  it('resolves name → label', () => {
    expect(sectionLabel({ name: 'x' })).toBe('x');
  });
  it('resolves title → label', () => {
    expect(sectionLabel({ title: 'y' })).toBe('y');
  });
  it('prefers an explicit label over name/title', () => {
    expect(sectionLabel({ label: 'L', name: 'N', title: 'T' })).toBe('L');
  });
  it('returns "" (not undefined) for a section with no label key — so .split never throws', () => {
    const out = sectionLabel({});
    expect(out).toBe('');
    expect(() => out.split('\n')).not.toThrow();
  });
  it('coerces a non-string label to a string', () => {
    expect(sectionLabel({ label: 123 })).toBe('123');
  });
  it('tolerates null / non-object', () => {
    expect(sectionLabel(null)).toBe('');
    expect(sectionLabel('nope' as any)).toBe('');
  });
});

describe('normalizeSectionRows (object-shape → tuple arrays)', () => {
  it('converts {fields:[{name,bits,color}]} to [[name,bits,color]]', () => {
    const rows = normalizeSectionRows([
      { fields: [{ name: 'Ver', bits: 4, color: '#3366cc' }, { name: 'HdrLen', bits: 4 }] },
    ]);
    expect(rows).toEqual([[['Ver', 4, '#3366cc'], ['HdrLen', 4]]]);
  });
  it('leaves canonical tuple-array rows byte-identical (not a catch-all)', () => {
    const canonical = [[['A', 8], ['B', 8, '#fff']]] as any;
    expect(normalizeSectionRows(canonical)).toEqual(canonical);
  });
  it('honours width/size/label field aliases', () => {
    const rows = normalizeSectionRows([{ fields: [{ label: 'F', width: 16 }] }]);
    expect(rows).toEqual([[['F', 16]]]);
  });
  it('accepts {cells:[...]} as an alias for {fields:[...]}', () => {
    const rows = normalizeSectionRows([{ cells: [{ name: 'C', bits: 2 }] }]);
    expect(rows).toEqual([[['C', 2]]]);
  });
  it('returns [] for non-array input rather than throwing', () => {
    expect(normalizeSectionRows(undefined)).toEqual([]);
    expect(normalizeSectionRows({} as any)).toEqual([]);
  });
});

describe('normalizeSection', () => {
  it('repairs a name-keyed, object-row, theme-colored section', () => {
    const sec = normalizeSection(
      { name: 'hdr', theme: 'control', rows: [{ fields: [{ name: 'V', bits: 4 }] }] },
      32,
    );
    expect(sec.label).toBe('hdr');
    expect(sec.rows).toEqual([[['V', 4]]]);
    expect(sec.color).toBe('control'); // theme aliased to color
    expect(() => sec.label.split('\n')).not.toThrow();
  });
  it('synthesizes a placeholder row for a bracket-only section (no rows key)', () => {
    const sec = normalizeSection(
      { name: 'orphan', brackets: [{ label: 'b', start_row: 0, end_row: 5 }] },
      32,
    );
    expect(sec.rows.length).toBe(1);
    expect(sec.label).toBe('orphan');
  });
  it('does not clobber an existing label or color', () => {
    const sec = normalizeSection(
      { label: 'keep', color: '#abcdef', rows: [[['A', 8]]] },
      32,
    );
    expect(sec.label).toBe('keep');
    expect(sec.color).toBe('#abcdef');
    expect(sec.rows).toEqual([[['A', 8]]]);
  });
});

describe('normalizePacketSpec (Issue 35 end-to-end)', () => {
  it('normalizes a titleless, name-keyed, object-row spec into renderable sections', () => {
    const raw = {
      type: 'packet',
      bitWidth: 32,
      sections: [
        { name: 'row-sum-mismatch-header', theme: 'control',
          rows: [{ fields: [{ name: 'Ver', bits: 4, color: '#3366cc' }] }] },
        { name: 'no-rows-key-but-bracket-refs-it',
          brackets: [{ label: 'orphan', start_row: 0, end_row: 5, side: 'left' }] },
      ],
    };
    const spec = normalizePacketSpec(raw);
    expect(spec).not.toBeNull();
    expect(spec!.title).toBe('Packet'); // synthesized, was null pre-fix
    expect(spec!.sections).toHaveLength(2);
    // Every section now has a string label and tuple-array rows → .split safe.
    for (const sec of spec!.sections) {
      expect(typeof sec.label).toBe('string');
      expect(() => sec.label.split('\n')).not.toThrow();
      expect(Array.isArray(sec.rows)).toBe(true);
      for (const row of sec.rows) expect(Array.isArray(row)).toBe(true);
    }
    expect(spec!.sections[0].rows).toEqual([[['Ver', 4, '#3366cc']]]);
  });

  it('still returns null when there are no sections AND no title (guard preserved)', () => {
    expect(normalizePacketSpec({ type: 'packet' } as any)).toBeNull();
    expect(normalizePacketSpec({ type: 'packet', sections: [] } as any)).toBeNull();
  });

  it('preserves a well-formed titled tuple-array spec (not a catch-all)', () => {
    const raw = {
      type: 'packet', title: 'IPv4', bitWidth: 32,
      sections: [{ label: 'Header', rows: [[['Ver', 4], ['IHL', 4]]] }],
    };
    const spec = normalizePacketSpec(raw);
    expect(spec!.title).toBe('IPv4');
    expect(spec!.sections[0].label).toBe('Header');
    expect(spec!.sections[0].rows).toEqual([[['Ver', 4], ['IHL', 4]]]);
  });
});
