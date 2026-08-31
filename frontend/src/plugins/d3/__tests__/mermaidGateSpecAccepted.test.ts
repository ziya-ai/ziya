/**
 * The gate-lifecycle flowchart below rendered as
 *   No compatible plugin found for visualization type "mermaid"
 * even though mermaid-renderer IS in the registry.  The error text blames a
 * canHandle predicate declining the spec, so this pins down whether that is
 * actually possible for this definition: it contains `{{...}}` hexagon nodes,
 * `<br/>`/`<b>` inline HTML and a unicode arrow, any of which a shape check
 * could plausibly trip over.
 *
 * If these pass, canHandle is exonerated and the undefined plugin must have
 * come from the loader (a rejected dynamic import that loadPlugin swallows),
 * not from spec inspection.
 */
import { mermaidPlugin } from '../mermaidPlugin';

const GATE_FLOWCHART = `flowchart TD
    R["running"] -->|"gate block reached<br/>no answer on record"| G["<b>awaiting_input</b><br/><i>frame held alive</i><br/>question persisted"]
    G -->|"human answers<br/>approve"| A["answer written to<br/>run.gate_answers[block_id]"]
    A --> V["applied like State:<br/>ctx.variables + ctx.context_notes"]
    V --> R2["running<br/><i>downstream reads {{var.X}}</i>"]
    G -->|"SERVER RESTART"| Q{{"reconcile_stale_runs"}}
    Q -->|"today: paused → failed"| X["<b>failed</b><br/>'did not survive restart'"]

    style G fill:#8957e5,color:#fff`;

describe('mermaid canHandle vs the gate-lifecycle flowchart', () => {
    it('accepts the exact spec MarkdownRenderer builds for a mermaid fence', () => {
        // Shape mirrors MarkdownRenderer.tsx case 'mermaid'.
        expect(mermaidPlugin.canHandle({
            type: 'mermaid',
            definition: GATE_FLOWCHART,
            isStreaming: false,
            isMarkdownBlockClosed: true,
            forceRender: true,
        })).toBe(true);
    });

    it('is not tripped by {{hexagon}} nodes, inline HTML, or unicode arrows', () => {
        expect(mermaidPlugin.canHandle({ type: 'mermaid', definition: 'flowchart TD\n  Q{{"x"}} --> Y' })).toBe(true);
        expect(mermaidPlugin.canHandle({ type: 'mermaid', definition: 'flowchart TD\n  A["<b>x</b><br/>y"] --> B' })).toBe(true);
        expect(mermaidPlugin.canHandle({ type: 'mermaid', definition: 'flowchart TD\n  A -->|"a → b"| B' })).toBe(true);
    });

    it('still declines specs that genuinely are not mermaid or carry no definition', () => {
        // Negative side, so the assertions above are not vacuously true.
        expect(mermaidPlugin.canHandle({ type: 'graphviz', definition: 'digraph {}' })).toBe(false);
        expect(mermaidPlugin.canHandle({ type: 'mermaid', definition: '   ' })).toBe(false);
        expect(mermaidPlugin.canHandle({ type: 'mermaid' })).toBe(false);
    });
});
