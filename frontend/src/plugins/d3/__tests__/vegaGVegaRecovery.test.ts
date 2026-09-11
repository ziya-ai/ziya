import {
  rewriteVegaV2Dialect,
  sanitizeVegaSchemes,
  buildVegaEmbedOptions,
  rewriteMethodCallsInExpr,
} from '../vegaPlugin';
import { tolerantParseVegaSpec } from '../vegaRecovery';

/**
 * G-VEGA-RECOVERY — vegaPlugin.ts native-path recovery.
 *
 * Defects (all vega engine, both themes unless noted):
 *   D-223 v5 method-call / let() rewrite — CONFIRMED already-remediated (the
 *         D-279 dotted-member fix). Regression guard: the dotted LHS is kept
 *         whole (`datum.name.slice(...)` -> slice(datum.name, ...)); the pre-fix
 *         walk stopped at the '.', yielding slice(name, ...), so this guard
 *         FAILS on unpatched (pre-D-279) code.
 *   D-228 json syntax — CONFIRMED already-remediated. tolerantParseVegaSpec
 *         recovers all six near-miss forms (trailing comma, unquoted keys,
 *         single quotes, ```json fence, smart quotes, comments+';'); each throws
 *         a plain JSON.parse.
 *   D-230 v2 dialect (w4-10) — FIX. rewriteVegaV2Dialect now also converts a v2
 *         `ordinal`+`points` positional scale to band/point and a boolean
 *         `band:true` encode channel to `1`. Without it the bars render
 *         zero-width (invisible). Guards fail on unpatched code.
 *   D-231 unknown scheme (w4-14) — FIX. sanitizeVegaSchemes now DELETES the
 *         range/scale container it emptied, so the scale falls back to Vega's
 *         default scheme; leaving `range:{}` was itself fatal. Guard asserts the
 *         key is gone, which fails on unpatched code (it left `{}`).
 *   D-229 VL body under Vega envelope (w4-07) — FIX. buildVegaEmbedOptions
 *         accepts a `mode`, and render compiles a Vega-Lite body in
 *         'vega-lite' mode. Guard asserts the mode plumbing.
 */

describe('D-223 v5 method-call rewrite keeps dotted member paths whole', () => {
  it('rewrites datum.name.slice(0,5).toUpperCase() to nested v6 calls', () => {
    expect(rewriteMethodCallsInExpr('datum.name.slice(0, 5).toUpperCase()'))
      .toBe('upper(slice(datum.name, 0, 5))');
  });
  it("rewrites join([...], ' ').toUpperCase()", () => {
    expect(rewriteMethodCallsInExpr("join(['SLO', 'breach', 'at', threshold], ' ').toUpperCase()"))
      .toBe("upper(join(['SLO', 'breach', 'at', threshold], ' '))");
  });
});

describe('D-228 tolerant parse recovers near-miss JSON', () => {
  const cases: Record<string, string> = {
    'trailing commas': '{"$schema":"https://vega.github.io/schema/vega/v5.json","marks":[{"type":"rect",}],}',
    'unquoted keys': '{$schema:"https://vega.github.io/schema/vega/v5.json",marks:[{type:"rect"}]}',
    'single quotes': "{'$schema':'https://vega.github.io/schema/vega/v5.json','marks':[{'type':'rect'}]}",
    'json fence': '```json\n{"$schema":"https://vega.github.io/schema/vega/v5.json","marks":[{"type":"rect"}]}\n```',
    'smart quotes': '{\u201c$schema\u201d:\u201chttps://vega.github.io/schema/vega/v5.json\u201d,\u201cmarks\u201d:[{\u201ctype\u201d:\u201crect\u201d}]}',
    'comments + semicolon': '{\n// c\n"$schema":"https://vega.github.io/schema/vega/v5.json";\n"marks":[{"type":"rect"}]\n}',
  };
  for (const [name, raw] of Object.entries(cases)) {
    it(`recovers ${name} (plain JSON.parse throws)`, () => {
      expect(() => JSON.parse(raw)).toThrow();
      const parsed = tolerantParseVegaSpec(raw);
      expect(Array.isArray(parsed.marks)).toBe(true);
      expect(parsed.marks).toHaveLength(1);
    });
  }
});

describe('D-230 rewriteVegaV2Dialect converts v2 ordinal/band shapes', () => {
  it('ordinal+points:false positional scale becomes a band scale', () => {
    const spec: any = {
      scales: [{ name: 'x', type: 'ordinal', range: 'width', points: false }],
      marks: [{
        type: 'rect', from: { data: 't' },
        properties: { update: { x: { scale: 'x', field: 'c' }, width: { scale: 'x', band: true } } },
      }],
    };
    const out = rewriteVegaV2Dialect(spec);
    expect(out.scales[0].type).toBe('band');
    expect('points' in out.scales[0]).toBe(false);
    // properties -> encode, and boolean band -> 1
    expect(out.marks[0].encode).toBeDefined();
    expect(out.marks[0].properties).toBeUndefined();
    expect(out.marks[0].encode.update.width.band).toBe(1);
  });
  it('ordinal+points:true becomes a point scale', () => {
    const out = rewriteVegaV2Dialect({ scales: [{ name: 'x', type: 'ordinal', points: true }] } as any);
    expect(out.scales[0].type).toBe('point');
  });
  it('leaves a modern v5 spec unchanged (no points, numeric band)', () => {
    const spec: any = {
      scales: [{ name: 'x', type: 'band' }, { name: 'col', type: 'ordinal', range: { scheme: 'category10' } }],
      marks: [{ type: 'rect', encode: { update: { width: { scale: 'x', band: 1 } } } }],
    };
    const out = rewriteVegaV2Dialect(JSON.parse(JSON.stringify(spec)));
    expect(out).toEqual(spec);
  });
});

describe('D-231 sanitizeVegaSchemes drops unknown scheme AND the emptied range', () => {
  it('deletes range entirely when scheme was its only key (default scheme applies)', () => {
    const spec: any = {
      scales: [{ name: 'col', type: 'ordinal', domain: { data: 't', field: 'c' }, range: { scheme: 'ziyaDark' } }],
    };
    const dropped = sanitizeVegaSchemes(spec);
    expect(dropped).toBe(1);
    // Not merely `range:{}` (which is itself a fatal Vega error): the key is gone.
    expect('range' in spec.scales[0]).toBe(false);
  });
  it('preserves an unrelated range key alongside the dropped scheme', () => {
    const spec: any = { scales: [{ name: 'col', type: 'ordinal', range: { scheme: 'ziyaDark', reverse: true } }] };
    sanitizeVegaSchemes(spec);
    expect(spec.scales[0].range).toEqual({ reverse: true });
  });
  it('leaves a known scheme and a #hex range untouched', () => {
    const spec: any = {
      scales: [
        { name: 'a', type: 'ordinal', range: { scheme: 'category10' } },
        { name: 'b', type: 'linear', range: { scheme: '#ff0000' } },
      ],
    };
    expect(sanitizeVegaSchemes(spec)).toBe(0);
    expect(spec.scales[0].range).toEqual({ scheme: 'category10' });
    expect(spec.scales[1].range).toEqual({ scheme: '#ff0000' });
  });
});

describe('D-229 buildVegaEmbedOptions carries the compile mode', () => {
  it('defaults to vega mode', () => {
    expect(buildVegaEmbedOptions(false).mode).toBe('vega');
    expect(buildVegaEmbedOptions(true).mode).toBe('vega');
  });
  it('compiles a Vega-Lite body in vega-lite mode (both themes)', () => {
    expect(buildVegaEmbedOptions(false, 'vega-lite').mode).toBe('vega-lite');
    expect(buildVegaEmbedOptions(true, 'vega-lite').mode).toBe('vega-lite');
  });
});
