/**
 * Tests for Mermaid requirementDiagram preprocessor fixes.
 *
 * Validates that the preprocessing pipeline does not corrupt
 * valid requirement diagram definitions — specifically:
 *   - id values must NOT be quoted (Mermaid expects bare tokens)
 *   - verifymethod keyword must stay lowercase (Mermaid's lexer)
 *   - text values ARE quoted (multi-word text needs quotes)
 *   - verifymethod values are normalized to valid options
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

// Initialize the enhancer once to register all preprocessors
beforeAll(() => {
  initMermaidEnhancer();
});

const VALID_REQUIREMENT_DIAGRAM = `requirementDiagram
    requirement universal_compiler {
        id: UC001
        text: The Machine compiles reality
        risk: high
        verifymethod: Analysis
    }

    functionalreq entropy_module {
        id: UC002
        text: Entropy Engine manages decay
        risk: high
        verifymethod: Test
    }

    element precursors {
        type: Entity
    }

    precursors - satisfies -> universal_compiler
    universal_compiler - contains -> entropy_module`;

describe('requirementDiagram preprocessor', () => {
  it('should NOT quote id values', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    // id: UC001 must remain unquoted — Mermaid's parser expects a bare token
    expect(result).toMatch(/id:\s*UC001/);
    expect(result).not.toMatch(/id:\s*"UC001"/);
    expect(result).toMatch(/id:\s*UC002/);
    expect(result).not.toMatch(/id:\s*"UC002"/);
  });

  it('should quote text values (multi-word text)', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    // text with spaces needs quotes for Mermaid to parse correctly
    expect(result).toMatch(/text:\s*"The Machine compiles reality"/);
    expect(result).toMatch(/text:\s*"Entropy Engine manages decay"/);
  });

  it('should keep verifymethod keyword lowercase', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    // Mermaid's lexer only recognizes lowercase "verifymethod:"
    expect(result).toMatch(/verifymethod:/);
    expect(result).not.toMatch(/verifyMethod:/);
  });

  it('should preserve valid verifymethod values', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    expect(result).toMatch(/verifymethod:\s*Analysis/);
    expect(result).toMatch(/verifymethod:\s*Test/);
  });

  it('should normalize invalid verifymethod values to Test', () => {
    const input = `requirementDiagram
    requirement test_req {
        id: T001
        text: A test requirement
        risk: low
        verifymethod: unit_testing
    }`;
    const result = preprocessDefinition(input, 'requirementDiagram');
    // "unit_testing" is not valid — should be normalized to "Test"
    expect(result).toMatch(/verifymethod:\s*Test/);
  });

  it('should strip hyphens from IDs like UC-001', () => {
    const input = `requirementDiagram
    requirement test_req {
        id: UC-001
        text: A test
        risk: low
        verifymethod: Analysis
    }`;
    const result = preprocessDefinition(input, 'requirementDiagram');
    expect(result).toMatch(/id:\s*UC001/);
    expect(result).not.toMatch(/id:\s*UC-001/);
  });

  it('should preserve relationship lines', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    expect(result).toContain('precursors - satisfies -> universal_compiler');
    expect(result).toContain('universal_compiler - contains -> entropy_module');
  });

  it('should preserve element blocks', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    expect(result).toMatch(/element\s+precursors\s*\{/);
    expect(result).toMatch(/type:\s*Entity/);
  });

  it('should preserve risk values unquoted', () => {
    const result = preprocessDefinition(VALID_REQUIREMENT_DIAGRAM, 'requirementDiagram');
    expect(result).toMatch(/risk:\s*high/);
    expect(result).not.toMatch(/risk:\s*"high"/);
  });

  it('should handle all valid verifymethod values', () => {
    const validMethods = ['Analysis', 'Demonstration', 'Inspection', 'Test'];
    for (const method of validMethods) {
      const input = `requirementDiagram
    requirement r {
        id: R1
        text: Test
        risk: low
        verifymethod: ${method}
    }`;
      const result = preprocessDefinition(input, 'requirementDiagram');
      expect(result).toMatch(new RegExp(`verifymethod:\\s*${method}`));
    }
  });
});
