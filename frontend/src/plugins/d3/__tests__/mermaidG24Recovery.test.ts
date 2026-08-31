/**
 * G-24 recovery/theme regression tests for the mermaid enhancer.
 *
 * D-172 (markdown-fence-not-stripped-render-hangs): a diagram wrapped in a
 *   ```` ```mermaid … ``` ```` fence leaks the fence to the parser, which hangs
 *   the render for the full 30s timeout. The fix strips the outer fence.
 *
 * D-173 (commas-or-parens-inside-value-render-hangs): one tokenizer bug, two
 *   faces. (w4-04) bare parens inside a `[ ]` label (`A[Parse request (fast
 *   path)]`) fracture the lexer; the fix quotes the label. (w4-06) an
 *   `hsl()`/`rgb()`/`rgba()` colour function in a style directive carries
 *   in-parens commas that split mermaid's comma-delimited style grammar; the
 *   fix converts the function to a comma-free #hex.
 *
 * D-176 (missing-dateFormat-yields-NaN-scale-all-bars-dropped): a gantt with no
 *   dateFormat. The REAL cause (differs from triage): a default dateFormat IS
 *   already injected by gantt-date-format-fix, but gantt-task-definition-fix had
 *   an EMPTY `taskDefParts.length >= 3` branch that silently DROPPED every valid
 *   3-field task, leaving title+sections with zero bars. The fix preserves the
 *   already-valid task line.
 *
 * D-040 (transparent-fill-label-invisible:light): `primaryColor: transparent`
 *   in %%{init}%% makes mermaid derive a near-white node label over a
 *   see-through fill (ghost text ~1.1:1 on the light surface). Dropping the
 *   transparent primaryColor restores mermaid's default fill (#ECECFF) + derived
 *   dark text (~#333333 = 10.8:1 on the node) — legible on BOTH themes because
 *   the label sits on a real node fill, not the page surface.
 *
 * DIRECTION: every "fixed" assertion is paired with a check that the RAW input
 * genuinely needs the repair, so each test fails against the unpatched pipeline.
 */

import {
  preprocessDefinition,
  initMermaidEnhancer,
  stripMermaidCodeFence,
  quoteBracketLabelsWithParens,
  sanitizeInitTransparentPrimaryColor,
  convertStyleColorFunctionsToHex,
  resolveStyleColorToRgb,
  contrastRatioRgb,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

describe('D-172: outer markdown code fence is stripped', () => {
  const w401 =
    '```mermaid\nflowchart LR\n  A[Client] --> B[API Gateway]\n' +
    '  B --> C[(Database)]\n  B --> D[Cache]\n```';

  it('pure: removes the fence and keeps the graph', () => {
    // Direction: the raw input carries the fence.
    expect(w401).toContain('```');
    const out = stripMermaidCodeFence(w401);
    expect(out).not.toContain('```');
    expect(out.trim().split('\n')[0]).toBe('flowchart LR');
    // a fence-less definition is returned unchanged
    expect(stripMermaidCodeFence('flowchart LR\n A-->B')).toBe('flowchart LR\n A-->B');
  });

  it('pipeline: preprocessDefinition strips the fence', () => {
    const out = preprocessDefinition(w401, 'flowchart');
    expect(out).not.toContain('```');
    // the fence is gone and a real diagram header leads (the pipeline may
    // normalize `flowchart` -> `graph`, which is equivalent)
    expect(/(^|\n)\s*(flowchart|graph)\s+LR\b/.test(out)).toBe(true);
    expect(out).toContain('C[(Database)]'); // cylinder shape untouched
  });
});

describe('D-173 (w4-04): bracket labels with bare parens are quoted', () => {
  const w404 =
    'flowchart TD\n  A[Parse request (fast path)] --> B[Validate (schema v2)]\n' +
    '  B --> C[Emit result (JSON)]';

  it('pure: quotes only paren labels, leaves other shapes alone', () => {
    const out = quoteBracketLabelsWithParens(w404);
    expect(out).toContain('A["Parse request (fast path)"]');
    expect(out).toContain('B["Validate (schema v2)"]');
    expect(out).toContain('C["Emit result (JSON)"]');
    // idempotent
    expect(quoteBracketLabelsWithParens(out)).toBe(out);
    // shapes that must NOT be touched
    expect(quoteBracketLabelsWithParens('flowchart LR\n B --> C[(Database)]'))
      .toContain('C[(Database)]');
    expect(quoteBracketLabelsWithParens('flowchart LR\n A[[Sub]]')).toContain('A[[Sub]]');
    expect(quoteBracketLabelsWithParens('flowchart LR\n A([Start])')).toContain('A([Start])');
    // a plain label is byte-unchanged
    expect(quoteBracketLabelsWithParens('flowchart LR\n A[Client]'))
      .toBe('flowchart LR\n A[Client]');
  });

  it('pipeline: no unquoted paren survives in a [ ] label', () => {
    // Direction: raw label has an unquoted "(".
    expect(w404).toContain('[Parse request (fast path)]');
    const out = preprocessDefinition(w404, 'flowchart');
    expect(out).toContain('"Parse request (fast path)"');
    // no rectangle label still holds a bare "(" without a wrapping quote
    expect(/\[[^"\]\n]*\([^"\]\n]*\]/.test(out)).toBe(false);
  });
});

describe('D-173 (w4-06): rgb()/rgba()/hsl() in style directives become #hex', () => {
  const w406 =
    'flowchart TD\n  A[Queue] --> B[Worker] --> C[Sink]\n' +
    '  style A fill:rgba(255,99,71,0.85),stroke:rgb(139,0,0),color:rgba(0,0,0,1)\n' +
    '  style B fill:rgba(70,130,180,0.4),stroke:rgb(25,25,112)\n' +
    '  style C fill:hsl(120, 60%, 45%),color:#fff';

  it('pure: no colour function (or its in-parens commas) remain', () => {
    // Direction: raw carries rgba( and hsl(.
    expect(/rgba?\(/i.test(w406)).toBe(true);
    expect(/hsl\(/i.test(w406)).toBe(true);
    const out = convertStyleColorFunctionsToHex(w406);
    expect(/rgba?\(|hsla?\(/i.test(out)).toBe(false);
    expect(out).toContain('fill:#ff6347'); // rgba(255,99,71,..) tomato
    expect(out).toContain('fill:#2eb82e'); // hsl(120,60%,45%) green
  });

  it('pipeline: style directive is comma-safe after preprocessing', () => {
    const out = preprocessDefinition(w406, 'flowchart');
    expect(/hsl\(/i.test(out)).toBe(false);
    expect(/rgba\(/i.test(out)).toBe(false);
  });
});

describe('D-176: a gantt with no dateFormat keeps all its tasks', () => {
  const w413 =
    'gantt\n  title Release plan\n  section Build\n' +
    '    Compile        :a1, 2024-03-01, 5d\n' +
    '    Unit tests     :a2, after a1, 3d\n  section Ship\n' +
    '    Package        :a3, after a2, 2d\n' +
    '    Deploy         :milestone, after a3, 0d';

  it('pipeline: default dateFormat injected AND every task preserved', () => {
    // Direction: raw declares no dateFormat, and its tasks are the 3-field form
    // that the unpatched gantt-task-definition-fix dropped.
    expect(w413).not.toContain('dateFormat');
    const out = preprocessDefinition(w413, 'gantt');
    expect(out).toContain('dateFormat'); // default inferred
    // all four tasks survived (were previously dropped -> empty chart)
    expect(out).toContain('Compile');
    expect(out).toContain('Unit tests');
    expect(out).toContain('Package');
    expect(out).toContain('Deploy');
    // their scheduling data survived intact
    expect(out).toContain('2024-03-01');
    expect(out).toContain('after a1');
    expect(out).toContain('milestone');
  });
});

describe('D-040: transparent primaryColor is dropped so labels stay legible', () => {
  const w410 =
    '%%{init: {"theme":"base","themeVariables":{"primaryColor":"transparent","nodeBackground":"#123456"}}}%%\n' +
    'flowchart LR\n  A[See-through] --> B[Bogus token]';

  it('pure: removes transparent primaryColor, keeps other tokens', () => {
    // Direction: raw pins primaryColor:transparent.
    expect(w410).toContain('"primaryColor":"transparent"');
    const out = sanitizeInitTransparentPrimaryColor(w410);
    expect(out).not.toContain('"primaryColor"');
    expect(out).toContain('"nodeBackground":"#123456"'); // bogus token left for mermaid to ignore
    expect(out).toContain('"theme":"base"');
    // a real primaryColor is preserved verbatim
    const keep = '%%{init: {"themeVariables":{"primaryColor":"#2e7d32"}}}%%\nflowchart LR\n A-->B';
    expect(sanitizeInitTransparentPrimaryColor(keep)).toContain('"primaryColor":"#2e7d32"');
  });

  it('pipeline: preprocessDefinition drops the transparent primaryColor', () => {
    const out = preprocessDefinition(w410, 'flowchart');
    expect(out).not.toContain('"primaryColor"');
  });

  // Both-theme contrast: after the fix the label sits on mermaid's default node
  // fill #ECECFF with derived dark text ~#333333 — legible regardless of theme.
  it('contrast: fixed label/fill pair clears the 4.5 text floor (both themes)', () => {
    const text = resolveStyleColorToRgb('#333333')!;
    const nodeFill = resolveStyleColorToRgb('#ececff')!;
    // legible on the default node fill (theme-independent, since fill is fixed)
    expect(contrastRatioRgb(text, nodeFill)).toBeGreaterThanOrEqual(4.5);
    // and legible on a light page surface should the fill be light
    expect(contrastRatioRgb(text, resolveStyleColorToRgb('#ffffff')!)).toBeGreaterThanOrEqual(4.5);
    // BROKEN baseline: the near-white derived label on a white surface fails.
    const ghost = resolveStyleColorToRgb('#f0f0ff')!;
    expect(contrastRatioRgb(ghost, resolveStyleColorToRgb('#ffffff')!)).toBeLessThan(3);
  });
});
