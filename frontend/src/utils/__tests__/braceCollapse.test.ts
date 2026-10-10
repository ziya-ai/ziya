/**
 * Shared recovery for brace-escaped JSON specs (`{{` / `}}` for `{` / `}`),
 * which models carry over from Python f-strings and templates.
 *
 * Fails pre-fix: collapseUniformDoubleBraces / withBraceCollapse did not
 * exist, and every JSON-bodied renderer's parser rejected escaped input
 * (returned null/undefined, or threw).
 */
import JSON5 from 'json5';
import {
  collapseUniformDoubleBraces,
  withBraceCollapse,
  lenientParse,
  parseD3Spec,
  stripDefinitionFence,
  normalizeSmartQuotes,
} from '../d3SpecParser';
import {
  lenientParsePacketJson,
  stripPacketFence,
  normalizePacketSmartQuotes,
} from '../../plugins/d3/packetPlugin';
import {
  lenientParseObject,
  stripDefinitionFence as fdStripFence,
  normalizeSmartQuotes as fdSmartQuotes,
} from '../../plugins/d3/forceDirectedPlugin';
import {
  lenientParseNetworkObject,
  stripNetworkFence,
  normalizeNetworkSmartQuotes,
} from '../../plugins/d3/networkDiagram';
import { tolerantParseVegaSpec } from '../../plugins/d3/vegaRecovery';
import {
  parsePlotlyDefinition,
  stripPlotlyFence,
  normalizePlotlySmartQuotes,
} from '../../plugins/d3/plotlyPreprocessor';
import {
  parseJointJsonish,
  stripJointFence,
  normalizeJointSmartQuotes,
} from '../../plugins/d3/jointPlugin';
import { parseFlamegraphInput } from '../d3Plugins/flamegraphPlugin';
import { parseWaveJson } from '../d3Plugins/wavedromPlugin';
import {
  lenientJsonParse as railroadLenientJsonParse,
  renderRailroadSvg,
} from '../d3Plugins/railroadPlugin';
import { lenientJsonParse as timelineLenientJsonParse } from '../d3Plugins/timelinePlugin';

const CHART_ESCAPED = '{{"mark": "bar", "encoding": {{"x": {{"field": "a"}}}}}}';
const CHART = { mark: 'bar', encoding: { x: { field: 'a' } } };

describe('collapseUniformDoubleBraces', () => {
  it('collapses an escaped body, including braces inside strings', () => {
    expect(collapseUniformDoubleBraces('{{"a": "{{}}"}}')).toBe('{"a": "{}"}');
  });

  it('keeps an array wrapper around escaped objects', () => {
    expect(collapseUniformDoubleBraces('[{{"a": 1}}, {{"b": 2}}]')).toBe('[{"a": 1}, {"b": 2}]');
  });

  it('returns null for valid JSON (even with nested }}) and non-escaped text', () => {
    expect(collapseUniformDoubleBraces('{"a":{"b":{}}}')).toBeNull();
    expect(collapseUniformDoubleBraces('')).toBeNull();
    expect(collapseUniformDoubleBraces('no braces here')).toBeNull();
  });
});

describe('withBraceCollapse', () => {
  const tryJson = (calls: string[]) => (s: string) => {
    calls.push(s);
    try { return JSON.parse(s); } catch (_e) { return null; }
  };

  it('does not retry when the first parse succeeds', () => {
    const calls: string[] = [];
    expect(withBraceCollapse(tryJson(calls), '{"a": 1}')).toEqual({ a: 1 });
    expect(calls).toHaveLength(1);
  });

  it('retries once, through the same parser, on the collapsed body', () => {
    const calls: string[] = [];
    expect(withBraceCollapse(tryJson(calls), '{{"a": 1}}')).toEqual({ a: 1 });
    expect(calls).toEqual(['{{"a": 1}}', '{"a": 1}']);
  });

  it('returns the original failure without retrying when input is not escaped', () => {
    const calls: string[] = [];
    expect(withBraceCollapse(tryJson(calls), '{"a": oops')).toBeNull();
    expect(calls).toHaveLength(1);
  });

  it('supports throwing parsers: retries on throw, rethrows the ORIGINAL error', () => {
    expect(withBraceCollapse(JSON.parse, '{{"a": 1}}')).toEqual({ a: 1 });
    let original: unknown;
    try { JSON.parse('{"a": oops'); } catch (e) { original = e; }
    expect(() => withBraceCollapse(JSON.parse, '{"a": oops')).toThrow((original as Error).message);
    // Escaped but still broken after collapse: the first error surfaces.
    let firstErr: unknown;
    try { JSON.parse('{{"a": oops}}'); } catch (e) { firstErr = e; }
    expect(() => withBraceCollapse(JSON.parse, '{{"a": oops}}')).toThrow((firstErr as Error).message);
  });
});

describe('shared lenientParse / parseD3Spec', () => {
  it('control: strict JSON rejects the escaped spec', () => {
    expect(() => JSON.parse(CHART_ESCAPED)).toThrow();
  });

  it('lenientParse recovers an escaped spec', () => {
    expect(lenientParse(CHART_ESCAPED)).toEqual(CHART);
  });

  it("the shared parser's own repairs still apply after the collapse", () => {
    expect(lenientParse("{{'a': True, 'b': None}}")).toEqual({ a: true, b: null });
  });

  it('parseD3Spec (the D3Renderer entry point) recovers an escaped spec', () => {
    expect(parseD3Spec(CHART_ESCAPED)).toEqual(CHART);
  });

  it('valid input is parsed unchanged', () => {
    const raw = '{"a":{"b":{}}}';
    expect(lenientParse(raw)).toEqual(JSON.parse(raw));
  });
});

// SEAM: every JSON-bodied renderer parses its own `definition` string, so the
// shared parser alone does not cover them. Each must route through
// withBraceCollapse. A parser added later without the wrapper fails here.
describe('every JSON-bodied renderer parser recovers brace-escaped input', () => {
  const ESCAPED = '{{"name": "root", "value": 3, "children": [{{"name": "a", "value": 1}}]}}';
  const EXPECTED = { name: 'root', value: 3, children: [{ name: 'a', value: 1 }] };

  const parsers: Array<[string, (s: string) => any]> = [
    ['shared lenientParse (d3 / basic-chart / music)', lenientParse],
    ['packet lenientParsePacketJson', lenientParsePacketJson],
    ['force-directed / chord lenientParseObject', lenientParseObject],
    ['network lenientParseNetworkObject', lenientParseNetworkObject],
    ['vega / vega-lite tolerantParseVegaSpec', tolerantParseVegaSpec],
    ['plotly parsePlotlyDefinition', parsePlotlyDefinition],
    ['joint parseJointJsonish', parseJointJsonish],
    ['flamegraph parseFlamegraphInput', (s) => parseFlamegraphInput(s).root],
    ['wavedrom parseWaveJson', parseWaveJson],
    ['railroad lenientJsonParse', railroadLenientJsonParse],
    ['timeline lenientJsonParse', timelineLenientJsonParse],
  ];

  it('control: JSON5 alone rejects the escaped input', () => {
    expect(() => JSON5.parse(ESCAPED)).toThrow();
  });

  it.each(parsers)('%s', (_name, parse) => {
    expect(parse(ESCAPED)).toEqual(EXPECTED);
  });

  it.each(parsers)('%s: valid input is unchanged', (_name, parse) => {
    expect(parse(JSON.stringify(EXPECTED))).toEqual(EXPECTED);
  });

  it('railroad renders the brace-escaped grammar a model emitted (seam)', () => {
    // Verbatim shape of a failing chat render: every brace doubled, nested
    // rules, a string containing an escaped quote.
    const spec = '{{"title": "q", "rules": [{{"name": "src", "diagram": ' +
      '{{"choice": [{{"terminal": "\\""}}, {{"nonterminal": "ns"}}]}}}}]}}';
    const out = renderRailroadSvg(spec, false);
    expect(out.title).toBe('q');
    expect(out.rules.map(r => r.name)).toEqual(['src']);
    expect(out.rules[0].svg).toContain('ns');
  });

  it('railroad streaming probe stays undefined on a partial escaped spec', () => {
    expect(railroadLenientJsonParse('{{"title": "q", "diagram": [{{"terminal": "a"')).toBeUndefined();
  });

  it('joint keeps an escaped top-level cell array', () => {
    expect(parseJointJsonish('[{{"id": "a"}}, {{"id": "b"}}]')).toEqual([{ id: 'a' }, { id: 'b' }]);
  });
});

// Duplication cleanup: per-plugin fence / smart-quote helpers now delegate to
// the d3SpecParser implementations instead of carrying copies.
describe('per-plugin fence and smart-quote helpers share one implementation', () => {
  it('aliases are the shared functions', () => {
    for (const f of [stripPacketFence, fdStripFence, stripNetworkFence, stripPlotlyFence]) {
      expect(f).toBe(stripDefinitionFence);
    }
    for (const f of [normalizePacketSmartQuotes, fdSmartQuotes, normalizeNetworkSmartQuotes, normalizePlotlySmartQuotes]) {
      expect(f).toBe(normalizeSmartQuotes);
    }
  });

  it('joint wrappers keep their null-safety and match the shared output', () => {
    const fenced = '```json\n{\u201Ca\u201D: 1}\n```';
    expect(stripJointFence(fenced)).toBe(stripDefinitionFence(fenced));
    expect(normalizeJointSmartQuotes(fenced)).toBe(normalizeSmartQuotes(fenced));
    expect(stripJointFence(undefined as unknown as string)).toBe('');
    expect(normalizeJointSmartQuotes(undefined as unknown as string)).toBe('');
  });
});

describe('packet plugin uses the shared recovery (seam)', () => {
  it('lenientParsePacketJson and lenientParse agree on an escaped spec', () => {
    const raw = '{{"title": "T", "sections": [{{"label": "x", "rows": []}}]}}';
    const expected = { title: 'T', sections: [{ label: 'x', rows: [] }] };
    expect(lenientParsePacketJson(raw)).toEqual(expected);
    expect(lenientParse(raw)).toEqual(expected);
  });
});
