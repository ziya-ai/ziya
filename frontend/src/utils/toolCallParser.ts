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
            const argsText = match[2].trim();
            
            // During streaming, check if JSON is complete before parsing
            // Count braces to ensure we have balanced JSON
            const openBraces = (argsText.match(/\{/g) || []).length;
            const closeBraces = (argsText.match(/\}/g) || []).length;
            
            if (openBraces !== closeBraces || openBraces === 0) {
                // Incomplete JSON - don't parse yet during streaming
                return null;
            }
            
            return {
                toolName: match[1].trim(),
                arguments: JSON.parse(argsText)
            };
        } catch (e) {
            // Silently return null for incomplete JSON during streaming
            return null;
        }
    }
    
    // Handle <n> format
    const nPattern = /<TOOL_SENTINEL>\s*<n>([^<]+)<\/n>\s*<arguments>\s*(\{.*?\})\s*<\/arguments>\s*<\/TOOL_SENTINEL>/s;
    match = content.match(nPattern);
    
    if (match) {
        try {
            const argsText = match[2].trim();
            
            // During streaming, check if JSON is complete before parsing
            const openBraces = (argsText.match(/\{/g) || []).length;
            const closeBraces = (argsText.match(/\}/g) || []).length;
            
            if (openBraces !== closeBraces || openBraces === 0) {
                // Incomplete JSON - don't parse yet during streaming
                return null;
            }
            
            return {
                toolName: match[1].trim(),
                arguments: JSON.parse(argsText)
            };
        } catch (e) {
            // Silently return null for incomplete JSON during streaming
            return null;
        }
    }
    
    return null;
}

export function formatToolCallForDisplay(toolCall: ToolCall): string {
    const args = JSON.stringify(toolCall.arguments, null, 2);
    return `🔧 **${toolCall.toolName}**\n\`\`\`json\n${args}\n\`\`\``;
}
