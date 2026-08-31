/**
 * D-098 row-nesting-off-by-one and D-099 section-`cells`-alias regression tests.
 *
 * Both are packet STRING/STRUCTURE recovery defects in the shared normalizer
 * (frontend/src/utils/d3Plugins/packetPlugin.ts), imported here as the REAL
 * module so the test cannot drift from shipped logic.
 *
 *  - D-098 (w4-13): normalizeSectionRows recognised a canonical tuple row by
 *    `row.every(Array.isArray)` and returned it BY REFERENCE. A row nested one
 *    level too DEEP (`[[[name,bits],...]]`) also passes that test, so the draw
 *    loop got an array as field[0]/undefined as field[1]; a row one level too
 *    SHALLOW (a bare `[name,bits]` tuple used as the row) fell to `.map(
 *    fieldToTuple)` and produced `[['',0],['',0]]`. Both wrote invalid SVG and
 *    the render never produced an <svg> (30s timeout).
 *  - D-099 (w4-15): `cells` was honoured as a ROW key but not as a SECTION key,
 *    so a section keyed with `cells` lost every field to the placeholder row.
 *
 * Every case pins DIRECTION: it asserts the recovered/correct output, and the
 * "too-deep"/"cells" cases are shapes that unpatched code mangled, so this
 * suite fails against the pre-fix normalizer and passes with the fix.
 */
import {
  normalizeSectionRows,
  normalizeSection,
  normalizePacketSpec,
} from '../../../utils/d3Plugins/packetPlugin';

describe('D-098 off-by-one row nesting is repaired', () => {
  it('unwraps a row nested one level too DEEP into a single multi-field row (w4-13)', () => {
    // The exact shape from packet-w4-13 section 1: an extra array level wraps
    // the whole row of tuples.
    const tooDeep = [
      [
        [
          ['Version', 4],
          ['IHL', 4],
          ['TOS', 8],
          ['Total Length', 16],
        ],
      ],
    ];
    expect(normalizeSectionRows(tooDeep as any)).toEqual([
      [['Version', 4], ['IHL', 4], ['TOS', 8], ['Total Length', 16]],
    ]);
  });

  it('wraps a bare field tuple used one level too SHALLOW as a row (w4-13)', () => {
    // packet-w4-13 section 2: a single [name, bits] tuple handed in AS the row.
    const tooShallow = [['Flat Field Name', 32]];
    expect(normalizeSectionRows(tooShallow as any)).toEqual([
      [['Flat Field Name', 32]],
    ]);
  });

  it('DIRECTION: the too-shallow tuple was previously coerced to empty fields', () => {
    // Pre-fix, ['Flat Field Name', 32] fell to row.map(fieldToTuple), turning
    // each scalar element into ['', 0]. Prove the NEW output is not that.
    const out = normalizeSectionRows([['Flat Field Name', 32]] as any);
    expect(out).not.toEqual([[['', 0], ['', 0]]]);
    expect(out[0][0]).toEqual(['Flat Field Name', 32]);
  });

  it('leaves a canonical tuple-array row byte-identical (not a catch-all)', () => {
    const canonical = [[['A', 8], ['B', 8, '#fff']]] as any;
    expect(normalizeSectionRows(canonical)).toEqual(canonical);
  });

  it('still coerces object-shape rows and {cells}/{fields} row keys', () => {
    expect(normalizeSectionRows([{ fields: [{ name: 'Ver', bits: 4 }] }] as any))
      .toEqual([[['Ver', 4]]]);
    expect(normalizeSectionRows([{ cells: [{ label: 'C', width: 2 }] }] as any))
      .toEqual([[['C', 2]]]);
  });
});

describe('D-099 section keyed with `cells` recovers all fields', () => {
  it('resolves a section `cells` array (with name/label/bits/width/size aliases)', () => {
    // packet-w4-15 section 1, verbatim.
    const sec = normalizeSection(
      {
        name: 'Fixed Header',
        cells: [
          { name: 'Packet Type', bits: 4 },
          { label: 'Flags', width: 4 },
          { name: 'Remaining Length', size: 8 },
        ],
      },
      16,
    );
    expect(sec.label).toBe('Fixed Header');
    // All three fields survive — no placeholder row.
    const flat = sec.rows.flat();
    expect(flat).toEqual([
      ['Packet Type', 4],
      ['Flags', 4],
      ['Remaining Length', 8],
    ]);
  });

  it('DIRECTION: pre-fix a `cells`-keyed section collapsed to a single placeholder row', () => {
    const sec = normalizeSection(
      { name: 'Fixed Header', cells: [{ name: 'A', bits: 4 }, { name: 'B', bits: 4 }] },
      16,
    );
    // The placeholder fallback would be [[['Fixed Header', 16]]] (one field
    // named after the section). Assert we did NOT get that degenerate shape.
    expect(sec.rows.flat()).not.toEqual([['Fixed Header', 16]]);
    expect(sec.rows.flat().length).toBe(2);
  });

  it('a section keyed with `fields` still works (no regression)', () => {
    const sec = normalizeSection(
      {
        title: 'Variable Header',
        fields: [
          { name: 'Protocol Name Len', bits: 16 },
          { name: 'Protocol Level', bits: 8 },
          { name: 'Connect Flags', bits: 8 },
        ],
      },
      16,
    );
    expect(sec.label).toBe('Variable Header');
    expect(sec.rows.flat()).toEqual([
      ['Protocol Name Len', 16],
      ['Protocol Level', 8],
      ['Connect Flags', 8],
    ]);
  });

  it('end-to-end: the alias-only, cells+fields spec normalizes with all 6 fields (w4-15)', () => {
    const raw = {
      name: 'MQTT CONNECT (no type, aliases only)',
      bits_per_row: 16,
      sections: [
        {
          name: 'Fixed Header',
          cells: [
            { name: 'Packet Type', bits: 4 },
            { label: 'Flags', width: 4 },
            { name: 'Remaining Length', size: 8 },
          ],
        },
        {
          title: 'Variable Header',
          fields: [
            { name: 'Protocol Name Len', bits: 16 },
            { name: 'Protocol Level', bits: 8 },
            { name: 'Connect Flags', bits: 8 },
          ],
        },
      ],
    };
    const spec = normalizePacketSpec(raw as any);
    expect(spec).not.toBeNull();
    expect(spec!.sections).toHaveLength(2);
    const allFields = spec!.sections.flatMap((s) => s.rows.flat().map((t) => t[0]));
    expect(allFields).toEqual([
      'Packet Type', 'Flags', 'Remaining Length',
      'Protocol Name Len', 'Protocol Level', 'Connect Flags',
    ]);
  });
});
