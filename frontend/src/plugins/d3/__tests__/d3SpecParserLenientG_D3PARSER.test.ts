/**
 * D-250 (group G-D3PARSER): the shared d3SpecParser lenient recovery stage.
 *
 * Before the fix, parseD3Spec only did strict JSON.parse + a weak regex
 * (jsObjectToJson) and resolveMusicSpec used a bare JSON.parse behind a
 * first-char '{' guard. Malformed-but-recoverable specs a model realistically
 * emits (markdown fence + prose, smart/curly quotes, Python True/False/None,
 * a `const x = {...};` wrapper, semicolon separators) therefore never parsed,
 * no plugin claimed them, and the host retried to a 30s timeout with zero
 * output.
 *
 * Each dialect below returned `null` (parseD3Spec) or an unrecovered wrapper
 * (resolveMusicSpec) under the pre-fix source, so every `not.toBeNull()` /
 * `not.toBe(wrapper)` assertion here FAILS without the lenientParse change and
 * passes with it. The two guard cases at the end assert the fast path and the
 * garbage-rejection are unchanged (targeted fix, no behaviour drift).
 *
 * Theme-independent by nature: parsing precedes any theming, so the recovery
 * is identical in light and dark (this defect carries themes_affected
 * [light,dark] purely because the resulting 30s blank affected both).
 */
import { parseD3Spec, lenientParse } from '../../../utils/d3SpecParser';
import { resolveMusicSpec } from '../../../utils/d3Plugins/musicPlugin';

describe('D-250 basic-chart / d3: lenient dialect recovery via parseD3Spec', () => {
  it('recovers a spec wrapped in a ```json fence with a prose line (w4-04)', () => {
    const raw =
      'Here is the chart:\n```json\n{\n  "type": "bar",\n  "data": [\n' +
      '    {"label": "Alpha", "value": 30},\n' +
      '    {"label": "Beta", "value": 55}\n  ]\n}\n```';
    const r = parseD3Spec(raw);
    expect(r).not.toBeNull();
    expect(r.type).toBe('bar');
    expect(r.data).toHaveLength(2);
    expect(r.data[0].label).toBe('Alpha');
  });

  it('recovers typographic/smart double quotes around keys and values (w4-05)', () => {
    const raw =
      '{\n  \u201Ctype\u201D: \u201Cbar\u201D,\n  \u201Cdata\u201D: [\n' +
      '    {\u201Clabel\u201D: \u201CAlpha\u201D, \u201Cvalue\u201D: 30}\n  ]\n}';
    const r = parseD3Spec(raw);
    expect(r).not.toBeNull();
    expect(r.type).toBe('bar');
    expect(r.data[0].label).toBe('Alpha');
  });

  it('recovers Python repr leakage: True/False/None + single quotes (w4-07)', () => {
    const raw =
      "{\n  'type': 'bar',\n  'stacked': False,\n  'title': None,\n" +
      "  'animate': True,\n  'data': [\n    {'label': 'Alpha', 'value': 30, 'color': None}\n  ]\n}";
    const r = parseD3Spec(raw);
    expect(r).not.toBeNull();
    expect(r.type).toBe('bar');
    expect(r.stacked).toBe(false);
    expect(r.title).toBeNull();
    expect(r.animate).toBe(true);
    expect(r.data[0].color).toBeNull();
  });

  it('recovers a JS assignment wrapper with a trailing semicolon (w4-08)', () => {
    const raw =
      'const chart = {\n  type: "bar",\n  data: [\n' +
      '    {label: "Alpha", value: 30}\n  ]\n};';
    const r = parseD3Spec(raw);
    expect(r).not.toBeNull();
    expect(r.type).toBe('bar');
    expect(r.data[0].value).toBe(30);
  });

  it('recovers the kitchen-sink: fence + unquoted keys + trailing comma + smart-quoted label (w4-15)', () => {
    const raw =
      "```chart\n{\n  type: 'bar',\n  data: [\n" +
      "    {label: \u2018Alpha\u2019, value: \"30\", color: '#f90',},\n" +
      "    {label: 'Beta', value: \"55\", color: '#09c',},\n  ],\n}\n```";
    const r = parseD3Spec(raw);
    expect(r).not.toBeNull();
    expect(r.type).toBe('bar');
    expect(r.data).toHaveLength(2);
    expect(r.data[0].label).toBe('Alpha');
    expect(r.data[0].color).toBe('#f90');
  });

  // Guard: the strict fast path is preserved (well-formed spec unchanged).
  it('leaves a well-formed strict-JSON spec unchanged (fast path)', () => {
    const r = parseD3Spec('{ "type": "force-directed", "width": 700 }');
    expect(r).toEqual({ type: 'force-directed', width: 700 });
  });

  // Guard: genuinely unparseable input still yields null (no over-eager claim).
  it('returns null for unparseable garbage', () => {
    expect(parseD3Spec('not valid at all {{')).toBeNull();
    expect(lenientParse('')).toBeNull();
  });
});

describe('D-250 music: lenient dialect recovery via resolveMusicSpec', () => {
  const wrap = (definition: string) => ({ type: 'music', definition, theme: 'light' });

  it('recovers semicolon separators where JSON needs commas (w4-07)', () => {
    const raw =
      '{\n  "timeSignature": "4/4";\n  "clef": "treble";\n  "title": "Semicolon Separators";\n' +
      '  "notes": [\n    {"keys": ["a/4"], "duration": "q"};\n' +
      '    {"keys": ["c/5"], "duration": "q"};\n' +
      '    {"keys": ["e/5"], "duration": "h"}\n  ]\n}';
    const w = wrap(raw);
    const r = resolveMusicSpec(w);
    expect(r).not.toBe(w);
    expect(r.type).toBe('music');
    expect(r.notes).toHaveLength(3);
    expect(r.timeSignature).toBe('4/4');
  });

  it('recovers typographic curly quotes throughout (w4-06)', () => {
    const raw =
      '{\n  \u201CtimeSignature\u201D: \u201C4/4\u201D,\n  \u201Cclef\u201D: \u201Ctreble\u201D,\n' +
      '  \u201Cnotes\u201D: [\n    {\u201Ckeys\u201D: [\u201Ce/4\u201D], \u201Cduration\u201D: \u201Cq\u201D}\n  ]\n}';
    const w = wrap(raw);
    const r = resolveMusicSpec(w);
    expect(r).not.toBe(w);
    expect(r.type).toBe('music');
    expect(r.notes[0].keys).toEqual(['e/4']);
  });

  it('recovers every key AND value single-quoted (w4-03)', () => {
    const raw =
      "{'timeSignature': '3/4', 'clef': 'bass', 'notes': [{'keys': ['f/3'], 'duration': 'q'}]}";
    const w = wrap(raw);
    const r = resolveMusicSpec(w);
    expect(r).not.toBe(w);
    expect(r.type).toBe('music');
    expect(r.clef).toBe('bass');
    expect(r.notes[0].keys).toEqual(['f/3']);
  });

  // Guard: a non-music spec that happens to parse is NOT hijacked.
  it('does not claim a parsed spec that carries no music content', () => {
    const w = wrap('{ "type": "bar", "data": [ {"label": "x", "value": 1} ] }');
    const r = resolveMusicSpec(w);
    expect(r).toBe(w);
  });
});
