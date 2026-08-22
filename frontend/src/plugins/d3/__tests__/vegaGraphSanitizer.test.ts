/**
 * Regression tests for Issue 34 — Vega force-link / geoshape data sanitizers.
 *
 * Imports the REAL module (frontend/src/plugins/d3/vegaGraphSanitizer.ts), not
 * a local re-implementation, so the tests detect drift in the shipped logic.
 *
 * Would these FAIL against the pre-fix code? Yes — the module did not exist
 * before this fix, so `import` itself would throw (module-not-found). The
 * behavioural assertions below additionally pin BOTH directions: dangling
 * endpoints / null geometries are removed, AND valid links / valid features /
 * transform-free specs are preserved byte-for-byte (so the sanitizer is not a
 * catch-all that would erase legitimate data).
 */
import {
  sanitizeVegaForceLinks,
  sanitizeVegaGeoshapeData,
  isRenderableGeometry,
  sanitizeVegaSpec,
} from '../vegaGraphSanitizer';

// The exact adversarial force subset from Issue 34 (index-based endpoints).
const forceSpec = () => ({
  data: [
    { name: 'nodes', values: [{ idx: 0 }, { idx: 1 }, { idx: 2 }] },
    {
      name: 'links',
      values: [
        { source: 0, target: 1 }, // valid
        { source: 1, target: 99 }, // dangling target
        { source: -1, target: 2 }, // dangling (negative) source
        { source: 2, target: 0 }, // valid
      ],
    },
  ],
  marks: [
    {
      type: 'group',
      data: [
        {
          name: 'sim_nodes',
          source: 'nodes',
          transform: [
            {
              type: 'force',
              forces: [
                { force: 'nbody', strength: -30 },
                { force: 'link', links: 'links' },
              ],
            },
          ],
        },
      ],
      marks: [{ type: 'symbol', from: { data: 'sim_nodes' } }],
    },
  ],
});

describe('sanitizeVegaForceLinks — dangling endpoints (index-based)', () => {
  it('drops links whose endpoint is not a valid node index', () => {
    const spec = forceSpec();
    const dropped = sanitizeVegaForceLinks(spec);
    expect(dropped).toBe(2);
    const links = spec.data.find((d) => d.name === 'links')!.values;
    expect(links).toEqual([
      { source: 0, target: 1 },
      { source: 2, target: 0 },
    ]);
  });

  it('preserves a graph where every endpoint is a valid index (not a catch-all)', () => {
    const spec = {
      data: [
        { name: 'nodes', values: [{ idx: 0 }, { idx: 1 }] },
        { name: 'links', values: [{ source: 0, target: 1 }, { source: 1, target: 0 }] },
      ],
      marks: [
        {
          type: 'group',
          data: [
            {
              name: 'sim',
              source: 'nodes',
              transform: [{ type: 'force', forces: [{ force: 'link', links: 'links' }] }],
            },
          ],
          marks: [],
        },
      ],
    };
    const before = JSON.parse(JSON.stringify(spec.data[1].values));
    const dropped = sanitizeVegaForceLinks(spec);
    expect(dropped).toBe(0);
    expect(spec.data[1].values).toEqual(before);
  });

  it('resolves endpoints by an id field when the link force declares `id`', () => {
    const spec = {
      data: [
        { name: 'nodes', values: [{ name: 'a' }, { name: 'b' }] },
        {
          name: 'links',
          values: [
            { source: 'a', target: 'b' }, // valid id
            { source: 'a', target: 'ghost' }, // dangling id
          ],
        },
      ],
      marks: [
        {
          type: 'group',
          data: [
            {
              name: 'sim',
              source: 'nodes',
              transform: [
                { type: 'force', forces: [{ force: 'link', links: 'links', id: 'name' }] },
              ],
            },
          ],
          marks: [],
        },
      ],
    };
    const dropped = sanitizeVegaForceLinks(spec);
    expect(dropped).toBe(1);
    expect(spec.data[1].values).toEqual([{ source: 'a', target: 'b' }]);
  });

  it('is a no-op on a spec with no force transform', () => {
    const spec = {
      data: [{ name: 'links', values: [{ source: 5, target: 99 }] }],
      marks: [{ type: 'symbol', from: { data: 'links' } }],
    };
    const before = JSON.parse(JSON.stringify(spec));
    expect(sanitizeVegaForceLinks(spec)).toBe(0);
    expect(spec).toEqual(before);
  });

  it('leaves object-shaped (already-resolved) endpoints alone', () => {
    const spec = {
      data: [
        { name: 'nodes', values: [{ idx: 0 }] },
        { name: 'links', values: [{ source: { idx: 0 }, target: { idx: 0 } }] },
      ],
      marks: [
        {
          type: 'group',
          data: [
            {
              name: 'sim',
              source: 'nodes',
              transform: [{ type: 'force', forces: [{ force: 'link', links: 'links' }] }],
            },
          ],
          marks: [],
        },
      ],
    };
    expect(sanitizeVegaForceLinks(spec)).toBe(0);
    expect(spec.data[1].values.length).toBe(1);
  });
});

describe('isRenderableGeometry', () => {
  it('accepts a polygon with coordinates', () => {
    expect(isRenderableGeometry({ type: 'Polygon', coordinates: [[[0, 0]]] })).toBe(true);
  });
  it('accepts a point with coordinates', () => {
    expect(isRenderableGeometry({ type: 'Point', coordinates: [0, 0] })).toBe(true);
  });
  it('rejects null coordinates', () => {
    expect(isRenderableGeometry({ type: 'Polygon', coordinates: null })).toBe(false);
  });
  it('rejects a null geometry', () => {
    expect(isRenderableGeometry(null)).toBe(false);
  });
  it('accepts a GeometryCollection with geometries array', () => {
    expect(isRenderableGeometry({ type: 'GeometryCollection', geometries: [] })).toBe(true);
  });
});

describe('sanitizeVegaGeoshapeData — null geometries', () => {
  const geoSpec = () => ({
    data: [
      {
        name: 'geodata',
        values: [
          { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } },
          { type: 'Feature', geometry: { type: 'Polygon', coordinates: null } }, // bad
          { type: 'Feature', geometry: null }, // bad
          { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] } },
        ],
      },
    ],
    marks: [
      {
        type: 'shape',
        from: { data: 'geodata' },
        transform: [{ type: 'geoshape', projection: 'proj' }],
      },
    ],
  });

  it('drops features with null geometry / null coordinates, keeps valid ones', () => {
    const spec = geoSpec();
    const dropped = sanitizeVegaGeoshapeData(spec);
    expect(dropped).toBe(2);
    const vals = spec.data[0].values;
    expect(vals.length).toBe(2);
    expect(vals.every((f: any) => f.geometry && f.geometry.coordinates != null)).toBe(true);
  });

  it('preserves an all-valid feature set (not a catch-all)', () => {
    const spec = {
      data: [
        {
          name: 'geodata',
          values: [
            { type: 'Feature', geometry: { type: 'Point', coordinates: [1, 2] } },
            { type: 'Feature', geometry: { type: 'Point', coordinates: [3, 4] } },
          ],
        },
      ],
      marks: [{ type: 'shape', from: { data: 'geodata' }, transform: [{ type: 'geoshape' }] }],
    };
    const before = JSON.parse(JSON.stringify(spec.data[0].values));
    expect(sanitizeVegaGeoshapeData(spec)).toBe(0);
    expect(spec.data[0].values).toEqual(before);
  });

  it('handles bare-geometry rows (not Feature-wrapped)', () => {
    const spec = {
      data: [
        {
          name: 'geodata',
          values: [
            { type: 'Polygon', coordinates: [[[0, 0]]] },
            { type: 'Polygon', coordinates: null },
          ],
        },
      ],
      marks: [{ type: 'shape', from: { data: 'geodata' }, transform: [{ type: 'geoshape' }] }],
    };
    expect(sanitizeVegaGeoshapeData(spec)).toBe(1);
    expect(spec.data[0].values.length).toBe(1);
  });

  it('is a no-op when there is no geoshape transform', () => {
    const spec = {
      data: [{ name: 'geodata', values: [{ type: 'Feature', geometry: null }] }],
      marks: [{ type: 'symbol', from: { data: 'geodata' } }],
    };
    const before = JSON.parse(JSON.stringify(spec));
    expect(sanitizeVegaGeoshapeData(spec)).toBe(0);
    expect(spec).toEqual(before);
  });
});

describe('sanitizeVegaSpec — combined, safety', () => {
  it('applies both sanitizers and returns the spec', () => {
    const spec = {
      data: [
        { name: 'nodes', values: [{ idx: 0 }, { idx: 1 }] },
        { name: 'links', values: [{ source: 0, target: 1 }, { source: 0, target: 88 }] },
        {
          name: 'geodata',
          values: [
            { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] } },
            { type: 'Feature', geometry: { type: 'Polygon', coordinates: null } },
          ],
        },
      ],
      marks: [
        {
          type: 'group',
          data: [
            {
              name: 'sim',
              source: 'nodes',
              transform: [{ type: 'force', forces: [{ force: 'link', links: 'links' }] }],
            },
          ],
          marks: [{ type: 'shape', from: { data: 'geodata' }, transform: [{ type: 'geoshape' }] }],
        },
      ],
    };
    const out = sanitizeVegaSpec(spec);
    expect(out).toBe(spec);
    expect(spec.data[1].values.length).toBe(1); // one dangling link dropped
    expect(spec.data[2].values.length).toBe(1); // one null-coord feature dropped
  });

  it('tolerates non-object / empty specs without throwing', () => {
    expect(() => sanitizeVegaSpec(null)).not.toThrow();
    expect(() => sanitizeVegaSpec({})).not.toThrow();
    expect(sanitizeVegaSpec(null)).toBe(null);
  });

  it('is idempotent', () => {
    const spec = forceSpec();
    sanitizeVegaSpec(spec);
    const afterFirst = JSON.parse(JSON.stringify(spec.data));
    sanitizeVegaSpec(spec);
    expect(spec.data).toEqual(afterFirst);
  });
});
