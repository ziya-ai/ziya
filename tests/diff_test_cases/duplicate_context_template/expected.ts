function processToolResult(toolName: string, headerCommand: string, content: string) {
    // Extract command/query for header display
    let headerCommand = '';
    const cleanToolName = toolName.replace('mcp_', '').replace(/_/g, ' ');
    
    if (storedInput?.command) {
        headerCommand = `${cleanToolName}: $ ${storedInput.command}`;
    } else if (storedInput?.searchQuery) {
        headerCommand = `${cleanToolName}: "${storedInput.searchQuery}"`;
    }

    if (!toolName.startsWith('mcp_')) {
        toolName = `mcp_${toolName}`;
    }
    toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');


    // Include command/query in the tool block for better visibility
    let toolResultDisplay;
    let toolStartPrefix;
    
    if (headerCommand) {
        toolResultDisplay = `\n\`\`\`tool:${toolName}|${headerCommand}\n${formatted.content}\n\`\`\`\n\n`;
        toolStartPrefix = `\n\`\`\`tool:${toolName}|${headerCommand}\n`;
    } else {
        toolResultDisplay = `\n\`\`\`tool:${toolName}\n${formatted.content}\n\`\`\`\n\n`;
        toolStartPrefix = `\n\`\`\`tool:${toolName}\n`;
    }
    
    const lastStartIndex = currentContent.lastIndexOf(toolStartPrefix);
    if (lastStartIndex !== -1) {
        const blockEndIndex = currentContent.indexOf('\n```\n\n', lastStartIndex);
        if (blockEndIndex !== -1) {
            currentContent = currentContent.substring(0, lastStartIndex) + toolResultDisplay + currentContent.substring(blockEndIndex + 6);
        }
    }
}

function processToolStart(toolName: string, headerCommand: string, content: string) {
    // Extract command/query for header display
    let headerCommand = '';
    const cleanToolName = toolName.replace('mcp_', '').replace(/_/g, ' ');
    
    if (storedInput?.command) {
        headerCommand = `${cleanToolName}: $ ${storedInput.command}`;
    } else if (storedInput?.searchQuery) {
        headerCommand = `${cleanToolName}: "${storedInput.searchQuery}"`;
    }

    if (!toolName.startsWith('mcp_')) {
        toolName = `mcp_${toolName}`;
    }
    toolName = toolName.replace(/^mcp_mcp_/, 'mcp_');


    // Include command/query in the tool block for better visibility
    const toolStartDisplay = headerCommand 
        ? `\n\`\`\`tool:${toolName}|${headerCommand}\n⏳ Running: ${headerCommand}\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n⏳ Running: ${toolName}\n\`\`\`\n\n`;
    
    const toolStartPrefix = headerCommand
        ? `\n\`\`\`tool:${toolName}|${headerCommand}\n`
        : `\n\`\`\`tool:${toolName}\n`;
    
    const lastStartIndex = currentContent.lastIndexOf(toolStartPrefix);
    if (lastStartIndex !== -1) {
        const blockEndIndex = currentContent.indexOf('\n```\n\n', lastStartIndex);
        if (blockEndIndex !== -1) {
            currentContent = currentContent.substring(0, lastStartIndex) + toolStartDisplay + currentContent.substring(blockEndIndex + 6);
        }
    }
}
