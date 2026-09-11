/**
 * G-0daf08 / D-160 (missing-dateFormat-yields-NaN-scale-all-bars-dropped).
 *
 * A gantt with NO `dateFormat` and NO `axisFormat` (both optional in the
 * grammar) makes mermaid's date scale evaluate to NaN: every bar x becomes NaN,
 * all task bars + the date axis are silently dropped, and only the title
 * survives — an image that renders "successfully" while showing zero data
 * (mermaid-w4-13).
 *
 * The gantt-date-format-fix preprocessor injects `dateFormat YYYY-MM-DD` (and a
 * matching `axisFormat %Y-%m-%d`) when the definition declares neither, so the
 * ISO task dates parse and the four bars + axis are drawn. This is a
 * text-preprocessor recovery with no theme input, so the both-theme obligation
 * is discharged at the shared render stage.
 *
 * DIRECTION: the raw definition is first asserted to lack dateFormat/axisFormat
 * (so it needs the injection); a pipeline without the fix leaves it untouched
 * and the assertions on the injected directives fail.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

// Exact mermaid-w4-13 definition.
const W4_13 =
  'gantt\n' +
  '  title Release plan\n' +
  '  section Build\n' +
  '    Compile        :a1, 2024-03-01, 5d\n' +
  '    Unit tests     :a2, after a1, 3d\n' +
  '  section Ship\n' +
  '    Package        :a3, after a2, 2d\n' +
  '    Deploy         :milestone, after a3, 0d';

describe('D-160: gantt with no dateFormat gets a default injected', () => {
  it('injects dateFormat YYYY-MM-DD and axisFormat for a dateless gantt', () => {
    // Direction: the raw spec declares neither directive.
    expect(W4_13).not.toMatch(/dateFormat/);
    expect(W4_13).not.toMatch(/axisFormat/);

    const out = preprocessDefinition(W4_13, 'gantt');

    // A dateFormat is now present and matches the ISO task dates (YYYY-MM-DD),
    // so the scale no longer evaluates to NaN and the bars are drawn.
    expect(out).toMatch(/dateFormat\s+YYYY-MM-DD/);
    expect(out).toMatch(/axisFormat\s+%Y-%m-%d/);
    // The task rows and their ISO date survive the preprocessing.
    expect(out).toContain('2024-03-01');
    expect(out).toContain('Compile');
    expect(out).toContain('Deploy');
  });

  it('does NOT clobber an author-declared dateFormat', () => {
    const withFmt =
      'gantt\n  dateFormat YYYY-MM-DD\n  section S\n    T :a1, 2024-01-01, 2d';
    const out = preprocessDefinition(withFmt, 'gantt');
    // Only the one author dateFormat line remains (no duplicate injected).
    expect(out.match(/dateFormat/g)!.length).toBe(1);
  });
});
