/**
 * Frontend thinking content parser to handle <thinking-data> tags
 */

export interface ThinkingContent {
    content: string;
}

export function parseThinkingContent(content: string): ThinkingContent | null {
    // Use non-greedy matching to capture all content including newlines
    const thinkingPattern = /<thinking-data>([\s\S]*?)<\/thinking-data>/;
    const match = content.match(thinkingPattern);
    
    if (match) {
        return {
            content: match[1] // Don't trim to preserve formatting
        };
    }
    
    return null;
}

export function removeThinkingTags(content: string): string {
    // Remove all thinking-data blocks, preserving spacing
    return content.replace(/<thinking-data>[\s\S]*?<\/thinking-data>\s*/g, '');
}
