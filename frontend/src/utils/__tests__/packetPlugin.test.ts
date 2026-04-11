import { computeDimensions, defaultLayout, resolveColor, assignBracketDepths, normalizePacketSpec } from '../d3Plugins/packetPlugin';

describe('computeDimensions', () => {
  it('returns valid dimensions for a well-formed spec', () => {
    const spec = {
      title: 'Test Packet',
      bitWidth: 8,
      sections: [
        {
          name: 'Header',
          rows: [
            [['Field A', 4], ['Field B', 4]],
          ],
        },
      ],
    };
    const result = computeDimensions(spec as any);
    expect(result.width).toBeGreaterThan(0);
    expect(result.height).toBeGreaterThan(0);
    expect(result.layout).toBeDefined();
  });

  it('handles missing sections gracefully', () => {
    const spec = { title: 'Empty', bitWidth: 8 } as any;
    const result = computeDimensions(spec);
    expect(result.width).toBeGreaterThan(0);
    expect(result.height).toBeGreaterThanOrEqual(0);
    expect(result.layout).toBeDefined();
  });

  it('handles empty sections array', () => {
    const spec = { title: 'Empty', bitWidth: 8, sections: [] } as any;
    const result = computeDimensions(spec);
    expect(result.width).toBeGreaterThan(0);
    expect(result.layout).toBeDefined();
  });

  it('handles section with missing rows', () => {
    const spec = {
      title: 'Bad Section',
      bitWidth: 8,
      sections: [{ name: 'Broken' }],
    } as any;
    const result = computeDimensions(spec);
    expect(result.width).toBeGreaterThan(0);
    expect(result.layout).toBeDefined();
  });
});

describe('defaultLayout', () => {
  it('returns layout config for 8-bit width', () => {
    const L = defaultLayout(8);
    expect(L.BIT_W).toBeGreaterThan(0);
    expect(L.ROW_H).toBeGreaterThan(0);
  });

  it('returns layout config for 32-bit width', () => {
    const L = defaultLayout(32);
    expect(L.BIT_W).toBeGreaterThan(0);
    expect(L.BIT_W).toBeLessThanOrEqual(defaultLayout(8).BIT_W);
  });
});

describe('resolveColor', () => {
  it('resolves named theme colors in light mode', () => {
    const result = resolveColor('header', false, 0);
    expect(result.bg).toBeDefined();
    expect(result.border).toBeDefined();
    expect(result.text).toBeDefined();
  });

  it('resolves hex color strings', () => {
    const result = resolveColor('#ff0000', false, 0);
    expect(result.bg).toBe('#ff0000');
  });
});

describe('assignBracketDepths', () => {
  it('returns empty array for empty input', () => {
    expect(assignBracketDepths([], 'right')).toEqual([]);
  });
});

describe('normalizePacketSpec', () => {
  it('passes through a valid spec with sections', () => {
    const input = {
      title: 'Test',
      bitWidth: 8,
      sections: [{ label: 'Hdr', rows: [[['A', 4], ['B', 4]]] }],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.title).toBe('Test');
    expect(result!.sections).toHaveLength(1);
  });

  it('converts sections with fields but no rows', () => {
    const input = {
      title: 'Crossing Protocol',
      bitWidth: 8,
      sections: [
        {
          label: 'Header',
          fields: [
            { name: 'SYNC', bits: 4 },
            { name: 'VERSION', bits: 4 },
          ],
        },
        {
          label: 'Payload',
          fields: [
            { name: 'DATA', bits: 8 },
          ],
        },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.sections).toHaveLength(2);
    // Each section should have rows synthesized from its fields
    expect(result!.sections[0].rows.length).toBeGreaterThan(0);
    expect(result!.sections[1].rows.length).toBeGreaterThan(0);
  });

  it('gives a fallback row to sections with neither rows nor fields', () => {
    const input = {
      title: 'Minimal',
      sections: [{ label: 'Empty Section' }],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.sections[0].rows.length).toBe(1);
  });

  it('accepts "name" as alias for "title"', () => {
    const input = {
      name: 'My Frame',
      sections: [{ label: 'S', rows: [[['X', 8]]] }],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.title).toBe('My Frame');
  });

  it('accepts "width" as alias for "bitWidth"', () => {
    const input = {
      title: 'Wide',
      width: 32,
      sections: [{ label: 'S', rows: [[['X', 32]]] }],
    };
    const result = normalizePacketSpec(input);
    expect(result!.bitWidth).toBe(32);
  });

  it('converts flat fields array into sections with rows', () => {
    const input = {
      name: 'The Signal',
      width: 32,
      fields: [
        { name: 'SYNC', bits: 8, color: '#e74c3c' },
        { name: 'ORIGIN', bits: 8, color: '#3498db' },
        { name: 'SEQ', bits: 4, color: '#2ecc71' },
        { name: 'TYPE', bits: 4, color: '#f39c12' },
        { name: 'PAYLOAD', bits: 8, color: '#9b59b6' },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.title).toBe('The Signal');
    expect(result!.bitWidth).toBe(32);
    expect(result!.sections).toHaveLength(1);
    // 8+8+4+4+8 = 32, fits in one row
    expect(result!.sections[0].rows).toHaveLength(1);
    expect(result!.sections[0].rows[0]).toHaveLength(5);
  });

  it('auto-wraps flat fields across multiple rows', () => {
    const input = {
      title: 'Multi-row',
      bitWidth: 8,
      fields: [
        { name: 'A', bits: 8 },
        { name: 'B', bits: 4 },
        { name: 'C', bits: 4 },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result!.sections[0].rows).toHaveLength(2);
  });

  it('unwraps array wrapper: [{...}] -> {...}', () => {
    const input = [{
      name: 'Wrapped',
      width: 8,
      fields: [{ name: 'F', bits: 8 }],
    }];
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.title).toBe('Wrapped');
  });

  it('returns null for unrecognizable input', () => {
    expect(normalizePacketSpec(null)).toBeNull();
    expect(normalizePacketSpec({})).toBeNull();
    expect(normalizePacketSpec([])).toBeNull();
    expect(normalizePacketSpec({ title: 'No data' })).toBeNull();
    expect(normalizePacketSpec('string')).toBeNull();
  });

  it('converts a bare array of field objects into a valid spec', () => {
    // This is the format LLMs frequently produce: just an array of fields
    const input = [
      { name: 'SYNC', bits: 2, color: '#4ecdc4' },
      { name: 'PRIO', bits: 2, color: '#ff6b6b' },
      { name: 'FROM', bits: 4, color: '#ffd93d' },
      { name: 'TO', bits: 4, color: '#ffd93d' },
      { name: 'CHANNEL', bits: 4, color: '#c9e4de' },
      { name: 'MSG_TYPE', bits: 8, color: '#dcc9e4' },
      { name: 'PAYLOAD (the actual message)', bits: 32, color: '#f0f0f0' },
      { name: 'CRC', bits: 8, color: '#ffb3b3' },
    ];
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.type).toBe('packet');
    expect(result!.title).toBe('Packet Frame');
    expect(result!.sections).toHaveLength(1);
    expect(result!.sections[0].label).toBe('Frame');
    // Total bits: 2+2+4+4+4+8+32+8 = 64
    expect(result!.bitWidth).toBe(64);
    // All fields should be present across all rows
    const allFields = result!.sections[0].rows.flat();
    expect(allFields).toHaveLength(8);
    expect(allFields[0][0]).toBe('SYNC');
    expect(allFields[0][1]).toBe(2);
  });

  it('handles bare array with "label" alias for field name', () => {
    const input = [
      { label: 'Start', bits: 4 },
      { label: 'End', bits: 4 },
    ];
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.sections[0].rows.flat()[0][0]).toBe('Start');
    expect(result!.bitWidth).toBe(8);
  });

  it('accepts "bits_per_row" as alias for "bitWidth"', () => {
    const input = {
      title: 'BitsPerRow',
      bits_per_row: 32,
      fields: [{ name: 'A', bits: 16 }, { name: 'B', bits: 16 }],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.bitWidth).toBe(32);
  });

  it('converts index-based sections + flat fields into row-based sections', () => {
    // This is the format from the TUBP bug report: fields + sections with {start, end}
    const input = {
      title: 'TUBP v1',
      bits_per_row: 32,
      fields: [
        { name: 'VER', bits: 4, color: '#1a1a4a' },
        { name: 'SRC', bits: 12, color: '#1a3a1a' },
        { name: 'DST', bits: 12, color: '#3a1a1a' },
        { name: 'HOP', bits: 4, color: '#4a3a00' },
        { name: 'TYPE', bits: 8, color: '#4a004a' },
        { name: 'DX', bits: 24, color: '#006666' },
      ],
      sections: [
        { label: 'HEADER', start: 0, end: 3 },
        { label: 'ROUTING', start: 3, end: 5 },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.bitWidth).toBe(32);
    expect(result!.sections).toHaveLength(2);
    // Every section must have rows (the bug was rows being undefined)
    for (const sec of result!.sections) {
      expect(sec.rows).toBeDefined();
      expect(sec.rows.length).toBeGreaterThan(0);
    }
    expect(result!.sections[0].label).toBe('HEADER');
    expect(result!.sections[1].label).toBe('ROUTING');
  });

  it('converts top-level brackets from field-index to row-index in index-based sections', () => {
    const input = {
      title: 'With Brackets',
      bits_per_row: 8,
      fields: [
        { name: 'A', bits: 8 },
        { name: 'B', bits: 8 },
        { name: 'C', bits: 8 },
        { name: 'D', bits: 8 },
      ],
      sections: [
        { label: 'SEC1', start: 0, end: 1 },
        { label: 'SEC2', start: 2, end: 3 },
      ],
      brackets: [
        { label: 'Span A-C', start: 0, end: 2, side: 'right' },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    // Bracket overlaps both sections
    const sec1Brackets = result!.sections[0].brackets ?? [];
    const sec2Brackets = result!.sections[1].brackets ?? [];
    // At least one section should have the bracket attached
    const totalBrackets = sec1Brackets.length + sec2Brackets.length;
    expect(totalBrackets).toBeGreaterThanOrEqual(1);
  });

  it('converts top-level brackets in flat-fields-only spec (no index sections)', () => {
    const input = {
      title: 'Flat with Brackets',
      bitWidth: 8,
      fields: [
        { name: 'A', bits: 8 },
        { name: 'B', bits: 8 },
        { name: 'C', bits: 8 },
      ],
      brackets: [
        { label: 'Encrypted', start: 0, end: 2, side: 'right' },
        { label: 'Signed', start: 1, end: 2, side: 'left' },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.sections).toHaveLength(1);
    const brackets = result!.sections[0].brackets;
    expect(brackets).toBeDefined();
    expect(brackets!.length).toBe(2);
    expect(brackets![0].label).toBe('Encrypted');
    expect(brackets![0].start_row).toBe(0);
    expect(brackets![0].end_row).toBe(2);
    expect(brackets![1].side).toBe('left');
  });

  it('handles the full TUBP spec from the bug report without crashing', () => {
    // Exact spec that triggered "Cannot read properties of undefined (reading 'length')"
    const input = {
      title: 'Trans-Universal Bridge Protocol (TUBP v1)',
      bits_per_row: 32,
      fields: [
        { name: 'TUBP VER', bits: 4, color: '#1a1a4a' },
        { name: 'SRC UNIVERSE', bits: 12, color: '#1a3a1a' },
        { name: 'DST UNIVERSE', bits: 12, color: '#3a1a1a' },
        { name: 'HOP', bits: 4, color: '#4a3a00' },
        { name: 'CONSCIOUSNESS PAYLOAD TYPE', bits: 8, color: '#4a004a' },
        { name: 'DIMENSIONAL OFFSET X', bits: 24, color: '#006666' },
        { name: 'DIMENSIONAL OFFSET Y', bits: 16, color: '#006666' },
        { name: 'DIMENSIONAL OFFSET Z', bits: 16, color: '#006666' },
        { name: 'ENTROPY STATE VECTOR', bits: 64, color: '#cc4400' },
        { name: 'CONSCIOUSNESS ENCODING', bits: 128, color: '#6633cc' },
        { name: 'ANTI-ENTROPY SEED', bits: 32, color: '#228b22' },
        { name: 'TEMPORAL SYNC TOKEN', bits: 32, color: '#4682b4' },
        { name: 'FOLD-SPACE COORDINATES', bits: 96, color: '#8B6914' },
        { name: 'INTEGRITY CHECK', bits: 32, color: '#333333' },
        { name: 'QUANTUM ENTANGLE ID', bits: 16, color: '#cc0066' },
        { name: 'PAD', bits: 16, color: '#1a1a1a' },
      ],
      sections: [
        { label: 'HEADER', start: 0, end: 3 },
        { label: 'ROUTING', start: 3, end: 6 },
        { label: 'DIMENSIONAL', start: 6, end: 8 },
        { label: 'CONSCIOUSNESS', start: 8, end: 10 },
        { label: 'NAVIGATION', start: 10, end: 13 },
        { label: 'VERIFICATION', start: 13, end: 15 },
      ],
      brackets: [
        { label: 'Encrypted with universal-key', start: 3, end: 13, side: 'right' },
        { label: 'Quantum-entangled region', start: 8, end: 14, side: 'left' },
        { label: 'Fold-space navigable', start: 6, end: 12, side: 'right' },
      ],
    };
    const result = normalizePacketSpec(input);
    expect(result).not.toBeNull();
    expect(result!.title).toBe('Trans-Universal Bridge Protocol (TUBP v1)');
    expect(result!.bitWidth).toBe(32);
    expect(result!.sections).toHaveLength(6);
    // Every section must have valid rows
    for (const sec of result!.sections) {
      expect(sec.rows).toBeDefined();
      expect(Array.isArray(sec.rows)).toBe(true);
      expect(sec.rows.length).toBeGreaterThan(0);
    }
    // At least some sections should have brackets
    const totalBrackets = result!.sections.reduce((n, s) => n + (s.brackets?.length ?? 0), 0);
    expect(totalBrackets).toBeGreaterThan(0);
  });
});
