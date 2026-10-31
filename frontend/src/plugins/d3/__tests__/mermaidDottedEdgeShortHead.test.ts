/**
 * Short dotted arrowheads `-.x`, `-.o`, `-.>` are normalized to `-.-x`,
 * `-.-o`, `-.->`.
 *
 * Mermaid 11's dotted-link token is /\s*[xo<]?-?\.+-[xo>]?\s*\/: the head
 * must follow a dash.  `-.x` therefore lexes as START_DOTTED_LINK (`-.`)
 * followed by text, and a pipe label after it fails.  Observed
 * (2026-10-22): `P10[[Y port 10]] -.x|"txpa1 ethpl1:<br/>no carrier"| T1`
 * -> "Parse error on line 5 ... Expecting 'LINK', 'UNICODE_TEXT',
 * 'EDGE_TEXT', got 'STR'".  Every earlier pass left the edge untouched.
 *
 * The first test fails against the unpatched enhancer: `-.x|` survives.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const Q = String.fromCharCode(34);
const fc = (body: string): string => `flowchart LR\n    ${body}`;

describe('short dotted arrowheads', () => {
  it('rewrites -.x before a quoted pipe label (the observed failure)', () => {
    const result = preprocessDefinition(
      fc(`Y -->|${Q}a${Q}| P10[[Y port 10]] -.x|${Q}txpa1 ethpl1:<br/>no carrier, 0 rx${Q}| T1[txpa1]`),
      'flowchart');
    expect(result).toContain(`P10[[Y port 10]] -.-x|${Q}txpa1 ethpl1:<br/>no carrier, 0 rx${Q}| T1`);
    expect(result).not.toMatch(/-\.x/);
  });

  it('rewrites -.o and -.> with and without labels', () => {
    expect(preprocessDefinition(fc('A -.o B'), 'flowchart')).toContain('A -.-o B');
    expect(preprocessDefinition(fc('A -.> B'), 'flowchart')).toContain('A -.-> B');
    expect(preprocessDefinition(fc(`A -.o|${Q}l${Q}| B`), 'flowchart')).toContain(`A -.-o|${Q}l${Q}| B`);
  });

  it('leaves valid dotted links alone', () => {
    // Positive control: these already lex as DOTTED_LINK.
    const result = preprocessDefinition(fc('A -.-x B\n    C -.-> D\n    E -..-o F\n    G -.- H'), 'flowchart');
    expect(result).toContain('A -.-x B');
    expect(result).toContain('C -.-> D');
    expect(result).toContain('E -..-o F');
    expect(result).toContain('G -.- H');
  });

  it('does not touch a dotted inline label that starts with x', () => {
    const result = preprocessDefinition(fc('A -.xfer.-> B'), 'flowchart');
    expect(result).not.toContain('-.-xfer');
  });
});
