/**
 * G-76 / D-032 (author-hardcoded-color-not-theme-normalized) — mermaid half.
 *
 * A chat-message mermaid fence supplies its own palette via a `%%{init}%%`
 * themeVariables block. The custom pale ramp in chat-message-w3-15 sets
 * `primaryColor:#eef6fb` (near-white node fill) with `primaryTextColor:#9fc7e0`
 * (pale blue label) — 1.64:1, an invisible label on the node in BOTH themes,
 * because the node fill is author-fixed regardless of the page surface.
 *
 * remediateInitThemeVariableContrast clamps `primaryTextColor` to the black/
 * white that reads best on the fill ONLY when the pair is below the 2.0:1 floor,
 * so a genuinely illegible label is repaired while a deliberate per-page palette
 * whose text already reads on its fill is left byte-for-byte unchanged.
 *
 * BOTH-THEME obligation: the repair is theme-independent (the node fill is
 * author-supplied, not derived from the page). The two legible author palettes
 * this guard must NOT touch are the light-tuned one (w3-08: #333 on #fafafa) and
 * the dark-tuned one (w3-09: #eee on #1a1a1a); asserting both survive unchanged
 * discharges the "the other theme still is correct" half, while the w3-15
 * assertion discharges "the broken case is now correct".
 *
 * DIRECTION: each "fixed" assertion is paired with a check that the RAW palette
 * is genuinely below the contrast floor (so it needs the repair), which means
 * the test fails against the unpatched preprocessor that never clamps it.
 */

import {
  preprocessDefinition,
  initMermaidEnhancer,
  remediateInitThemeVariableContrast,
  resolveStyleColorToRgb,
  contrastRatioRgb,
} from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const FLOOR = 2.0;

const themeVarsOf = (def: string): Record<string, string> => {
  const m = def.match(/%%\{\s*init\s*:\s*([\s\S]*?)\}%%/i);
  if (!m) return {};
  // normalize single quotes / bare keys enough for JSON.parse in the assertion
  const t = m[1]
    .replace(/'([^'\\]*(?:\\.[^'\\]*)*)'/g, (_x, inner: string) => '"' + inner.replace(/"/g, '\\"') + '"')
    .replace(/([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)/g, '$1"$2"$3')
    .replace(/,(\s*[}\]])/g, '$1');
  return JSON.parse(t).themeVariables || {};
};

const contrast = (text: string, fill: string): number => {
  const a = resolveStyleColorToRgb(text)!;
  const b = resolveStyleColorToRgb(fill)!;
  return contrastRatioRgb(a, b);
};

// The exact w3-15 init directive (single-quoted, as the spec emits it).
const W3_15 =
  "%%{init: {'theme':'base','themeVariables':{'primaryColor':'#eef6fb'," +
  "'primaryTextColor':'#9fc7e0','primaryBorderColor':'#dbeaf4','lineColor':'#e3eef6'}}}%%\n" +
  'flowchart TD\n  S1[step 1] --> S2[step 2]';

describe('D-032: illegible init primaryTextColor on its own primaryColor fill is clamped', () => {
  it('w3-15: pale #9fc7e0 label on pale #eef6fb fill is below floor (direction) and gets clamped readable', () => {
    // Direction: the author pair is genuinely illegible, so the unpatched
    // pipeline (no init contrast guard) would leave it invisible.
    expect(contrast('#9fc7e0', '#eef6fb')).toBeLessThan(FLOOR);

    const out = remediateInitThemeVariableContrast(W3_15);
    const tv = themeVarsOf(out);

    // Fill is untouched; only the illegible text colour is repaired.
    expect(tv.primaryColor.toLowerCase()).toBe('#eef6fb');
    expect(tv.primaryTextColor.toLowerCase()).not.toBe('#9fc7e0');
    // Repaired label now clears the floor on the (theme-independent) fill.
    expect(contrast(tv.primaryTextColor, '#eef6fb')).toBeGreaterThanOrEqual(FLOOR);
    // Best-legible on a near-white fill is black (19.2:1).
    expect(tv.primaryTextColor.toLowerCase()).toBe('#000000');
  });

  it('applies through the full preprocessor pipeline for a flowchart', () => {
    const out = preprocessDefinition(W3_15, 'flowchart');
    const tv = themeVarsOf(out);
    expect(tv.primaryColor.toLowerCase()).toBe('#eef6fb');
    expect(contrast(tv.primaryTextColor, '#eef6fb')).toBeGreaterThanOrEqual(FLOOR);
  });

  it('is idempotent (a second pass changes nothing)', () => {
    const once = remediateInitThemeVariableContrast(W3_15);
    const twice = remediateInitThemeVariableContrast(once);
    expect(twice).toBe(once);
  });
});

describe('D-032: deliberate legible per-page palettes are NOT overridden (both themes)', () => {
  it('w3-08 light-tuned palette (#333 on #fafafa = legible) is left byte-for-byte unchanged', () => {
    // Direction: this pair is comfortably legible, so the guard must not fire.
    expect(contrast('#333333', '#fafafa')).toBeGreaterThanOrEqual(FLOOR);

    const light =
      "%%{init: {'theme':'base','themeVariables':{'primaryColor':'#fafafa'," +
      "'primaryTextColor':'#333333','primaryBorderColor':'#dddddd','lineColor':'#cccccc'}}}%%\n" +
      'flowchart LR\n  A[Ingest] --> B[Parse]';
    expect(remediateInitThemeVariableContrast(light)).toBe(light);
  });

  it('w3-09 dark-tuned palette (#eee on #1a1a1a = legible) is left byte-for-byte unchanged', () => {
    expect(contrast('#eeeeee', '#1a1a1a')).toBeGreaterThanOrEqual(FLOOR);

    const dark =
      "%%{init: {'theme':'base','themeVariables':{'primaryColor':'#1a1a1a'," +
      "'primaryTextColor':'#eeeeee','primaryBorderColor':'#3a3a3a','lineColor':'#555555'}}}%%\n" +
      'flowchart LR\n  A[Ingest] --> B[Parse]';
    expect(remediateInitThemeVariableContrast(dark)).toBe(dark);
  });
});

describe('D-032: guard declines when it cannot resolve exactly', () => {
  it('leaves a directive whose colours are theme tokens unchanged', () => {
    const tokens =
      '%%{init: {"themeVariables":{"primaryColor":"var(--fill)","primaryTextColor":"var(--text)"}}}%%\n' +
      'flowchart LR\n  A --> B';
    expect(remediateInitThemeVariableContrast(tokens)).toBe(tokens);
  });

  it('leaves a directive with no themeVariables unchanged', () => {
    const none = '%%{init: {"theme":"base"}}%%\nflowchart LR\n  A --> B';
    expect(remediateInitThemeVariableContrast(none)).toBe(none);
  });

  it('leaves a definition with no init directive unchanged', () => {
    const plain = 'flowchart LR\n  A[step 1] --> B[step 2]';
    expect(remediateInitThemeVariableContrast(plain)).toBe(plain);
  });
});
