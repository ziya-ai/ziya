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
});
