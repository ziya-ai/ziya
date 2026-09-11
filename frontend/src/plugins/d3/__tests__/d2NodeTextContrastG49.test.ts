/**
 * G-49 — d2 node-label contrast + block-string recovery (shared file: d2Plugin.ts).
 *
 *   D-055 (theme, high): nodeTextFill returned the unconditional theme text
 *          constant (white in dark) whenever the author supplied no font-color,
 *          so white dark-text drowned on the light-ish fills models emit
 *          (papayawhip 1.13:1, cyan 1.25:1, ...). The fix chooses #000/#fff by
 *          WCAG contrast against the ACTUAL resolved node fill (composited over
 *          the page when the fill is semi-transparent), via the new exported
 *          helpers d2ReadableTextOn / d2ColorAlpha.
 *
 *   D-056 (structural): a `|md ... |` block string was not consumed, so its body
 *          lines leaked as phantom nodes. stripD2BlockStrings collapses the
 *          block into a single labelled node.
 *
 * THEME CONTRACT (D-055): every assertion is PAIRED across both themes — the
 * previously-broken DARK direction is asserted fixed AND the LIGHT direction is
 * asserted still-correct, so the fix cannot be a swap-one-constant that repairs
 * dark by breaking light. The choice resolves from the SAME fill in either
 * theme, so it is a per-fill resolution, not a constant.
 *
 * DIRECTION: d2ReadableTextOn / d2ColorAlpha / stripD2BlockStrings do not exist
 * in unpatched d2Plugin.ts (the import fails to compile against HEAD), and the
 * pre-fix behaviour (white text on papayawhip/cyan) is shown to be below the
 * 3:1 graphical floor so the assertions certify the fix, not the bug.
 */
import {
  d2ReadableTextOn,
  d2ColorAlpha,
  stripD2BlockStrings,
  D2_DARK_BG,
  D2_LIGHT_BG,
  D2Parser,
} from '../d2Plugin';
import { contrastRatio } from '../chartTheme';

const DARK_TEXT = '#ffffff'; // d2ThemeColors(true).text
const LIGHT_TEXT = '#000000'; // d2ThemeColors(false).text

describe('D-055 node label contrast against the resolved fill', () => {
  // ── the two headline broken cases (dark theme, light author fill) ──────────
  it('papayawhip fill: DARK picks black (fixed), LIGHT stays black (still correct)', () => {
    const dark = d2ReadableTextOn('papayawhip', D2_DARK_BG, DARK_TEXT);
    const light = d2ReadableTextOn('papayawhip', D2_LIGHT_BG, LIGHT_TEXT);
    expect(dark).toBe('#000000');
    expect(light).toBe('#000000');
    // pre-fix dark result was the theme white — prove it was illegible.
    expect(contrastRatio('#ffffff', '#ffefd5')).toBeLessThan(3); // ~1.13 (bug)
    expect(contrastRatio(dark, '#ffefd5')).toBeGreaterThan(4.5); // ~18.6 (fix)
  });

  it('cyan (#0ff) fill: DARK picks black (fixed), LIGHT stays black', () => {
    const dark = d2ReadableTextOn('#0ff', D2_DARK_BG, DARK_TEXT);
    const light = d2ReadableTextOn('#0ff', D2_LIGHT_BG, LIGHT_TEXT);
    expect(dark).toBe('#000000');
    expect(light).toBe('#000000');
    expect(contrastRatio('#ffffff', '#00ffff')).toBeLessThan(3); // ~1.25 (bug)
    expect(contrastRatio(dark, '#00ffff')).toBeGreaterThan(4.5);
  });

  it('cornflowerblue fill: DARK picks black over the old invisible white', () => {
    const dark = d2ReadableTextOn('cornflowerblue', D2_DARK_BG, DARK_TEXT);
    expect(dark).toBe('#000000');
    expect(contrastRatio('#ffffff', '#6495ed')).toBeLessThan(3); // ~2.97 (bug)
    expect(contrastRatio(dark, '#6495ed')).toBeGreaterThanOrEqual(4.5);
    // light theme: black is already correct and preserved.
    expect(d2ReadableTextOn('cornflowerblue', D2_LIGHT_BG, LIGHT_TEXT)).toBe('#000000');
  });

  // ── semi-transparent fill: the composite over the page, not the nominal ────
  it('rgba(255,99,71,0.5): DARK composites to a dark tone -> white; LIGHT -> black', () => {
    // Nominal tomato is light; but composited over #1f1f1f at 0.5 it is dark, so
    // white is the correct label there — proving the composite path is used.
    expect(d2ReadableTextOn('rgba(255, 99, 71, 0.5)', D2_DARK_BG, DARK_TEXT)).toBe('#ffffff');
    expect(d2ReadableTextOn('rgba(255, 99, 71, 0.5)', D2_LIGHT_BG, LIGHT_TEXT)).toBe('#000000');
  });

  // ── identity: theme-default fills keep the theme text constant ─────────────
  it('default dark node fill #303f9f keeps white; default light fill #e3f2fd keeps black', () => {
    expect(d2ReadableTextOn('#303f9f', D2_DARK_BG, DARK_TEXT)).toBe('#ffffff');
    expect(d2ReadableTextOn('#e3f2fd', D2_LIGHT_BG, LIGHT_TEXT)).toBe('#000000');
  });

  it('an unresolvable (unknown) fill falls back to the theme text constant', () => {
    // A valid-but-unlisted CSS name we cannot resolve keeps prior behaviour.
    expect(d2ReadableTextOn('rebeccohotpinkish', D2_DARK_BG, DARK_TEXT)).toBe('#ffffff');
    expect(d2ReadableTextOn('rebeccohotpinkish', D2_LIGHT_BG, LIGHT_TEXT)).toBe('#000000');
  });

  it('d2ColorAlpha parses rgba/hsla/#rrggbbaa and defaults to opaque', () => {
    expect(d2ColorAlpha('rgba(1,2,3,0.5)')).toBeCloseTo(0.5, 5);
    expect(d2ColorAlpha('hsla(0,0%,0%,0.25)')).toBeCloseTo(0.25, 5);
    expect(d2ColorAlpha('#11223344')).toBeCloseTo(0x44 / 255, 5);
    expect(d2ColorAlpha('#123456')).toBe(1);
    expect(d2ColorAlpha('papayawhip')).toBe(1);
  });
});

describe('D-056 |md| block-string collapse', () => {
  it('collapses a multi-line |md ... | block into a single labelled node', () => {
    const src = [
      'note: |md',
      '  ## Deployment note',
      '  Runs in **us-east-1**',
      '|',
      'users -> note',
    ].join('\n');
    const cleaned = stripD2BlockStrings(src);
    // The block body headings/bold must NOT survive as their own lines.
    expect(cleaned).not.toContain('## Deployment note');
    expect(cleaned).not.toContain('**us-east-1**');
    expect(cleaned).toMatch(/note:\s*Deployment note/);

    // End to end: exactly one `note` node, no phantom `## Deployment note` node.
    const parser = new D2Parser();
    const g = parser.parse(src);
    const ids = g.nodes.map((n: any) => n.id);
    expect(ids).toContain('note');
    expect(ids.some((id: string) => id.includes('#') || id.includes('*'))).toBe(false);
  });

  it('collapses an inline |md ... | block on one line', () => {
    const cleaned = stripD2BlockStrings('label: |md **bold** text|');
    expect(cleaned).toMatch(/label:\s*bold text/);
    expect(cleaned).not.toContain('|');
  });

  it('is a no-op on ordinary d2 source (no block strings)', () => {
    const src = 'a: A\nb: B\na -> b: edge';
    expect(stripD2BlockStrings(src)).toBe(src);
  });

  it('leaves an unterminated block opener untouched (no closer found)', () => {
    const src = 'note: |md\n  ## heading only';
    // No closing `|` line -> opener not consumed, nothing dropped.
    expect(stripD2BlockStrings(src)).toBe(src);
  });
});
