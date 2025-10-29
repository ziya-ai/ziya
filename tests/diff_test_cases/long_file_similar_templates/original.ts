// Tool handler for display events
function handleToolDisplay(toolName: string, data: any, context: any) {
    const storedInput = context.toolInputsMap.get(data.tool_id);
    
    // Format the output
    const formatted = formatOutput(toolName, data.result, storedInput);
    
    // Build display block
    const toolResultDisplay = storedInput?.command 
        ? `\n\`\`\`tool:${toolName}|$ ${storedInput.command}\n${formatted.content}\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n${formatted.content}\n\`\`\`\n\n`;
    
    return toolResultDisplay;
}

// Tool handler for start events
function handleToolStart(toolName: string, data: any, context: any) {
    const inputArgs = data.args || data.input || {};
    
    // Build start block
    const toolStartDisplay = inputArgs.command
        ? `\n\`\`\`tool:${toolName}|$ ${inputArgs.command}\n⏳ Running: $ ${inputArgs.command}\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n⏳ Running: ${toolName}\n\`\`\`\n\n`;
    
    return toolStartDisplay;
}

// Tool handler for error events
function handleToolError(toolName: string, data: any, context: any) {
    const errorMsg = data.error || 'Unknown error';
    
    // Build error block
    const toolErrorDisplay = data.command
        ? `\n\`\`\`tool:${toolName}|$ ${data.command}\n❌ Error: ${errorMsg}\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n❌ Error: ${errorMsg}\n\`\`\`\n\n`;
    
    return toolErrorDisplay;
}

// Tool handler for progress events
function handleToolProgress(toolName: string, data: any, context: any) {
    const progress = data.progress || 0;
    
    // Build progress block
    const toolProgressDisplay = data.query
        ? `\n\`\`\`tool:${toolName}|"${data.query}"\n⏳ Progress: ${progress}%\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n⏳ Progress: ${progress}%\n\`\`\`\n\n`;
    
    return toolProgressDisplay;
}
