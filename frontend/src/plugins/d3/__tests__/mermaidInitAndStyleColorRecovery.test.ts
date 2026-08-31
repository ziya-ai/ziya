/**
 * G-09 recovery regression tests for the mermaid enhancer.
 *
 * D-037 (init-json-palette-dropped): a near-miss `%%{init}%%` themeVariables
 *   JSON — a single trailing comma (w4-06), or unquoted keys + single-quoted
 *   values + a string-typed fontSize (w4-07) — is rejected by mermaid's strict
 *   directive parser, so the requested palette is silently dropped and the
 *   diagram renders as default lavender. The fix repairs the JSON leniently.
 *
 * D-038 (style-rgba-color-form-parse-error): `style A fill:rgba(74,144,217,
 *   0.85),...` (w4-09) carries commas INSIDE the rgba() function; mermaid's
 *   style grammar splits properties on commas, fracturing the directive into a
 *   parse error. The fix converts the rgb()/rgba() function to a comma-free
 *   #hex value (named CSS colors are left alone — mermaid accepts them).
 *
 * Both defects are kind:recovery and theme-independent (the repair happens in
 * the text preprocessor, which has no theme input), so the "both themes"
 * obligation is discharged at the shared render stage, not here.
 *
 * DIRECTION: every "fixed" assertion is paired with a check that the RAW input
 * genuinely needs the repair (its init body is not JSON.parse-able / it still
 * contains `rgba(`), so each test fails against the unpatched preprocessor.
 */

import {
  preprocessDefinition,
  initMermaidEnhancer,
  normalizeInitDirectiveJson,
  repairInitDirectives,
  convertStyleColorFunctionsToHex,
  remediateStyleFillTextContrast,
  resolveStyleColorToRgb,
  contrastRatioRgb,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const initBodyOf = (def: string): string => {
  const m = def.match(/%%\{\s*init\s*:\s*([\s\S]*?)\}%%/i);
  return m ? m[1] : '';
};

describe('D-037: near-miss %%{init}%% directive JSON is repaired', () => {
  it('w4-06: strips a trailing comma and keeps the palette', () => {
    const raw =
      '%%{init: {"theme":"base","themeVariables":{"primaryColor":"#2e7d32","primaryTextColor":"#ffffff",}}}%%\n' +
      'flowchart LR\n  A[Green] --> B[Nodes]';

    // Direction: the raw init body is NOT valid JSON, so the unpatched pipeline
    // (which never normalizes it) leaves mermaid to reject it.
    expect(() => JSON.parse(initBodyOf(raw))).toThrow();

    const out = preprocessDefinition(raw, 'flowchart');
    const body = initBodyOf(out);
    // The repaired directive is now strict-parseable...
    const obj = JSON.parse(body);
    // ...and the requested palette survived verbatim.
    expect(obj.theme).toBe('base');
    expect(obj.themeVariables.primaryColor).toBe('#2e7d32');
    expect(obj.themeVariables.primaryTextColor).toBe('#ffffff');
    // No trailing comma remains.
    expect(body).not.toMatch(/,\s*[}\]]/);
  });

  it('w4-07: quotes bare keys, converts single quotes, keeps string fontSize', () => {
    const raw =
      "%%{init: {theme:'base', themeVariables:{primaryColor:'#c62828', primaryTextColor:'#ffffff', fontSize:'18'}}}%%\n" +
      'flowchart LR\n  A[Red] --> B[Theme]';

    expect(() => JSON.parse(initBodyOf(raw))).toThrow();

    const out = preprocessDefinition(raw, 'flowchart');
    const obj = JSON.parse(initBodyOf(out));
    expect(obj.theme).toBe('base');
    expect(obj.themeVariables.primaryColor).toBe('#c62828');
    expect(obj.themeVariables.fontSize).toBe('18');
    // No single quotes remain in the directive.
    expect(initBodyOf(out)).not.toContain("'");
  });

  it('leaves an already-valid init directive parse-equivalent (idempotent)', () => {
    const valid = '%%{init: {"theme":"dark"}}%%\nflowchart LR\n  A --> B';
    const once = preprocessDefinition(valid, 'flowchart');
    const twice = preprocessDefinition(once, 'flowchart');
    expect(JSON.parse(initBodyOf(once))).toEqual({ theme: 'dark' });
    expect(initBodyOf(twice)).toBe(initBodyOf(once));
  });

  it('helper: unrepairable body returns null (caller must leave it untouched)', () => {
    expect(normalizeInitDirectiveJson('{not : : json')).toBeNull();
    // A bad directive is passed through unchanged rather than mangled.
    const junk = '%%{init: {this is not json}}%%\nflowchart LR\n A-->B';
    expect(repairInitDirectives(junk)).toBe(junk);
  });
});

describe('D-038: rgb()/rgba() in style directives becomes comma-free #hex', () => {
  it('w4-09: converts fill:rgba(...) to #hex, preserves named colors', () => {
    const raw =
      'flowchart LR\n' +
      '  A[Alpha] --> B[Named]\n' +
      '  style A fill:rgba(74,144,217,0.85),stroke:darkorange,color:white\n' +
      '  style B fill:rebeccapurple,stroke:black,color:white';

    // Direction: the unpatched pipeline leaves the comma-bearing rgba() in.
    expect(raw).toContain('rgba(');

    const out = preprocessDefinition(raw, 'flowchart');

    // No color function (and therefore no property-splitting comma) survives.
    expect(out).not.toMatch(/rgba?\(/i);
    // 74,144,217 -> #4a90d9
    expect(out).toContain('style A fill:#4a90d9');
    // Named CSS colors are valid in mermaid and must be left as authored.
    expect(out).toContain('stroke:darkorange');
    expect(out).toContain('fill:rebeccapurple');
  });

  it('helper: plain #hex style lines and node labels are untouched', () => {
    const hexLine = 'style B fill:#f94144,stroke:black,color:white';
    expect(convertStyleColorFunctionsToHex(hexLine)).toBe(hexLine);
    // rgb() OUTSIDE a style/classDef directive (e.g. in a node label) is left
    // alone — the fix is scoped to directives.
    const label = 'flowchart LR\n  A["uses rgba(1,2,3,4) in text"] --> B';
    expect(convertStyleColorFunctionsToHex(label)).toBe(label);
  });

  it('helper: rgb() without alpha and classDef lines are handled', () => {
    expect(convertStyleColorFunctionsToHex('style C fill:rgb(255, 0, 0)'))
      .toBe('style C fill:#ff0000');
    expect(convertStyleColorFunctionsToHex('classDef hot fill:rgba(200,10,10,0.5),stroke:#000'))
      .toBe('classDef hot fill:#c80a0a,stroke:#000');
  });

  it('helper: percentage rgb() is left untouched (not mis-scaled)', () => {
    const pct = 'style D fill:rgb(100%, 0%, 0%)';
    // We decline to guess percentage->255 scaling; leave verbatim.
    expect(convertStyleColorFunctionsToHex(pct)).toBe(pct);
  });
});

/**
 * D-158 (user-specified-fill-color-contrast-not-remediated): a style/classDef
 * that sets a text `color:` which lands on its OWN `fill:` with near-zero
 * contrast (w4-07: fill:whitesmoke,color:snow = 1.05:1; w4-15: white-on-white =
 * 1.0:1) is painted verbatim, so the label vanishes on a visible card. The old
 * classdef-text-contrast-fix only fired on a 6-colour light-background
 * whitelist AND carried a `(?!.*color:)` lookahead, so it never saw
 * snow/whitesmoke/white and refused to override an existing color:. The new
 * style-fill-text-contrast-guard measures the fill/text contrast and overrides
 * only when it is genuinely broken (< 2.0:1).
 *
 * THEME OBLIGATION: D-158 is theme-independent — the fill is author-fixed, so
 * the card is the same colour in light and dark and one legible text colour is
 * correct on BOTH surfaces. The pair of assertions below discharges the
 * both-theme requirement in that theme-independent shape: (a) the broken pair
 * is now legible against its own (theme-invariant) fill, and (b) a pair that
 * was already fine stays byte-unchanged (no regression in either theme).
 *
 * DIRECTION: each "fixed" case first asserts the RAW author contrast is broken
 * (< 2.0), so the test fails against the unpatched pipeline that emits it as-is.
 */
describe('D-158: illegible fill/text pair in style/classDef is remediated', () => {
  const contrastOf = (fill: string, text: string): number => {
    const f = resolveStyleColorToRgb(fill);
    const t = resolveStyleColorToRgb(text);
    if (!f || !t) throw new Error(`unresolved: ${fill} / ${text}`);
    return contrastRatioRgb(f, t);
  };

  it('resolveStyleColorToRgb: named, #hex, #rgb, rgb() forms', () => {
    expect(resolveStyleColorToRgb('whitesmoke')).toEqual({ r: 245, g: 245, b: 245 });
    expect(resolveStyleColorToRgb('snow')).toEqual({ r: 255, g: 250, b: 250 });
    expect(resolveStyleColorToRgb('#FFF')).toEqual({ r: 255, g: 255, b: 255 });
    expect(resolveStyleColorToRgb('#4a90d9')).toEqual({ r: 74, g: 144, b: 217 });
    expect(resolveStyleColorToRgb('rgb(255,0,0)')).toEqual({ r: 255, g: 0, b: 0 });
    // Unresolvable forms decline (caller leaves them alone).
    expect(resolveStyleColorToRgb('transparent')).toBeNull();
    expect(resolveStyleColorToRgb('var(--brand)')).toBeNull();
  });

  it('w4-07: classDef fill:whitesmoke,color:snow (1.05:1) gets a legible text colour', () => {
    const raw =
      'flowchart LR\n' +
      '  A[Ingest] --> B[Route] --> C[Store]\n' +
      '  classDef n1 fill:tomato,stroke:rebeccapurple,color:white\n' +
      '  classDef n2 fill:lightgoldenrodyellow,stroke:darkslategray,color:black\n' +
      '  classDef n3 fill:whitesmoke,stroke:gainsboro,color:snow\n' +
      '  class A n1\n  class B n2\n  class C n3';

    // Direction: the authored n3 pair is genuinely broken.
    expect(contrastOf('whitesmoke', 'snow')).toBeLessThan(2.0);

    const out = preprocessDefinition(raw, 'flowchart');

    // n3 text colour overridden to black -> legible on whitesmoke in both themes.
    expect(out).toMatch(/classDef n3 fill:whitesmoke,stroke:gainsboro,color:#000000/);
    const n3 = out.match(/classDef n3[^\n]*/)![0];
    const textColor = n3.match(/color:([^,;\s]+)/)![1];
    expect(contrastOf('whitesmoke', textColor)).toBeGreaterThanOrEqual(4.5);

    // Legible author pairs are left byte-for-byte unchanged (no regression):
    // tomato/white = 2.95:1 and lightgoldenrodyellow/black = 19.67:1.
    expect(out).toContain('classDef n1 fill:tomato,stroke:rebeccapurple,color:white');
    expect(out).toContain('classDef n2 fill:lightgoldenrodyellow,stroke:darkslategray,color:black');
  });

  it('w4-15: white-on-white in three notations all get a legible text colour', () => {
    const raw =
      'flowchart LR\n' +
      '  A[Stage one] --> B[Stage two] --> C[Stage three]\n' +
      '  style A fill:#ffffff,stroke:#ffffff,color:#ffffff\n' +
      '  style B fill:#FFF,stroke:#EEE,color:#FEFEFE\n' +
      '  style C fill:white,stroke:white,color:ivory';

    // Direction: all three authored pairs are broken.
    expect(contrastOf('#ffffff', '#ffffff')).toBeLessThan(2.0);
    expect(contrastOf('#FFF', '#FEFEFE')).toBeLessThan(2.0);
    expect(contrastOf('white', 'ivory')).toBeLessThan(2.0);

    const out = preprocessDefinition(raw, 'flowchart');

    for (const node of ['A', 'B', 'C']) {
      const line = out.match(new RegExp(`style ${node}[^\\n]*`))![0];
      const fill = line.match(/fill:([^,;\s]+)/)![1];
      const text = line.match(/color:([^,;\s]+)/)![1];
      // The text colour is now legible against the fill on both surfaces.
      expect(contrastOf(fill, text)).toBeGreaterThanOrEqual(4.5);
      expect(text.toLowerCase()).toBe('#000000');
    }
  });

  it('helper is theme-independent and idempotent, and never touches non-directive lines', () => {
    const raw = 'flowchart LR\n  A[x] --> B\n  style A fill:white,color:ivory';
    const once = remediateStyleFillTextContrast(raw);
    // No theme input exists; a second pass changes nothing further.
    expect(remediateStyleFillTextContrast(once)).toBe(once);
    // A node label that merely contains the word "color:" is not a directive.
    const label = 'flowchart LR\n  A["color: white on white"] --> B';
    expect(remediateStyleFillTextContrast(label)).toBe(label);
  });
});
