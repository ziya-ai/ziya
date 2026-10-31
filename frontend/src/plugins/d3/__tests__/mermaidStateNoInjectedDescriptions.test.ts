/**
 * stateDiagram preprocessing must not give states descriptions they lack.
 *
 * The removed `state-shape-fix` pass inserted `X: X` after the header for
 * every state name it found.  `X: X` is a state *description*, and mermaid
 * draws a described state as a titled box with the description below a
 * divider, so every state rendered as a doubled box.  On a composite
 * (`state OnPrimary { ... }`) it also attached a description to the
 * cluster.  Reported 2026-10-27 on a MAC-move failover diagram that came
 * out illegible.
 *
 * The first test fails against the unpatched pipeline: the output carries
 * `OnPrimary: OnPrimary`, `idle: idle`, ...
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const REPORTED = [
  'stateDiagram-v2',
  '    [*] --> OnPrimary',
  '    OnPrimary --> OnPrimary : MAC-move → redundant,<br/>primary reads UP<br/>(set pending, return GT_BAD_PARAM)',
  '    OnPrimary --> OnRedundant : MAC-move → redundant,<br/>primary reads DOWN<br/>(or primary not "direct")',
  '    state OnPrimary {',
  '        [*] --> idle',
  '        idle --> pending : spurious/flap MAC-move refused',
  '        pending --> idle : MAC-move → primary<br/>(SwitchToPrimary clears pending)',
  '        pending --> pending : Poll: primary still UP',
  '    }',
  '    OnPrimary --> OnRedundant : Poll (counters thread) sees<br/>pending && primary DOWN<br/>→ complete failover',
  '    OnRedundant --> OnPrimary : MAC-move → primary',
].join('\n');

// A line that gives a bare state a description: `Name : text`, not a
// transition, note, or `state` statement.
const DESCRIPTION_LINE = /^\s*(\w+)\s*:(?!::)/;

const describedStates = (def: string): string[] =>
  def.split('\n')
    .filter(l => !/-->|^\s*(note|state|classDef|class|style|direction)\b/i.test(l))
    .map(l => l.match(DESCRIPTION_LINE)?.[1])
    .filter((s): s is string => !!s);

describe('stateDiagram: no injected state descriptions', () => {
  it('adds no `X: X` description lines to the reported spec', () => {
    expect(describedStates(REPORTED)).toEqual([]);
    const out = preprocessDefinition(REPORTED, 'stateDiagram-v2');
    expect(describedStates(out)).toEqual([]);
    expect(out).not.toMatch(/^\s*OnPrimary\s*:\s*OnPrimary\s*$/m);
    // Positive: the composite, its body and the transitions survive.
    expect(out).toMatch(/^\s*state OnPrimary \{$/m);
    expect(out).toMatch(/^\s*idle --> pending : spurious\/flap MAC-move refused$/m);
    expect(out).toMatch(/^\s*OnRedundant --> OnPrimary : MAC-move → primary$/m);
    // The colon in "Poll: primary" is still escaped by the label pass.
    expect(out).toContain('Poll#58; primary still UP');
  });

  it('keeps a description the author wrote, exactly once', () => {
    const src = 'stateDiagram-v2\n    [*] --> Up\n    Up : link is up\n    Up --> Down';
    const out = preprocessDefinition(src, 'stateDiagram-v2');
    expect(describedStates(out)).toEqual(['Up']);
    expect(out).toMatch(/^\s*Up : link is up$/m);
  });
});
