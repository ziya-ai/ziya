// Handler 1
function handleToolDisplay(toolName: string, data: any) {
    const display = data.command 
        ? `\n\`\`\`tool:${toolName}|$ ${data.command}\n${data.content}\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n${data.content}\n\`\`\`\n\n`;
    return display;
}

// Handler 2
function handleToolStart(toolName: string, data: any) {
    const display = data.command 
        ? `\n\`\`\`tool:${toolName}|$ ${data.command}\n⏳ Running\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n⏳ Running\n\`\`\`\n\n`;
    return display;
}

// Handler 3
function handleToolError(toolName: string, data: any) {
    let display;
    if (data.command) {
        display = `\n\`\`\`tool:${toolName}|$ ${data.command}\n❌ Error\n\`\`\`\n\n`;
    } else {
        display = `\n\`\`\`tool:${toolName}\n❌ Error\n\`\`\`\n\n`;
    }
    return display;
}

// Handler 4
function handleToolProgress(toolName: string, data: any) {
    const display = data.command 
        ? `\n\`\`\`tool:${toolName}|$ ${data.command}\n⏳ Progress\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n⏳ Progress\n\`\`\`\n\n`;
    return display;
}

// Handler 5
function handleToolComplete(toolName: string, data: any) {
    const display = data.command 
        ? `\n\`\`\`tool:${toolName}|$ ${data.command}\n✓ Complete\n\`\`\`\n\n`
        : `\n\`\`\`tool:${toolName}\n✓ Complete\n\`\`\`\n\n`;
    return display;
}
