/**
 * stateDiagram label text with colons / semicolons (reported 2026-10-16).
 *
 * Mermaid's state lexer reads label text as `":"[^:\n;]+`, so a time in a
 * note ("parabolic 1, 05:54:19.101") ends the label at "05" and the parse
 * fails with "Expecting ... got 'DESCR'".  The state-shape-fix pass that
 * once declared "05" and "54" as states is gone entirely (see
 * mermaidStateNoInjectedDescriptions.test.ts); the pipeline test below
 * still guards against phantom `05: 05` lines.
 *
 * DIRECTION: the reported definition is asserted to carry raw colons in
 * label text; without the escape pass the pipeline output still has them.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';
import { escapeStateLabelText } from '../mermaidStateLabelText';

beforeAll(() => {
  initMermaidEnhancer();
});

const REPORTED =
  'stateDiagram-v2\n' +
  '    [*] --> Up\n' +
  '    Up --> Init : miss ×1\n' +
  '    Init --> Init : miss ×2\n' +
  '    Init --> Down : miss ×3\n' +
  '    Down --> Up : 3 consecutive echoes\n' +
  '    note right of Down : parabolic 1, 05:54:19.101';

describe('escapeStateLabelText', () => {
  it('escapes colons in single-line note text and leaves other lines alone', () => {
    const out = escapeStateLabelText(REPORTED);
    expect(out).toContain('note right of Down : parabolic 1, 05#58;54#58;19.101');
    const before = REPORTED.split('\n');
    const after = out.split('\n');
    expect(after).toHaveLength(before.length);
    expect(after.slice(0, 6)).toEqual(before.slice(0, 6));
  });

  it('escapes colons in transition labels and state descriptions', () => {
    expect(escapeStateLabelText('    A --> B : at 05:54')).toBe('    A --> B : at 05#58;54');
    expect(escapeStateLabelText('    A-->B:x:y')).toBe('    A-->B:x#58;y');
    expect(escapeStateLabelText('    Up:::hot --> X : a:b')).toBe('    Up:::hot --> X : a#58;b');
    expect(escapeStateLabelText('    Up : at 05:54')).toBe('    Up : at 05#58;54');
  });

  it('escapes a prose semicolon in label text', () => {
    expect(escapeStateLabelText('    A --> B : one; two')).toBe('    A --> B : one#59; two');
  });

  // The state lexer's ID rule takes `;`, so a separator `;` adds a state
  // named ";" and a terminator turns `B;` into a state named "B;".
  it('splits separator semicolons onto their own lines at the same indent', () => {
    expect(escapeStateLabelText('    B --> C ; C --> D')).toBe('    B --> C\n    C --> D');
    expect(escapeStateLabelText('    A --> B : x ; C --> D : y:z'))
      .toBe('    A --> B : x\n    C --> D : y#58;z');
    expect(escapeStateLabelText('  note right of D : a:b; C --> D'))
      .toBe('  note right of D : a#58;b\n  C --> D');
  });

  it('drops a trailing statement terminator', () => {
    expect(escapeStateLabelText('    A --> B;')).toBe('    A --> B');
    expect(escapeStateLabelText('    A --> B : done;')).toBe('    A --> B : done');
    expect(escapeStateLabelText('    A --> B; B --> C : t:1;'))
      .toBe('    A --> B\n    B --> C : t#58;1');
  });

  it('leaves multi-line note bodies, directives and quoted state names untouched', () => {
    const src =
      'stateDiagram-v2\n' +
      '    note left of Up\n' +
      '        multi 05:54:19; x\n' +
      '    end note\n' +
      '    classDef hot fill:#f00,color:#fff\n' +
      '    state "a: b" as X\n' +
      '    accTitle: a:b\n' +
      '    %% comment: x:y';
    expect(escapeStateLabelText(src)).toBe(src);
    // The note block ends at `end note`: a description after it is processed.
    expect(escapeStateLabelText(src + '\n    Q : r:s')).toBe(src + '\n    Q : r#58;s');
  });

  it('is idempotent and preserves existing entities', () => {
    const once = escapeStateLabelText('    A --> B : 05#58;54 then 1:2; ok');
    expect(once).toBe('    A --> B : 05#58;54 then 1#58;2#59; ok');
    expect(escapeStateLabelText(once)).toBe(once);
  });
});

// collectStateShapeNames was removed with state-shape-fix; see
// mermaidStateNoInjectedDescriptions.test.ts.

describe('pipeline: reported state diagram', () => {
  it('escapes the note colons and declares no phantom states', () => {
    expect(REPORTED).toMatch(/05:54:19/);
    const out = preprocessDefinition(REPORTED, 'stateDiagram-v2');
    expect(out).toContain('05#58;54#58;19.101');
    expect(out).not.toMatch(/05:54/);
    expect(out).not.toMatch(/^\s*05\s*:/m);
    expect(out).not.toMatch(/^\s*54\s*:/m);
    // The real states and transitions survive.
    expect(out).toMatch(/Init --> Down : miss ×3/);
    expect(out).toMatch(/note right of Down :/);
  });

  it('leaves no semicolon where mermaid would read it as part of a state name', () => {
    const src = 'stateDiagram-v2\n    A --> B;\n    B --> C ; C --> D';
    const out = preprocessDefinition(src, 'stateDiagram-v2');
    expect(out).not.toContain(';');
    expect(out).toMatch(/^\s*A --> B\s*$/m);
    expect(out).toMatch(/^\s*B --> C\s*$/m);
    expect(out).toMatch(/^\s*C --> D\s*$/m);
  });
});
