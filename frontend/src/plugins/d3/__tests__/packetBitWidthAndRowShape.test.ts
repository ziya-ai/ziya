/**
 * Regression tests for Issue 45 (packet renderer):
 *
 *  (1) PRIMARY data-loss bug — an array row whose ELEMENTS are field OBJECTS
 *      (`[{name,bits}]`, the shape LLMs emit most often) instead of tuples
 *      (`[[name,bits]]`) was returned verbatim by normalizeSectionRows, so the
 *      draw loop's `field[0]`/`field[1]` indexed to `undefined` -> every cell
 *      collapsed to a zero-width, unnamed rect (silent per-field data loss
 *      across the WHOLE diagram, incl. trivially-valid sections).
 *
 *  (2) SECONDARY ruler bug — a fractional top-level `bitWidth` (31.5) leaked
 *      into the ruler tick loop, printing "30.5 29.5 … 0.5 -0.5" (illegible,
 *      colliding, plus a spurious negative tick).
 *
 * Imports the REAL module (no re-implementation) so the test tracks shipped
 * behaviour. Each block reasons about why it FAILS against the pre-fix code.
 */
import {
  normalizeSectionRows,
  fieldToTuple,
  sanitizePacketBitWidth,
  computeDimensions,
  PACKET_DEFAULT_BIT_WIDTH,
  PACKET_MAX_BIT_WIDTH,
  type PacketSpec,
} from '../../../utils/d3Plugins/packetPlugin';

describe('Issue 45 (1): array rows with field-OBJECT elements are coerced to tuples', () => {
  it('coerces [{name,bits}] element objects to [name,bits] tuples (the data-loss trigger)', () => {
    // Pre-fix: normalizeSectionRows returned this row VERBATIM, so out[0][0]
    // was the object {name:'f1',bits:4} and out[0][0][0]/[1] were undefined ->
    // zero-width unnamed rect. The assertions below would all fail pre-fix.
    const out = normalizeSectionRows([[{ name: 'f1', bits: 4 }]]);
    expect(out).toEqual([[['f1', 4]]]);
    expect(out[0][0][0]).toBe('f1');
    expect(out[0][0][1]).toBe(4);
  });

  it('recovers every field in a multi-field object-element row', () => {
    const out = normalizeSectionRows([[
      { name: 'OverlapA', bits: 8 },
      { name: 'OverlapB', bits: -8 },
      { name: 'Frac', bits: 3.5 },
      { name: '', bits: 0 },
      { name: 'MissingBitsKey' },        // missing bits -> 0
    ]]);
    expect(out[0]).toHaveLength(5);
    expect(out[0][0]).toEqual(['OverlapA', 8]);
    expect(out[0][1]).toEqual(['OverlapB', -8]); // preserved raw; sanitizeFieldBits clamps at draw time
    expect(out[0][2]).toEqual(['Frac', 3.5]);
    expect(out[0][4]).toEqual(['MissingBitsKey', 0]);
  });

  it('honours name/label + bits/width/size + color aliases on object elements', () => {
    const out = normalizeSectionRows([[
      { label: 'aliased', width: 6, color: '#abcdef' },
      { name: 'sz', size: 2 },
    ]]);
    expect(out[0][0]).toEqual(['aliased', 6, '#abcdef']);
    expect(out[0][1]).toEqual(['sz', 2]);
  });

  // GUARD: canonical tuple rows must be untouched (returned by reference) so
  // well-formed specs stay byte-identical — proves this is a gap-fill, not a
  // catch-all that rewrites everything.
  it('returns a canonical tuple row BY REFERENCE (byte-identical)', () => {
    const row: any = [['Ver', 4], ['IHL', 4], ['TOS', 8]];
    const out = normalizeSectionRows([row]);
    expect(out[0]).toBe(row); // same reference, not a rebuilt copy
    expect(out[0]).toEqual([['Ver', 4], ['IHL', 4], ['TOS', 8]]);
  });

  // GUARD: the pre-existing object-SHAPE row ({fields:[...]}) path still works.
  it('still coerces object-shape rows {fields:[...]} and {cells:[...]}', () => {
    expect(normalizeSectionRows([{ fields: [{ name: 'a', bits: 1 }] }]))
      .toEqual([[['a', 1]]]);
    expect(normalizeSectionRows([{ cells: [['b', 2]] }]))
      .toEqual([[['b', 2]]]);
  });

  it('tolerates non-array input and unrecognisable rows', () => {
    expect(normalizeSectionRows(null as any)).toEqual([]);
    expect(normalizeSectionRows([{ nope: true }])).toEqual([[]]);
    expect(normalizeSectionRows([[]])).toEqual([[]]);
  });

  it('fieldToTuple: passes tuples through, coerces objects', () => {
    const t: any = ['x', 3];
    expect(fieldToTuple(t)).toBe(t);              // reference preserved
    expect(fieldToTuple({ name: 'y', bits: 5 })).toEqual(['y', 5]);
    expect(fieldToTuple({})).toEqual(['', 0]);
  });
});

describe('Issue 45 (2): sanitizePacketBitWidth coerces to a positive integer', () => {
  it('rounds a fractional bitWidth (the ruler trigger)', () => {
    // Pre-fix there was no coercion: 31.5 flowed straight into the ruler loop.
    expect(sanitizePacketBitWidth(31.5)).toBe(32);
    expect(sanitizePacketBitWidth(7.4)).toBe(7);
  });

  it('falls back to the default for degenerate widths', () => {
    for (const bad of [NaN, Infinity, -Infinity, 0, -8, null, undefined, 'abc', {}]) {
      expect(sanitizePacketBitWidth(bad as any)).toBe(PACKET_DEFAULT_BIT_WIDTH);
    }
  });

  it('clamps an astronomically large width to the cap', () => {
    expect(sanitizePacketBitWidth(1e9)).toBe(PACKET_MAX_BIT_WIDTH);
  });

  // GUARD: ordinary integer widths pass through unchanged (not a no-op-defeating
  // rewrite) — 8/16/32 are the common real values.
  it('leaves well-formed integer widths unchanged', () => {
    expect(sanitizePacketBitWidth(8)).toBe(8);
    expect(sanitizePacketBitWidth(16)).toBe(16);
    expect(sanitizePacketBitWidth(32)).toBe(32);
  });

  it('computeDimensions produces finite, integer-width geometry for a fractional bitWidth', () => {
    const spec: PacketSpec = {
      type: 'packet', title: 'T', bitWidth: 31.5 as any,
      sections: [{ label: 'S', rows: [[['f', 4]]] }],
    };
    const { width, height } = computeDimensions(spec);
    expect(Number.isFinite(width)).toBe(true);
    expect(Number.isFinite(height)).toBe(true);
    // bits rounds 31.5 -> 32 -> BIT_W 24; a 31.5-bit ruler would have made the
    // grid width fractional. Width must equal the width for an integer 32.
    const control = computeDimensions({ ...spec, bitWidth: 32 });
    expect(width).toBe(control.width);
  });
});
