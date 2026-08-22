import {
  collectDeclaredParamNames,
  dropDanglingParamConditions,
  sanitizeDanglingParamConditions,
} from '../vegaLiteParamGuard';

describe('vegaLiteParamGuard — dangling-param condition removal (Issue 32)', () => {
  describe('collectDeclaredParamNames', () => {
    it('collects names from a top-level params block', () => {
      const names = collectDeclaredParamNames({
        params: [{ name: 'brush', select: { type: 'interval' } }, { name: 'threshold', value: 0 }],
      });
      expect(names.has('brush')).toBe(true);
      expect(names.has('threshold')).toBe(true);
      expect(names.size).toBe(2);
    });

    it('collects names from nested params (repeat.spec, layer)', () => {
      const names = collectDeclaredParamNames({
        repeat: { layer: ['a'] },
        spec: {
          params: [{ name: 'inner' }],
          layer: [{ params: [{ name: 'deep' }] }],
        },
      });
      expect(names.has('inner')).toBe(true);
      expect(names.has('deep')).toBe(true);
    });

    it('ignores params entries without a string name', () => {
      const names = collectDeclaredParamNames({ params: [{ value: 5 }, { name: 42 }, {}] as any });
      expect(names.size).toBe(0);
    });
  });

  describe('dropDanglingParamConditions', () => {
    it('drops an object condition referencing an undeclared param (the exact Issue-32 trigger)', () => {
      const spec: any = {
        mark: 'bar',
        encoding: {
          color: { field: 'cat', type: 'nominal', condition: { param: 'selection_never_declared', value: 'red' } },
        },
      };
      const dropped = dropDanglingParamConditions(spec, collectDeclaredParamNames(spec));
      expect(dropped).toBe(1);
      expect(spec.encoding.color.condition).toBeUndefined();
      // Base encoding preserved.
      expect(spec.encoding.color.field).toBe('cat');
      expect(spec.encoding.color.type).toBe('nominal');
    });

    it('PRESERVES a condition whose param IS declared (guard against over-removal / catch-all)', () => {
      const spec: any = {
        params: [{ name: 'brush', select: { type: 'interval' } }],
        mark: 'bar',
        encoding: {
          opacity: { condition: { param: 'brush', value: 1 }, value: 0.1 },
        },
      };
      const dropped = dropDanglingParamConditions(spec, collectDeclaredParamNames(spec));
      expect(dropped).toBe(0);
      expect(spec.encoding.opacity.condition).toEqual({ param: 'brush', value: 1 });
    });

    it('PRESERVES a test-expression condition (no param key)', () => {
      const spec: any = {
        mark: 'bar',
        encoding: { color: { condition: { test: 'datum.v > 0', value: 'green' }, value: 'gray' } },
      };
      const dropped = dropDanglingParamConditions(spec, collectDeclaredParamNames(spec));
      expect(dropped).toBe(0);
      expect(spec.encoding.color.condition).toEqual({ test: 'datum.v > 0', value: 'green' });
    });

    it('filters only the dangling entries from an ARRAY condition, keeping declared ones', () => {
      const spec: any = {
        params: [{ name: 'ok' }],
        mark: 'point',
        encoding: {
          color: {
            condition: [
              { param: 'ok', value: 'blue' },
              { param: 'ghost', value: 'red' },
              { test: 'datum.x > 1', value: 'green' },
            ],
            value: 'gray',
          },
        },
      };
      const dropped = dropDanglingParamConditions(spec, collectDeclaredParamNames(spec));
      expect(dropped).toBe(1);
      expect(spec.encoding.color.condition).toEqual([
        { param: 'ok', value: 'blue' },
        { test: 'datum.x > 1', value: 'green' },
      ]);
    });

    it('removes the whole condition key when all array entries are dangling', () => {
      const spec: any = {
        mark: 'point',
        encoding: {
          color: { condition: [{ param: 'ghost1', value: 'a' }, { param: 'ghost2', value: 'b' }], value: 'gray' },
        },
      };
      const dropped = dropDanglingParamConditions(spec, collectDeclaredParamNames(spec));
      expect(dropped).toBe(2);
      expect(spec.encoding.color.condition).toBeUndefined();
      expect(spec.encoding.color.value).toBe('gray');
    });
  });

  describe('sanitizeDanglingParamConditions (pure, non-mutating)', () => {
    it('does not mutate the input spec', () => {
      const spec: any = {
        mark: 'bar',
        encoding: { color: { condition: { param: 'ghost', value: 'red' } } },
      };
      const before = JSON.stringify(spec);
      const { spec: out, dropped } = sanitizeDanglingParamConditions(spec);
      expect(JSON.stringify(spec)).toBe(before); // input untouched
      expect(dropped).toBe(1);
      expect(out.encoding.color.condition).toBeUndefined();
    });

    it('returns a well-formed interactive spec structurally unchanged (dropped=0)', () => {
      const spec: any = {
        params: [{ name: 'brush', select: { type: 'interval', encodings: ['x'] } }],
        mark: 'bar',
        encoding: {
          x: { field: 'cat', type: 'nominal' },
          y: { field: 'v', type: 'quantitative' },
          opacity: { condition: { param: 'brush', value: 1 }, value: 0.1 },
        },
      };
      const { spec: out, dropped } = sanitizeDanglingParamConditions(spec);
      expect(dropped).toBe(0);
      expect(out).toEqual(spec);
    });

    it('handles the full Issue-32 shape: keeps declared brush, drops undeclared color param', () => {
      const spec: any = {
        params: [
          { name: 'brush', select: { type: 'interval', encodings: ['x'] } },
          { name: 'threshold', value: 0, bind: { input: 'range', min: -1e12, max: 1e12 } },
        ],
        repeat: { layer: ['v1', 'v2', 'v3'] },
        spec: {
          mark: { type: 'bar' },
          encoding: {
            color: { field: 'cat', type: 'nominal', condition: { param: 'selection_never_declared', value: 'red' } },
            opacity: { condition: { param: 'brush', value: 1 }, value: 0.1 },
          },
        },
      };
      const { spec: out, dropped } = sanitizeDanglingParamConditions(spec);
      expect(dropped).toBe(1);
      expect(out.spec.encoding.color.condition).toBeUndefined();
      expect(out.spec.encoding.color.field).toBe('cat');
      // brush is declared → opacity condition survives.
      expect(out.spec.encoding.opacity.condition).toEqual({ param: 'brush', value: 1 });
    });

    it('tolerates non-object input', () => {
      expect(sanitizeDanglingParamConditions(null as any)).toEqual({ spec: null, dropped: 0 });
      expect(sanitizeDanglingParamConditions('str' as any)).toEqual({ spec: 'str', dropped: 0 });
    });
  });
});
