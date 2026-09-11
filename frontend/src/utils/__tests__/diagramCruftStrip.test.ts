/**
 * stripToolCallArtifacts -- removes tool-call / function-call sentinel tags an
 * LLM can leak into a fenced diagram body so the diagram still renders.
 *
 * The motivating case is a mermaid flowchart that ended with a stray
 * parameter/invoke close pair; the whole diagram rendered as a lexer error.
 * The riskiest case is drawio, whose definition is real XML -- the strip must
 * remove ONLY known sentinel tag names, never generic markup.
 */
import { stripToolCallArtifacts } from '../diagramUtils';

// Sentinel tags are assembled with \x3c ('<') so the literal tool-call markup
// never appears verbatim in source. The runtime strings are exactly the tags
// an LLM leaks into a fence.
const INVOKE_OPEN = '\x3cinvoke name="foo">';
const INVOKE_CLOSE = '\x3c/invoke>';
const PARAM_OPEN = '\x3cparameter name="definition">';
const PARAM_CLOSE = '\x3c/parameter>';
const FN_OPEN = '\x3cfunction_calls>';
const FN_CLOSE = '\x3c/function_calls>';
const SENTINEL_TAG = '\x3c/TOOL_SENTINEL>';
const SENTINEL_BRACKET = '[/TOOL_SENTINEL]';

describe('stripToolCallArtifacts', () => {
    it('removes a trailing parameter/invoke close pair after a mermaid body', () => {
        const input = [
            'flowchart TD',
            '    A["Merge"] --> B',
            '    style A fill:#ffe58f,stroke:#d48806',
            PARAM_CLOSE,
            INVOKE_CLOSE,
        ].join('\n');
        expect(stripToolCallArtifacts(input)).toBe(
            'flowchart TD\n    A["Merge"] --> B\n    style A fill:#ffe58f,stroke:#d48806',
        );
    });

    it('returns a clean mermaid definition unchanged', () => {
        const clean = 'flowchart TD\n    A --> B';
        expect(stripToolCallArtifacts(clean)).toBe(clean);
    });

    it('preserves a trailing newline on a clean body (trims only when a sentinel was removed)', () => {
        const clean = 'graph TD\nA-->B\n';
        expect(stripToolCallArtifacts(clean)).toBe(clean);
    });

    it('does not touch a legitimate less-than inside a mermaid label', () => {
        const input = 'flowchart TD\n    A["x \x3c y"] --> B';
        expect(stripToolCallArtifacts(input)).toBe(input);
    });

    it('keeps graphviz braces balanced after stripping a trailing sentinel', () => {
        const out = stripToolCallArtifacts('digraph G {\n  a -> b;\n}\n' + INVOKE_CLOSE);
        expect(out).toBe('digraph G {\n  a -> b;\n}');
        expect(out.split('{').length).toBe(out.split('}').length);
    });

    it('leaves valid drawio mxGraph XML intact (never strips generic tags)', () => {
        const xml = [
            '\x3cmxfile host="app.diagrams.net">',
            '  \x3cdiagram name="d">\x3cmxGraphModel>\x3croot>',
            '  \x3cmxCell id="0"/>\x3cmxCell id="1" parent="0"/>',
            '  \x3c/root>\x3c/mxGraphModel>\x3c/diagram>',
            '\x3c/mxfile>',
        ].join('\n');
        expect(stripToolCallArtifacts(xml)).toBe(xml);
    });

    it('strips a leaked sentinel after a drawio mxfile close without harming the XML', () => {
        const xml = '\x3cmxfile>\x3c/mxfile>';
        expect(stripToolCallArtifacts(xml + '\n' + PARAM_CLOSE + '\n' + INVOKE_CLOSE)).toBe(xml);
    });

    it('tolerates attributes and the antml namespace prefix', () => {
        const input = 'graph TD\nA-->B\n' + INVOKE_OPEN + '\n' + INVOKE_CLOSE;
        expect(stripToolCallArtifacts(input)).toBe('graph TD\nA-->B');
    });

    it('peels a parameter wrapper off both ends, keeping the body', () => {
        const input = PARAM_OPEN + '\ngraph TD\nA --> B\n' + PARAM_CLOSE;
        // The leading tag's trailing newline is consumed, so the body starts clean.
        expect(stripToolCallArtifacts(input)).toBe('graph TD\nA --> B');
    });

    it('removes function_calls wrappers from both boundaries', () => {
        const input = FN_OPEN + '\ngraph TD\nA-->B\n' + FN_CLOSE;
        expect(stripToolCallArtifacts(input)).toBe('graph TD\nA-->B');
    });

    it('leaves a sentinel-named tag in the INTERIOR of the body untouched', () => {
        // The whole point of boundary-only peeling: a close tag that lands
        // mid-body (here, followed by more diagram) is preserved byte-for-byte,
        // where the old global strip would have deleted it and split the body.
        const input = 'graph TD\n  A --> B\n  ' + INVOKE_CLOSE + '\n  C --> D';
        expect(stripToolCallArtifacts(input)).toBe(input);
    });

    it('removes both the tag and bracket forms of TOOL_SENTINEL', () => {
        expect(stripToolCallArtifacts('graph TD\nA-->B\n' + SENTINEL_TAG)).toBe('graph TD\nA-->B');
        expect(stripToolCallArtifacts('graph TD\nA-->B\n' + SENTINEL_BRACKET)).toBe('graph TD\nA-->B');
    });

    it('is idempotent', () => {
        const once = stripToolCallArtifacts('flowchart TD\n A-->B\n' + PARAM_CLOSE + '\n' + INVOKE_CLOSE);
        expect(stripToolCallArtifacts(once)).toBe(once);
    });

    it('handles empty / whitespace input without throwing', () => {
        expect(stripToolCallArtifacts('')).toBe('');
        expect(stripToolCallArtifacts('   ')).toBe('   ');
    });

    it('does NOT strip unrelated XML-ish markup that is not a known sentinel', () => {
        const input = 'digraph { a [label=\x3c\x3cb>x\x3c/b>>]; }';
        expect(stripToolCallArtifacts(input)).toBe(input);
    });
});
