/**
 * Frontend tool call parser to handle both <n> and <name> formats
 */

export interface ToolCall {
    toolName: string;
    arguments: Record<string, any>;
}

export function parseToolCall(content: string): ToolCall | null {
    // Handle <name> format
    const namePattern = /<TOOL_SENTINEL>\s*<name>([^<]+)<\/name>\s*<arguments>\s*(\{.*?\})\s*<\/arguments>\s*<\/TOOL_SENTINEL>/s;
    let match = content.match(namePattern);
    
    if (match) {
        try {
            return {
                toolName: match[1].trim(),
                arguments: JSON.parse(match[2])
            };
        } catch (e) {
            console.warn('Failed to parse tool arguments:', e);
        }
    }
    
    // Handle <n> format
    const nPattern = /<TOOL_SENTINEL>\s*<n>([^<]+)<\/n>\s*<arguments>\s*(\{.*?\})\s*<\/arguments>\s*<\/TOOL_SENTINEL>/s;
    match = content.match(nPattern);
    
    if (match) {
        try {
            return {
                toolName: match[1].trim(),
                arguments: JSON.parse(match[2])
            };
        } catch (e) {
            console.warn('Failed to parse tool arguments:', e);
        }
    }
    
    return null;
}

export function formatToolCallForDisplay(toolCall: ToolCall): string {
    const args = JSON.stringify(toolCall.arguments, null, 2);
    return `🔧 **${toolCall.toolName}**\n\`\`\`json\n${args}\n\`\`\``;
}
