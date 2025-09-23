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

export function removeThinkingTags(content: string): string {
    // Remove both thinking-data and thinking blocks, preserving spacing
    return content
        .replace(/<thinking-data>[\s\S]*?<\/thinking-data>\s*/g, '')
        .replace(/<thinking>[\s\S]*?<\/thinking>\s*/g, '');
}
