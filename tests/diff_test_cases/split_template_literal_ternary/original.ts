function processToolResult(toolName: string, headerCommand: string, content: string) {
    // Original implementation with simple concatenation
    const toolResultDisplay = headerCommand 
        ? `\n\`\`\`tool:${toolName}|${headerCommand}\n${content}\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n${content}\n\`\`\`\n\n`;
    
    return toolResultDisplay;
}
