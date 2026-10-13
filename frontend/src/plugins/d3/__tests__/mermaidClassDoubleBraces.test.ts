/**
 * Regression tests for mermaidClassDoubleBraces.
 *
 * Models emit brace-escaped class bodies (`class Foo {{ ... }}`) carried over
 * from f-string / template habits. Mermaid's classDiagram grammar rejects
 * them: "Expecting 'STRUCT_STOP', 'MEMBER', got 'OPEN_IN_STRUCT'".
 *
 * These import the REAL module; they fail against pre-fix code (module did
 * not exist) and the guard cases fail against a blanket `{{`->`{` replace.
 */
import {
  collapseClassDiagramDoubleBraces,
  isClassDiagramForBraces,
} from '../mermaidClassDoubleBraces';

// Trimmed from the real failing definition (SDNAReceiver diagram).
const FAILING = [
  'classDiagram',
  '    class SDNAReceiver {{',
  '        -shared_ptr~TableManagerInterface~ tblMgr_',
  '        -unordered_map~uint16_t,uint64_t~ labelWriteGen_',
  '        +Receive(reader) ResultCode',
  '    }}',
  '    class RxSpotLabel {{',
  '        <<union>>',
  '        uint16_t label',
  '        spotId : 7',
  '    }}',
  '    SDNAReceiver ..> RxSpotLabel : keys tables',
].join('\n');

describe('isClassDiagramForBraces', () => {
  it('recognizes classDiagram, incl. after init directive', () => {
    expect(isClassDiagramForBraces('classDiagram\n class A')).toBe(true);
    expect(isClassDiagramForBraces('%%{init:{}}%%\nclassDiagram-v2\n class A')).toBe(true);
  });
  it('rejects other diagram types / bad input', () => {
    expect(isClassDiagramForBraces('flowchart LR\n A{{hex}}-->B')).toBe(false);
    expect(isClassDiagramForBraces(null as unknown as string)).toBe(false);
  });
});

describe('collapseClassDiagramDoubleBraces — fixes the parse failure', () => {
  it('collapses doubled open and close braces on the real definition', () => {
    const out = collapseClassDiagramDoubleBraces(FAILING);
    expect(out).not.toContain('{{');
    expect(out).not.toContain('}}');
    expect(out).toContain('    class SDNAReceiver {\n');
    expect(out).toContain('    class RxSpotLabel {\n');
    expect(out.split('\n').filter((l) => l.trim() === '}')).toHaveLength(2);
  });

  it('preserves member lines, generics, annotations and relationships', () => {
    const out = collapseClassDiagramDoubleBraces(FAILING);
    expect(out).toContain('-unordered_map~uint16_t,uint64_t~ labelWriteGen_');
    expect(out).toContain('<<union>>');
    expect(out).toContain('spotId : 7');
    expect(out).toContain('SDNAReceiver ..> RxSpotLabel : keys tables');
  });

  it('handles generic class headers and namespace blocks', () => {
    const out = collapseClassDiagramDoubleBraces(
      'classDiagram\nnamespace Net {{\n  class Box~T~ {{\n    +T v\n  }}\n}}',
    );
    expect(out).toBe('classDiagram\nnamespace Net {\n  class Box~T~ {\n    +T v\n  }\n}');
  });

  it('collapses an over-closed body opened with a single brace', () => {
    const out = collapseClassDiagramDoubleBraces('classDiagram\nclass A {\n  +x\n}}');
    expect(out).toBe('classDiagram\nclass A {\n  +x\n}');
  });
});

describe('collapseClassDiagramDoubleBraces — GUARDS', () => {
  it('leaves valid single-brace diagrams byte-identical', () => {
    const spec = 'classDiagram\nclass A {\n  +x\n}\nA --> B';
    expect(collapseClassDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('does not touch non-class diagrams (flowchart hexagon nodes use {{ }})', () => {
    const spec = 'flowchart LR\n  A{{hexagon}} --> B';
    expect(collapseClassDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('does not touch braces inside quoted labels or comments', () => {
    const spec = 'classDiagram\n%% class X {{\nA --> B : "fmt {{x}}"';
    expect(collapseClassDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('ignores a stray }} line outside any block', () => {
    const spec = 'classDiagram\nA --> B\n}}';
    expect(collapseClassDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('is idempotent', () => {
    const once = collapseClassDiagramDoubleBraces(FAILING);
    expect(collapseClassDiagramDoubleBraces(once)).toBe(once);
  });
});

// SEAM: the helper is only useful if the enhancer registers it and no later
// class-diagram pass reintroduces or mangles the braces. This drives the real
// preprocessing chain the renderer uses (mermaidEnhancer.ts -> mermaidAPI).
describe('preprocessDefinition chain — double-brace collapse is wired in', () => {
  // Imported lazily so the helper-level suites above stay independent of the
  // enhancer's module-level side effects.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const enhancer = require('../mermaidEnhancer');

  beforeAll(() => {
    enhancer.initMermaidEnhancer();
  });

  it('output of the full chain contains no doubled class braces', () => {
    const out: string = enhancer.preprocessDefinition(FAILING, 'classDiagram');
    // Positive: the chain actually ran on this definition and kept the classes.
    expect(out).toMatch(/class SDNAReceiver\s*\{\s*\n/);
    expect(out).toMatch(/class RxSpotLabel\s*\{\s*\n/);
    expect(out).toContain('<<union>>');
    // The parse-failure trigger is gone.
    expect(out).not.toMatch(/class\s+\S+\s*\{\{/);
    expect(out.split('\n').some((l) => /^\s*\}\}+\s*$/.test(l))).toBe(false);
  });

  it('chain leaves flowchart hexagon nodes intact', () => {
    const out: string = enhancer.preprocessDefinition(
      'flowchart LR\n  A{{hexagon}} --> B',
      'flowchart',
    );
    expect(out).toContain('A{{hexagon}}');
  });
});
