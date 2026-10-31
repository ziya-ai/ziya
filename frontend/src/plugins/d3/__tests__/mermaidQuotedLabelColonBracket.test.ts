/**
 * Reported 2026-10-16: a fully quoted flowchart label containing JSON-ish text
 *
 *     B["... {backlog:[…], done:[…]} …"] --> D
 *
 * reached the lexer as `{backlog:[…], "done:"[…]}` and failed with
 * `Expecting 'SQE' ... got 'STR'`.  The colon-in-node-id-fix pass scans for
 * `id-with-colon[` and restarts after every `[` it consumes, so inside the
 * quoted label it resumed at `done:` -- a clean token none of its substring
 * guards rejected -- and quoted it as a node ID.  The source was valid
 * Mermaid; the enhancer must leave quoted labels alone.
 */
import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

const LABEL = `{backlog:[…], done:[…]} … update it last`;

const DEF = `flowchart TD
    A[System prompt:<br/>zero mention of dataset_register / analyze] --> D
    B["task_cards skill TEACHES the pattern:<br/>'FIRST file_read .ziya/&lt;card&gt;-state.json …<br/>${LABEL}'"] --> D
    D["Model reaches for what it knows"]
    style B fill:#fff2cc,stroke:#b7950b`;

describe('colon-in-node-id-fix leaves quoted labels alone', () => {
  beforeAll(() => { initMermaidEnhancer(); });

  it('does not quote word:[...] found inside an already-quoted label', () => {
    const out = preprocessDefinition(DEF, 'flowchart');
    expect(out).not.toContain('"done:"');
    expect(out).toContain(LABEL);
  });

  it('still quotes a genuinely unquoted colon ID, without touching the same token inside a label', () => {
    const def = `flowchart TD
    done:[Finished] --> B
    B["see done:[…] above"]`;
    const out = preprocessDefinition(def, 'flowchart');
    // The bare node ID is quoted (the pass's purpose)...
    expect(out).toMatch(/"done:"\s*\[/);
    // ...and the identical token inside B's label is not.
    expect(out).toContain('see done:[…] above');
  });
});
