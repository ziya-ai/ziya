/**
 * Regression tests for mermaidClassGenerics (Issue 42 defect 1).
 *
 * A mermaid classDiagram class/type token with NESTED / unbalanced generic
 * tildes (`Repo~Comparable~K~~`) hangs mermaid's classDiagram parser to the
 * 30s render timeout with ZERO output. flattenNestedClassGenerics collapses
 * such nested generics to a single balanced level so the whole family renders,
 * while leaving well-formed single-level generics BYTE-IDENTICAL.
 *
 * These tests import the REAL module (not a re-implementation) so they detect
 * drift in the shipped logic. They FAIL against the pre-fix code because the
 * module did not exist there (import error), and the assertions below
 * contradict the unflattened (hanging) input.
 */
import {
  flattenNestedClassGenerics,
  isClassDiagram,
} from '../mermaidClassGenerics';

describe('isClassDiagram', () => {
  it('recognizes a plain classDiagram', () => {
    expect(isClassDiagram('classDiagram\n  class A')).toBe(true);
    expect(isClassDiagram('  classDiagram-v2\n')).toBe(true);
  });

  it('recognizes classDiagram after an %%{init}%% directive', () => {
    expect(
      isClassDiagram('%%{init: {"theme":"dark"}}%%\nclassDiagram\n class A'),
    ).toBe(true);
  });

  it('rejects non-classDiagram specs', () => {
    expect(isClassDiagram('flowchart LR\n A-->B')).toBe(false);
    expect(isClassDiagram('sequenceDiagram\n A->>B: hi')).toBe(false);
    expect(isClassDiagram('' as unknown as string)).toBe(false);
    expect(isClassDiagram(null as unknown as string)).toBe(false);
  });
});

describe('flattenNestedClassGenerics — flattens the nested/hang trigger', () => {
  it('collapses the minimal nested-tilde class name', () => {
    // The verified minimal hang trigger.
    const out = flattenNestedClassGenerics('classDiagram\n  class Repo~Comparable~K~~');
    expect(out).toContain('Repo~Comparable K~');
    // No interior tilde run survives (the thing that hangs the parser).
    expect(out).not.toContain('~Comparable~K~~');
  });

  it('collapses a multi-param nested generic in a class name', () => {
    const out = flattenNestedClassGenerics(
      'classDiagram\n  class Repository~K extends Comparable~K~, V~',
    );
    // outer generic kept, interior tildes gone
    expect(out).toContain('Repository~K extends Comparable K , V~');
    expect(out).not.toMatch(/Comparable~K~/);
  });

  it('preserves the relationship arrow when two generics + an arrow share a line', () => {
    const line =
      'classDiagram\n  IComparable~T~ <|.. Repository~K extends Comparable~K~, V~';
    const out = flattenNestedClassGenerics(line);
    // The arrow must survive (not swallowed into a generic body).
    expect(out).toContain('<|..');
    // Well-formed single-level IComparable~T~ untouched.
    expect(out).toContain('IComparable~T~');
    // Nested one flattened.
    expect(out).not.toMatch(/Comparable~K~/);
  });
});

describe('flattenNestedClassGenerics — GUARDS (not a catch-all)', () => {
  it('leaves a well-formed single-level generic byte-identical', () => {
    const spec = 'classDiagram\n  class Foo~T~\n  class Bar~K, V~';
    expect(flattenNestedClassGenerics(spec)).toBe(spec);
  });

  it('leaves a classDiagram with no generics untouched', () => {
    const spec = 'classDiagram\n  Animal <|-- Dog\n  class Animal';
    expect(flattenNestedClassGenerics(spec)).toBe(spec);
  });

  it('does NOT touch non-classDiagram specs even if they contain tildes', () => {
    const spec = 'flowchart LR\n  A[~nested~tilde~] --> B';
    expect(flattenNestedClassGenerics(spec)).toBe(spec);
  });

  it('returns non-string / tilde-free input unchanged', () => {
    expect(flattenNestedClassGenerics('classDiagram\n class A')).toBe(
      'classDiagram\n class A',
    );
    expect(flattenNestedClassGenerics(null as unknown as string)).toBe(
      null as unknown as string,
    );
  });

  it('is idempotent — flattening a flattened spec is a no-op', () => {
    const once = flattenNestedClassGenerics(
      'classDiagram\n  class Repo~Comparable~K~~',
    );
    expect(flattenNestedClassGenerics(once)).toBe(once);
  });
});
