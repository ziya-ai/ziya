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
    const blocks: string[] = [];
    const placeholder = (i: number) => `\x00CODEBLOCK${i}\x00`;
    const stripped = text.replace(/(`{3,})[^\n]*\n[\s\S]*?\1/g, (match) => {
        blocks.push(match);
        return placeholder(blocks.length - 1);
    });
    const transformed = transform(stripped);
    return transformed.replace(/\x00CODEBLOCK(\d+)\x00/g, (_, i) => blocks[Number(i)]);
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
