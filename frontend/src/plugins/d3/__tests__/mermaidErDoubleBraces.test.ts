/**
 * Regression tests for collapseErDiagramDoubleBraces.
 *
 * Models emit brace-escaped erDiagram entity blocks and cardinality markers
 * (`ENTITY {{ ... }}`, `||--o{{`) carried over from f-string / template
 * habits. Mermaid's erDiagram grammar rejects them:
 *   "Expecting 'BLOCK_STOP', 'ATTRIBUTE_WORD', got '{'".
 *
 * Valid erDiagram syntax never contains two adjacent braces outside a quoted
 * string or comment (cardinality tokens carry at most one brace per side), so
 * any such run is collapsed. These import the REAL module; they fail against
 * pre-fix code (the export did not exist).
 */
import {
  collapseErDiagramDoubleBraces,
  isErDiagramForBraces,
} from '../mermaidClassDoubleBraces';

// Verbatim from the failing render (shipment/delivery ER diagram).
const FAILING = [
  'erDiagram',
  '    shipment_route {{',
  '        string customer_id',
  '        string carrier_id',
  '        string hub_id',
  '        string depot_id',
  '        datetime64 start_time_unix_ms',
  '        datetime64 end_time_unix_ms',
  '    }}',
  '    catalog_entity {{',
  '        string entityId',
  '        string namespace',
  '        int versionNumber',
  '        string phase',
  '        string entityType',
  '    }}',
  '    delivery_aggregate_counts_v3 {{',
  '        string metric_context_key',
  '        string metric_name',
  '        string metric_aggregate_value',
  '        datetime last_updated',
  '    }}',
  '    catalog_entity ||--o{{ shipment_route : "depot phase"',
  '    shipment_route ||--o{{ delivery_aggregate_counts_v3 : "per delivery"',
].join('\n');

describe('isErDiagramForBraces', () => {
  it('recognizes erDiagram, incl. after an init directive', () => {
    expect(isErDiagramForBraces('erDiagram\n A { int id }')).toBe(true);
    expect(isErDiagramForBraces('%%{init:{}}%%\nerDiagram\n A { int id }')).toBe(true);
  });
  it('rejects other diagram types / bad input', () => {
    expect(isErDiagramForBraces('flowchart LR\n A{{hex}}-->B')).toBe(false);
    expect(isErDiagramForBraces('classDiagram\n class A {{')).toBe(false);
    expect(isErDiagramForBraces(null as unknown as string)).toBe(false);
  });
});

describe('collapseErDiagramDoubleBraces — fixes the parse failure', () => {
  it('collapses entity block braces and cardinality markers', () => {
    const out = collapseErDiagramDoubleBraces(FAILING);
    expect(out).not.toContain('{{');
    expect(out).not.toContain('}}');
    expect(out).toContain('    shipment_route {\n');
    expect(out).toContain('    catalog_entity {\n');
    expect(out).toContain('    delivery_aggregate_counts_v3 {\n');
    expect(out.split('\n').filter((l) => l.trim() === '}')).toHaveLength(3);
    expect(out).toContain('catalog_entity ||--o{ shipment_route : "depot phase"');
    expect(out).toContain('shipment_route ||--o{ delivery_aggregate_counts_v3 : "per delivery"');
  });

  it('collapses right-side markers and single-line entity blocks', () => {
    const out = collapseErDiagramDoubleBraces(
      'erDiagram\n  A }}|--|{{ B : has\n  C {{ int id }}',
    );
    expect(out).toBe('erDiagram\n  A }|--|{ B : has\n  C { int id }');
  });

  it('preserves attribute lines byte-for-byte', () => {
    const out = collapseErDiagramDoubleBraces(FAILING);
    expect(out).toContain('        datetime64 start_time_unix_ms\n');
    expect(out).toContain('        int versionNumber\n');
  });
});

describe('collapseErDiagramDoubleBraces — GUARDS', () => {
  it('leaves valid single-brace diagrams byte-identical', () => {
    const spec = 'erDiagram\n  A ||--o{ B : has\n  A {\n    int id PK\n  }';
    expect(collapseErDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('does not touch non-ER diagrams (flowchart hexagons use {{ }})', () => {
    const spec = 'flowchart LR\n  A{{hexagon}} --> B';
    expect(collapseErDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('does not touch braces inside quoted strings, comments or init directives', () => {
    const spec = [
      '%%{init: {"theme": {"x": 1}}}%%',
      'erDiagram',
      '  %% A {{ commented }}',
      '  A {',
      '    string fmt "uses {{x}} templates"',
      '  }',
      '  A ||--o{ B : "label {{y}}"',
    ].join('\n');
    expect(collapseErDiagramDoubleBraces(spec)).toBe(spec);
  });

  it('is idempotent', () => {
    const once = collapseErDiagramDoubleBraces(FAILING);
    expect(collapseErDiagramDoubleBraces(once)).toBe(once);
  });
});

// SEAM: the helper must be registered for erDiagram and no later ER pass may
// reintroduce or mangle the braces. Drives the real preprocessing chain.
describe('preprocessDefinition chain — ER double-brace collapse is wired in', () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const enhancer = require('../mermaidEnhancer');

  beforeAll(() => {
    enhancer.initMermaidEnhancer();
  });

  it('output of the full chain contains no doubled braces', () => {
    const out: string = enhancer.preprocessDefinition(FAILING, 'erDiagram');
    // Positive: the chain ran and kept every entity and relationship.
    expect(out).toMatch(/shipment_route\s*\{\s*\n/);
    expect(out).toMatch(/catalog_entity\s*\{\s*\n/);
    expect(out).toMatch(/delivery_aggregate_counts_v3\s*\{\s*\n/);
    expect(out).toContain('||--o{ shipment_route');
    // The parse-failure trigger is gone.
    expect(out).not.toContain('{{');
    expect(out).not.toContain('}}');
  });
});
