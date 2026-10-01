/**
 * Tool parts: the saved form of a tool call inside an assistant message, and
 * the one function that turns a part into the markdown the chat renders.
 *
 * design/conversation-single-writer.md, "save facts, render at display
 * time".  The server will save an assistant turn as `parts` (text segments
 * and tool calls, as they happened) instead of the browser's rendering of
 * them.  To keep the streaming experience identical, the live stream and the
 * saved-message renderer must produce the same string for the same tool
 * call.  They do so by construction: the live `tool_display` handler in
 * chatApi.ts builds a ToolPart from the event and calls renderToolPart, and
 * the saved renderer calls renderToolPart on the stored part.
 *
 * Extracted verbatim from chatApi.ts's tool_display handler (2026-09-25).
 * The splice that replaces the TOOL_MARKER placeholder stays in chatApi.ts:
 * it is a live-stream concern, and a saved part has no placeholder.
 */
import { formatMCPOutput, stripRedundantCdForDisplay } from './mcpFormatter';

/** A text segment of an assistant turn, in emission order. */
export interface TextPart {
  type: 'text';
  text: string;
}

/** One tool call: everything the server knew when it ran it. */
export interface ToolPart {
  type: 'tool';
  toolId: string;
  /** As the server named it (the renderer normalizes the `mcp_` prefix). */
  toolName: string;
  /** Raw result text, before any display formatting or truncation. */
  result: unknown;
  /** Arguments as sent on the tool_start event. */
  startArgs?: Record<string, any>;
  /** Header as sent on the tool_start event. */
  startHeader?: string;
  /** Arguments echoed on the tool_display event, when present. */
  args?: Record<string, any>;
  /** Header echoed on the tool_display event, when present. */
  displayHeader?: string;
  verified?: boolean;
  verificationError?: string;
  /** Backend syntax hint for highlighting the result body. */
  syntax?: string;
  /** Base64 data URI for an inline rendered image (diagram_render). */
  imageData?: string;
}

export type MessagePart = TextPart | ToolPart;

/** How a turn ended, as recorded by the server. */
export type TurnStatus =
  | 'streaming' | 'complete' | 'stopped' | 'superseded' | 'error' | 'interrupted';

/** Build the part the live stream is about to render, from a tool_display
 *  event and the tool_start facts chatApi keeps in toolInputsMap. */
export function toolPartFromEvent(
  event: any,
  toolInputsMap: { get(key: string): any },
): ToolPart {
  return {
    type: 'tool',
    toolId: event.tool_id,
    toolName: event.tool_name,
    result: event.result,
    startArgs: toolInputsMap.get(event.tool_id),
    startHeader: toolInputsMap.get(`${event.tool_id}_header`),
    args: event.args,
    displayHeader: event.display_header,
    verified: event.verified === true,
    verificationError: event.verification_error,
    syntax: event.syntax,
    imageData: event.image_data,
  };
}

export interface RenderedToolPart {
  /** The markdown block for this tool call, including leading/trailing newlines. */
  display: string;
  /** Normalized (`mcp_`-prefixed) tool name, used to locate the live placeholder. */
  toolName: string;
  /** Final header text, used to locate a legacy placeholder. */
  displayHeader: string;
  isShellCommandTool: boolean;
}

/**
 * Render one tool part.
 *
 * `contentSoFar` is the message text this block is being added to.  Only its
 * ending is read: the block starts with one or two newlines depending on
 * whether the text already ends in a blank line.
 */
export function renderToolPart(part: ToolPart, contentSoFar: string): RenderedToolPart {
  const isVerified = part.verified === true;
  const verificationError = part.verificationError;
  let toolName = part.toolName;
  const storedInput = part.startArgs;
  const storedHeader = part.startHeader;

  // Normalize tool name
  if (!toolName.startsWith('mcp_')) {
    toolName = `mcp_${toolName}`;
  }
  toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');

  const actualToolName = toolName.replace('mcp_', '');
  const isShellCommandTool = actualToolName === 'run_shell_command';

  // Build display header with proper fallback chain
  let displayHeader = storedHeader;
  if (!displayHeader) {
    displayHeader = part.displayHeader;
  }
  if (!displayHeader) {
    displayHeader = toolName.replace('mcp_', '').replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase());
  }

  // Enhance shell command headers with the actual command
  if (isShellCommandTool && storedInput?.command) {
    const displayCommand = stripRedundantCdForDisplay(storedInput.command);
    const cmdPreview = displayCommand.length > 50
      ? displayCommand.substring(0, 47) + '...'
      : displayCommand;
    displayHeader = `Shell: ${cmdPreview}`;
  }

  // Sanitize shell headers after all header logic: newlines in the header
  // break HTML comments and regex matching.
  if (isShellCommandTool && displayHeader) {
    displayHeader = displayHeader
      .replace(/\n/g, ' ')
      .replace(/\s+/g, ' ')
      // A backtick in the header lands in the ````tool:…|<header>|syntax
      // fence info string, which CommonMark (and our fenceScanner /
      // marked.js) reject as an opener — inverting fence state so the
      // block never closes and following prose is absorbed into it.
      .replace(/`/g, "'")
      .trim();
  }

  // Non-shell tools reach the same fence info string, so a backtick in ANY
  // tool's header poisons it identically.
  if (displayHeader) {
    displayHeader = displayHeader.replace(/`/g, "'");
  }

  let displayContent: any = part.result;

  // Parse result if it's a JSON string to extract the actual content
  if (typeof displayContent === 'string' && (displayContent.startsWith('{') || displayContent.startsWith('['))) {
    try {
      displayContent = JSON.parse(displayContent);
    } catch (e) {
      // Keep as string if parsing fails
    }
  }

  // For shell commands, add command as first line if not present
  if (actualToolName === 'run_shell_command' && storedInput?.command && !displayContent.startsWith('$ ')) {
    displayContent = `$ ${stripRedundantCdForDisplay(storedInput.command)}\n${displayContent}`;
  }

  // Lock icon only when the backend verified the tool response signature
  if (isVerified) {
    displayHeader = `🔐 ${displayHeader}`;
  }

  const inputForFormatter = part.args || storedInput;
  const imageDataUri: string | undefined = part.imageData;

  const formatted = formatMCPOutput(toolName, displayContent, inputForFormatter, {
    showInput: false,
    maxLength: 10000,
    defaultCollapsed: true
  });

  let toolResultContent: string;
  let toolResultDisplay: string;
  // When the formatter produced a unified diff (file_write patch mode) the
  // body is a diff, not the file's language, so the fence says so.
  const resultSyntax = formatted.renderAs === 'diff'
    ? 'diff'
    : (part.syntax || 'text');
  const resultFence = '`'.repeat(4);
  const needsExtraNewline = !contentSoFar.endsWith('\n\n');

  if (formatted.hierarchicalResults && formatted.hierarchicalResults.length > 0) {
    // Hierarchical results travel as a JSON structure, NOT as serialized
    // markdown, so MarkdownRenderer can show them (and the lock icon).
    toolResultContent = JSON.stringify({
      _isStructuredToolResult: true,
      _verified: isVerified,
      _verificationError: verificationError,
      summary: formatted.summary || formatted.content,
      type: formatted.type,
      hierarchicalResults: formatted.hierarchicalResults
    });
    toolResultDisplay = `${needsExtraNewline ? '\n\n' : '\n'}<!-- TOOL_BLOCK_START:${toolName}|${displayHeader}|${part.toolId} -->\n${toolResultContent}\n<!-- TOOL_BLOCK_END:${toolName}|${part.toolId} -->\n\n`;
  } else {
    // Default for ALL tools: code fence with syntax hint
    toolResultContent = formatted.content;
    toolResultDisplay = `${needsExtraNewline ? '\n\n' : '\n'}` +
      `${resultFence}tool:${toolName}|${displayHeader}|${resultSyntax}\n${toolResultContent}\n${resultFence}\n\n`;
  }

  // Inline image for diagram render results.
  if (imageDataUri) {
    const imgTag = `\n\n<img src="${imageDataUri}" alt="Rendered diagram" style="max-width:100%;border-radius:8px;margin:8px 0;" />\n\n`;
    toolResultDisplay = imgTag + toolResultDisplay;
  }

  return { display: toolResultDisplay, toolName, displayHeader: displayHeader as string, isShellCommandTool };
}
