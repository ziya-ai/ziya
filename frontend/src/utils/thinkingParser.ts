/**
 * Frontend thinking content parser to handle <thinking-data> and <thinking> tags
 */

export interface ThinkingContent {
    content: string;
}

export function parseThinkingContent(content: string): ThinkingContent | null {
    // Try thinking-data tags first (for deepseek-r1)
    let thinkingPattern = /<thinking-data>([\s\S]*?)<\/thinking-data>/;
    let match = content.match(thinkingPattern);
    
    if (match) {
        return {
            content: match[1] // Don't trim to preserve formatting
        };
    }
    
    // Try thinking tags (for nova-pro)
    thinkingPattern = /<thinking>([\s\S]*?)<\/thinking>/;
    match = content.match(thinkingPattern);
    
    if (match) {
        return {
            content: match[1] // Don't trim to preserve formatting
        };
    }
    
    return null;
}

/**
 * Run a transform on markdown text while preserving fenced code blocks.
 * Fenced blocks (``` or longer) are replaced with placeholders before the
 * transform runs, then restored afterwards so their content is never touched.
 */
function outsideCodeBlocks(text: string, transform: (s: string) => string): string {
    const { stripped, restore } = protectFences(text);
    return restore(transform(stripped));
}

/**
 * Swap fenced blocks for placeholders, returning the stripped text and an
 * explicit restore function.
 *
 * The restore step is exposed rather than applied only at the end because a
 * transform that CAPTURES text (rather than just rewriting around it) must
 * un-placeholder its capture before encoding it.  encodeThinkingBlocks
 * base64-encodes the reasoning payload; with restore available only after
 * the transform, a placeholder inside that payload was frozen into the
 * base64 where the restore pass could never reach it, so reasoning
 * containing a code fence rendered a literal \x00CODEBLOCK0\x00 to the user.
 */
function protectFences(text: string): {
    stripped: string;
    restore: (s: string) => string;
} {
    const blocks: string[] = [];
    const stripped = text.replace(/(`{3,})[^\n]*\n[\s\S]*?\1/g, (match) => {
        blocks.push(match);
        return `\x00CODEBLOCK${blocks.length - 1}\x00`;
    });
    const restore = (s: string) =>
        s.replace(/\x00CODEBLOCK(\d+)\x00/g, (_, i) => blocks[Number(i)]);
    return { stripped, restore };
}

export function removeThinkingTags(content: string): string {
    // Remove thinking-data / thinking blocks only outside fenced code blocks,
    // so that diff or code content mentioning these tags is not destroyed.
    return outsideCodeBlocks(content, (text) => text
        .replace(/<thinking-data>[\s\S]*?<\/thinking-data>\s*/g, '')
        .replace(/<thinking>[\s\S]*?<\/thinking>\s*/g, '')
    )
        // Remove fence-based thinking blocks created by mcpToolHandlers.ts
        .replace(/(`{4,})thinking:[^\n]*\n[\s\S]*?\1\s*/g, '');
}
