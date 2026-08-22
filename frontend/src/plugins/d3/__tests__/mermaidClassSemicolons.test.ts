/**
 * Regression tests for mermaidClassSemicolons (Issue 42 defect 2).
 *
 * A bare `;` inside a classDiagram quoted label/note string
 * (`A --> B : "self-ref ; semi"`) is mistaken for mermaid's statement
 * separator and hangs the classDiagram parser to the 30s render timeout with
 * ZERO output. escapeClassDiagramLabelSemicolons rewrites only in-quote
 * semicolons to mermaid's native `#59;` entity, leaving genuine separators
 * (outside quotes) untouched.
 *
 * These tests import the REAL module so they detect drift in the shipped
 * logic. They FAIL against the pre-fix code because the module did not exist
 * (import error), and the guard assertions below would fail against any naive
 * blanket-escape implementation.
 */
import {
  escapeClassDiagramLabelSemicolons,
  isClassDiagramForSemicolons,
} from '../mermaidClassSemicolons';

describe('isClassDiagramForSemicolons', () => {
  it('recognizes classDiagram (incl. after an init directive)', () => {
    expect(isClassDiagramForSemicolons('classDiagram\n class A')).toBe(true);
    expect(
      isClassDiagramForSemicolons('%%{init:{}}%%\nclassDiagram-v2\n class A'),
    ).toBe(true);
  });
  it('rejects non-classDiagram / bad input', () => {
    expect(isClassDiagramForSemicolons('flowchart LR\n A-->B')).toBe(false);
    expect(isClassDiagramForSemicolons(null as unknown as string)).toBe(false);
  });
});

describe('escapeClassDiagramLabelSemicolons — escapes the hang trigger', () => {
  it('escapes a `;` inside a quoted relationship label', () => {
    const out = escapeClassDiagramLabelSemicolons(
      'classDiagram\n  A --> B : "self-ref ; semi"',
    );
    expect(out).toContain('"self-ref #59; semi"');
    // The bare in-quote `;` must be gone (i.e. not preceded by the #59 entity).
    expect(out).not.toMatch(/[^9]; semi"/);
  });

  it('escapes multiple in-quote semicolons', () => {
    const out = escapeClassDiagramLabelSemicolons(
      'classDiagram\n  A --> B : "a ; b ; c"',
    );
    expect(out).toContain('"a #59; b #59; c"');
  });

  it('handles backslash-escaped inner quotes without losing quote state', () => {
    const out = escapeClassDiagramLabelSemicolons(
      'classDiagram\n  A --> B : "a \\"b\\" ; c"',
    );
    // The `;` is still inside the label, so it is escaped.
    expect(out).toContain('#59;');
    expect(out).toContain('\\"b\\"');
  });
});

describe('escapeClassDiagramLabelSemicolons — GUARDS (not a catch-all)', () => {
  it('leaves a GENUINE statement separator (outside quotes) untouched', () => {
    // A `;` separating two statements must stay a bare `;`.
    const spec = 'classDiagram\n  classA --|> classB; classC --|> classD';
    const out = escapeClassDiagramLabelSemicolons(spec);
    expect(out).toBe(spec);
    expect(out).not.toContain('#59;');
  });

  it('escapes ONLY the in-quote `;` when a line mixes both', () => {
    const spec = 'classDiagram\n  A --> B : "in ; quote" ; classC --|> classD';
    const out = escapeClassDiagramLabelSemicolons(spec);
    expect(out).toContain('"in #59; quote"');
    // The separator after the label is still a bare `;`.
    expect(out).toContain('quote" ; classC');
  });

  it('leaves a classDiagram with no semicolons byte-identical', () => {
    const spec = 'classDiagram\n  A --> B : plain label';
    expect(escapeClassDiagramLabelSemicolons(spec)).toBe(spec);
  });

  it('does NOT touch non-classDiagram specs', () => {
    const spec = 'sequenceDiagram\n  A->>B: "has ; semi"';
    expect(escapeClassDiagramLabelSemicolons(spec)).toBe(spec);
  });

  it('is idempotent', () => {
    const once = escapeClassDiagramLabelSemicolons(
      'classDiagram\n  A --> B : "x ; y"',
    );
    expect(escapeClassDiagramLabelSemicolons(once)).toBe(once);
  });
});
