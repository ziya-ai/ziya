/**
 * renderToolPart: the one function that turns a tool call into the markdown
 * the chat shows, used by BOTH the live stream (chatApi.ts tool_display
 * handler) and, from step 3 of "Turn authorship", the saved-message renderer
 * (design/conversation-single-writer.md, "save facts, render at display
 * time").  Sharing it is what keeps a message identical live and after
 * reload.
 *
 * The golden strings below were produced by the ORIGINAL inline block in
 * chatApi.ts before extraction (2026-09-25), not by this function, so they
 * pin the live streaming output.  During extraction the function was also
 * checked equal to the verbatim original across 95 event/preceding-text
 * combinations, with four planted mutations each detected.
 */
import * as fs from 'fs';
import * as path from 'path';
import { renderToolPart, toolPartFromEvent, ToolPart } from '../renderToolPart';

const map = (o: Record<string, any> = {}) => new Map(Object.entries(o));
const fromEvent = (ev: any, stored: Record<string, any> = {}) => toolPartFromEvent(ev, map(stored));

describe('renderToolPart matches the pre-extraction live output', () => {
  it('plain result, text without a trailing blank line', () => {
    expect(renderToolPart(fromEvent({ tool_id: 't1', tool_name: 'file_read', result: 'hello' }), 'text')).toEqual({
      display: '\n\n````tool:mcp_file_read|File Read|text\nhello\n````\n\n',
      toolName: 'mcp_file_read', displayHeader: 'File Read', isShellCommandTool: false,
    });
  });

  it('only one leading newline when the text already ends in a blank line', () => {
    expect(renderToolPart(fromEvent({ tool_id: 't1', tool_name: 'file_read', result: 'hello' }), 'text\n\n').display)
      .toBe('\n````tool:mcp_file_read|File Read|text\nhello\n````\n\n');
  });

  it('shell: command header, $ line, backtick sanitized in the header only, verified mark', () => {
    const r = renderToolPart(fromEvent(
      { tool_id: 's1', tool_name: 'run_shell_command', result: 'out', verified: true },
      { s1: { command: 'ls `x`' } }), '');
    expect(r).toEqual({
      display: "\n\n````tool:mcp_run_shell_command|🔐 Shell: ls 'x'|text\n$ ls `x`\nout\n````\n\n",
      toolName: 'mcp_run_shell_command', displayHeader: "🔐 Shell: ls 'x'", isShellCommandTool: true,
    });
  });

  it('structured results travel as JSON between TOOL_BLOCK markers', () => {
    const structured = JSON.stringify({
      _isStructuredToolResult: true, summary: 'Found 2', type: 'search_results',
      hierarchicalResults: [{ title: 'a', children: [] }],
    });
    expect(renderToolPart(fromEvent({ tool_id: 'h1', tool_name: 'search', result: structured, verified: true }), '').display)
      .toBe('\n\n<!-- TOOL_BLOCK_START:mcp_search|🔐 Search|h1 -->\n'
        + '{"_isStructuredToolResult":true,"_verified":true,"summary":"Found 2","type":"search_results","hierarchicalResults":[{"title":"a","children":[]}]}\n'
        + '<!-- TOOL_BLOCK_END:mcp_search|h1 -->\n\n');
  });

  it('inline image precedes the tool block', () => {
    expect(renderToolPart(fromEvent(
      { tool_id: 'i1', tool_name: 'render_diagram', result: 'ok', image_data: 'data:image/png;base64,AA' }), 'x\n\n').display)
      .toBe('\n\n<img src="data:image/png;base64,AA" alt="Rendered diagram" style="max-width:100%;border-radius:8px;margin:8px 0;" />\n\n'
        + '\n````tool:mcp_render_diagram|Render Diagram|text\nok\n````\n\n');
  });
});

describe('toolPartFromEvent', () => {
  it('captures the tool_start facts so a saved part renders without the live map', () => {
    const part = fromEvent(
      { tool_id: 's1', tool_name: 'run_shell_command', result: 'out', verified: true, syntax: 'bash' },
      { s1: { command: 'ls' }, s1_header: 'Shell' });
    expect(part).toMatchObject<Partial<ToolPart>>({
      type: 'tool', toolId: 's1', startArgs: { command: 'ls' }, startHeader: 'Shell', verified: true, syntax: 'bash',
    });
    // The part alone reproduces the live rendering: nothing else is consulted.
    const viaPart = renderToolPart(JSON.parse(JSON.stringify(part)), 'p');
    const live = renderToolPart(fromEvent(
      { tool_id: 's1', tool_name: 'run_shell_command', result: 'out', verified: true, syntax: 'bash' },
      { s1: { command: 'ls' }, s1_header: 'Shell' }), 'p');
    expect(viaPart).toEqual(live);
  });
});

describe('chatApi.ts renders tool results through renderToolPart (seam)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', '..', 'apis', 'chatApi.ts'), 'utf-8');
  // The main tool_display handler: from the specialized-handler dispatch to
  // the tool_start branch that follows it.
  const start = src.indexOf('if (handleToolDisplay(unwrappedData.tool_name, unwrappedData, context))');
  const end = src.indexOf("} else if (unwrappedData.type === 'tool_start')", start);
  const block = src.slice(start, end);

  it('locates the handler', () => {
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    // Positive control: the live-only placeholder splice is still here.
    expect(block).toContain('TOOL_MARKER:');
  });

  it('builds the block with the shared function', () => {
    expect(block).toContain('renderToolPart(');
    expect(block).toContain('toolPartFromEvent(unwrappedData, toolInputsMap)');
  });

  it('no longer carries a second copy of the rendering', () => {
    expect(block).not.toContain('formatMCPOutput(');
    expect(block).not.toContain('resultFence');
  });
});
