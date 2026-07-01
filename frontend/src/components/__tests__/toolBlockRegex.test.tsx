/**
 * Regression tests for the TOOL_BLOCK_START/END parsing regexes in
 * MarkdownRenderer.tsx.
 *
 * This repo's convention (see sendToolFeedback.test.tsx) is to mirror the
 * exact logic under test rather than render the component, because the
 * renderer is a large non-pure component. Keep the patterns below in lockstep
 * with the two regexes in components/MarkdownRenderer.tsx (the inline-token
 * matcher near "HTMLtoolBlockMatch" and the global "toolBlockRegex").
 *
 * The production bug this fix closed: the tool_id segment of the marker was
 * hardcoded to the Bedrock/Anthropic prefix (toolu_). Tool results streamed by
 * OpenAI-format providers (z.ai / GLM via OpenAIDirectProvider) carry call_…
 * ids, so the marker span never matched and the raw
 * "<!-- TOOL_BLOCK_START:… -->" comment leaked into the rendered transcript.
 *
 * The generalized patterns treat the tool_id as the final |-delimited segment
 * before " -->": [^|>\s]+ — provider-agnostic, while keeping displayHeader's
 * (.+?) capture able to contain pipes (the engine backtracks so header pipes
 * stay in the header and only the trailing segment is the id).
 */

// Mirrors MarkdownRenderer.tsx HTMLtoolBlockMatch (single, non-global).
const TOOL_BLOCK_SINGLE =
    /<!-- TOOL_BLOCK_START:(mcp_\w+)\|(.+?)\|[^|>\s]+ -->\s*([\s\S]*?)\s*<!-- TOOL_BLOCK_END:\1\|[^|>\s]+ -->/;

// Mirrors MarkdownRenderer.tsx toolBlockRegex (global).
const buildGlobal = () =>
    /<!-- TOOL_BLOCK_START:(mcp_\w+)\|(.+?)\|[^|>\s]+ -->\s*([\s\S]*?)\s*<!-- TOOL_BLOCK_END:\1\|[^|>\s]+ -->/g;

const makeBlock = (toolName: string, header: string, id: string, content: string) =>
    `<!-- TOOL_BLOCK_START:${toolName}|${header}|${id} -->\n${content}\n<!-- TOOL_BLOCK_END:${toolName}|${id} -->`;

describe('TOOL_BLOCK marker parsing — provider-agnostic tool_id', () => {
    it('matches Bedrock/Anthropic toolu_ ids (legacy behavior preserved)', () => {
        const text = makeBlock('mcp_WorkspaceSearch', 'Search: foo', 'toolu_bdrk_01ABC', 'result body');
        const m = text.match(TOOL_BLOCK_SINGLE);
        expect(m).not.toBeNull();
        const [, toolName, displayHeader, toolContent] = m!;
        expect(toolName).toBe('mcp_WorkspaceSearch');
        expect(displayHeader).toBe('Search: foo');
        expect(toolContent).toBe('result body');
    });

    it('matches OpenAI / z.ai (GLM) call_ ids — the regression case', () => {
        const text = makeBlock('mcp_WorkspaceSearch', 'Search: foo', 'call_f44d607e0c674f39bfe11d5d', 'result body');
        const m = text.match(TOOL_BLOCK_SINGLE);
        expect(m).not.toBeNull();
        const [, toolName, displayHeader, toolContent] = m!;
        expect(toolName).toBe('mcp_WorkspaceSearch');
        expect(displayHeader).toBe('Search: foo');
        expect(toolContent).toBe('result body');
    });

    it('keeps pipe characters inside the displayHeader, not the tool_id', () => {
        const text = makeBlock('mcp_run_shell_command', 'Shell: grep a|b|c', 'call_xyz123', 'out');
        const m = text.match(TOOL_BLOCK_SINGLE);
        expect(m).not.toBeNull();
        const [, , displayHeader, toolContent] = m!;
        expect(displayHeader).toBe('Shell: grep a|b|c');
        expect(toolContent).toBe('out');
    });

    it('requires matching tool names on START and END (\\1 backreference)', () => {
        const text =
            '<!-- TOOL_BLOCK_START:mcp_A|h|call_1 -->\nbody\n<!-- TOOL_BLOCK_END:mcp_B|call_1 -->';
        expect(text.match(TOOL_BLOCK_SINGLE)).toBeNull();
    });

    it('does not match an orphan START with no END', () => {
        const text = '<!-- TOOL_BLOCK_START:mcp_A|h|call_1 -->\nbody with no end marker';
        expect(text.match(TOOL_BLOCK_SINGLE)).toBeNull();
    });

    it('global regex finds multiple blocks with mixed provider id formats', () => {
        const text =
            makeBlock('mcp_WorkspaceSearch', 'q1', 'toolu_bdrk_01', 'r1') +
            '\n\nsome prose between\n\n' +
            makeBlock('mcp_run_shell_command', 'q2', 'call_abc', 'r2');
        const re = buildGlobal();
        const found: Array<{ tool: string; header: string; content: string }> = [];
        let match: RegExpExecArray | null;
        while ((match = re.exec(text)) !== null) {
            found.push({ tool: match[1], header: match[2], content: match[3] });
        }
        expect(found).toHaveLength(2);
        expect(found[0]).toEqual({ tool: 'mcp_WorkspaceSearch', header: 'q1', content: 'r1' });
        expect(found[1]).toEqual({ tool: 'mcp_run_shell_command', header: 'q2', content: 'r2' });
    });
});
