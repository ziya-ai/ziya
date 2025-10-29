function processToolResult(toolName: string, headerCommand: string, content: string) {
    // Refactored to avoid split template literals
    let toolResultDisplay;
    
    if (headerCommand) {
        toolResultDisplay = `\n\`\`\`tool:${toolName}|${headerCommand}\n${content}\n\`\`\`\n\n`;
    } else {
        toolResultDisplay = `\n\`\`\`tool:${toolName}\n${content}\n\`\`\`\n\n`;
    }
    
    return toolResultDisplay;
}
