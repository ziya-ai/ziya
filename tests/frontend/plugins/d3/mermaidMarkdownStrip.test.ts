/**
 * Tests for trailing markdown content stripping from mermaid definitions,
 * and for the ER diagram attribute value quoting preprocessor.
 *
 * LLMs sometimes append markdown prose (headings, horizontal rules) after
 * the last valid diagram line inside a code fence. This causes parse errors
 * because tokens like `---` are valid mermaid edge syntax.
 */

/**
 * Simulates the markdown stripping logic applied in mermaidEnhancer before
 * the definition is sent to the mermaid renderer.
 */
function stripTrailingMarkdown(input: string): string {
  let cleaned = input;

  // Pattern 1: `---` followed by markdown heading
  cleaned = cleaned.replace(/\n---\s*\n+##\s+.*/s, '');

  // Pattern 2: Standalone markdown heading after blank line
  cleaned = cleaned.replace(/\n\s*\n##\s+.*$/s, '');

  // Pattern 3: Trailing `---` (markdown horizontal rule)
  cleaned = cleaned.replace(/\n---\s*$/, '');

  return cleaned;
}

/**
 * Simulates the ER diagram attribute value quoting preprocessor.
 * Uses the FIXED regex with [^\S\n] to prevent cross-line matching.
 */
function erAttrFix(def: string): string {
  return def.replace(
    /^(\s+)(\w+)[^\S\n]+(\w+)[^\S\n]+([^"\n][^\s\n]*)(\s*)$/gm,
    (match, indent, datatype, attrName, value, trail) => {
      // Skip relationship lines (they contain : or --)
      if (value.includes(':') || value.includes('-') || value.includes('|')) {
        return match;
      }
      return `${indent}${datatype} ${attrName} "${value}"${trail}`;
    }
  );
}

describe('stripTrailingMarkdown', () => {
  it('strips --- followed by ## heading (the reported bug)', () => {
    const input = [
      'graph BT',
      '    Core["THE CORE"]',
      '    style Core fill:#e94560,color:white',
      '    style Archive fill:#f39c12,color:black',
      '---',
      '## Chapter 33 — "The Archive: A History Not Their Own"',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).not.toContain('---');
    expect(result).not.toContain('Chapter 33');
    expect(result).toContain('style Archive fill:#f39c12,color:black');
  });

  it('strips --- followed by ## heading with blank lines between', () => {
    const input = [
      'graph LR',
      '    A --> B',
      '',
      '---',
      '',
      '## Some Heading',
      'And some paragraph text after.',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).not.toContain('Some Heading');
    expect(result).not.toContain('paragraph text');
    expect(result).toContain('A --> B');
  });

  it('strips standalone ## heading after blank line', () => {
    const input = [
      'sequenceDiagram',
      '    Alice->>Bob: Hello',
      '',
      '## Next Section',
      'More prose here.',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).not.toContain('Next Section');
    expect(result).toContain('Alice->>Bob: Hello');
  });

  it('strips trailing --- (horizontal rule)', () => {
    const input = [
      'pie',
      '    "A" : 30',
      '    "B" : 70',
      '---',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).not.toContain('---');
    expect(result).toContain('"B" : 70');
  });

  it('preserves valid diagram content with --- edges', () => {
    // `---` between node references is valid flowchart syntax
    const input = [
      'graph LR',
      '    A --- B --- C',
      '    style A fill:#f00',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).toBe(input);
  });

  it('preserves definitions without any trailing markdown', () => {
    const input = [
      'graph TD',
      '    A["Start"] --> B["End"]',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).toBe(input);
  });

  it('does not strip ## inside node labels', () => {
    const input = [
      'graph LR',
      '    A["Section ## 1"] --> B',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).toBe(input);
  });

  it('handles multiple trailing markdown sections', () => {
    const input = [
      'graph TD',
      '    X --> Y',
      '',
      '## First heading',
      'Some text.',
      '',
      '## Second heading',
      'More text.',
    ].join('\n');

    const result = stripTrailingMarkdown(input);
    expect(result).not.toContain('First heading');
    expect(result).not.toContain('Second heading');
    expect(result).toContain('X --> Y');
  });

  it('strips trailing --- with whitespace', () => {
    const input = 'pie\n    "A" : 50\n---   ';
    const result = stripTrailingMarkdown(input);
    expect(result).not.toContain('---');
  });
});

describe('erAttrFix - ER diagram attribute value quoting', () => {
  it('does not consume closing brace as attribute value (the reported bug)', () => {
    const input = [
      'erDiagram',
      '    CLAN {',
      '        string name',
      '        string totem_animal',
      '        int territory_size',
      '    }',
      '    CLAN ||--|{ ELDER : "led by"',
    ].join('\n');

    const result = erAttrFix(input);
    // The closing } must remain on its own line
    expect(result).toContain('        int territory_size\n    }');
    // The relationship line must be preserved
    expect(result).toContain('CLAN ||--|{ ELDER : "led by"');
    // Must NOT contain "}" as a quoted value
    expect(result).not.toContain('"}"');
  });

  it('quotes legitimate unquoted attribute values like PK', () => {
    const input = [
      'erDiagram',
      '    USER {',
      '        int id PK',
      '        string email UK',
      '    }',
    ].join('\n');

    const result = erAttrFix(input);
    expect(result).toContain('int id "PK"');
    expect(result).toContain('string email "UK"');
    // Closing brace still intact
    expect(result).toContain('\n    }');
  });

  it('skips lines with only two tokens (type + name, no value)', () => {
    const input = [
      'erDiagram',
      '    PRODUCT {',
      '        string name',
      '        int price',
      '    }',
    ].join('\n');

    const result = erAttrFix(input);
    // Nothing should change - no third token to quote
    expect(result).toBe(input);
  });

  it('skips already-quoted values', () => {
    const input = [
      'erDiagram',
      '    ITEM {',
      '        string label "Primary Key"',
      '    }',
    ].join('\n');

    const result = erAttrFix(input);
    // Already quoted — unchanged
    expect(result).toBe(input);
  });

  it('handles multiple entity blocks without cross-contamination', () => {
    const input = [
      'erDiagram',
      '    CLAN {',
      '        string name',
      '        int size',
      '    }',
      '    ELDER {',
      '        string name',
      '        int wisdom_score',
      '    }',
      '    CLAN ||--|{ ELDER : "led by"',
    ].join('\n');

    const result = erAttrFix(input);
    // Both closing braces must survive
    const braceCount = (result.match(/^\s+\}$/gm) || []).length;
    expect(braceCount).toBe(2);
    // No "}" should appear as a quoted value
    expect(result).not.toContain('"}"');
  });

  it('skips relationship lines that contain : or - or |', () => {
    const input = [
      'erDiagram',
      '    CUSTOMER ||--o{ ORDER : places',
    ].join('\n');

    // The value "places" follows a ":", which is filtered out
    const result = erAttrFix(input);
    expect(result).toBe(input);
  });
});
