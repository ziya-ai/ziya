/**
 * link-target-definition-fix must not treat a chained edge as one label.
 *
 * Its label group was \|.*?\|, which, anchored to end of line, spanned
 * pipes: `Y -->|"a"| P10[[p]] -.-x|"b"| T1[t]` matched with label
 * `|"a"| P10[[p]] -.-x|"b"|`, so `T1[t]` was hoisted onto its own line
 * above the chain (observed 2026-10-22).  The graph survived only because
 * the middle was copied back verbatim.
 *
 * The first test fails against the unpatched regex: T1[txpa1] is hoisted.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const Q = String.fromCharCode(34);
const fc = (body: string): string => `flowchart LR\n    ${body}`;
const lines = (s: string) => s.split('\n').map(l => l.trim());

describe('link-target-definition-fix on chained edges', () => {
  it('leaves a chained line with two labels intact (the observed case)', () => {
    const out = preprocessDefinition(
      fc(`Y -->|${Q}a${Q}| P10[[Y port 10]] -.-x|${Q}b${Q}| T1[txpa1]`), 'flowchart');
    expect(lines(out)).not.toContain('T1[txpa1]');
    expect(out).toContain(`-.-x|${Q}b${Q}| T1[txpa1]`);
  });

  it('still splits a simple labelled link to a defined target', () => {
    // Positive control: the pass still runs for the case it was written for.
    const out = preprocessDefinition(fc(`A -->|${Q}x${Q}| B[b]`), 'flowchart');
    const ls = lines(out);
    const defAt = ls.indexOf('B[b]');
    expect(defAt).toBeGreaterThan(-1);
    expect(ls[defAt + 1]).toMatch(/^A --> ?\|"x"\| B$/);
  });
});
