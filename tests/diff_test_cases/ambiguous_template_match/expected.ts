// Block 1
const toolResultDisplay = headerCommand 
    ? `\n\`\`\`tool:${toolName}|${headerCommand}\n${formatted.content}\n\`\`\`\n\n`
    : `\n\`\`\`tool:${toolName}\n${formatted.content}\n\`\`\`\n\n`;

const toolStartPrefix = headerCommand
    ? `\n\`\`\`tool:${toolName}|${headerCommand}\n`
    : `\n\`\`\`tool:${toolName}\n`;

// Block 2
let toolResultDisplay;
if (headerCommand) {
    toolResultDisplay = `\n\`\`\`tool:${toolName}|${headerCommand}\n${formatted.content}\n\`\`\`\n\n`;
} else {
    toolResultDisplay = `\n\`\`\`tool:${toolName}\n${formatted.content}\n\`\`\`\n\n`;
}

let toolStartPrefix;
if (headerCommand) {
    toolStartPrefix = `\n\`\`\`tool:${toolName}|${headerCommand}\n`;
} else {
    toolStartPrefix = `\n\`\`\`tool:${toolName}\n`;
}

// Block 3
const toolResultDisplay = headerCommand
    ? `\n\`\`\`tool:${toolName}|${headerCommand}\n${formatted.content}\n\`\`\`\n\n`
    : `\n\`\`\`tool:${toolName}\n${formatted.content}\n\`\`\`\n\n`;

const toolStartPrefix = headerCommand
    ? `\n\`\`\`tool:${toolName}|${headerCommand}\n`
    : `\n\`\`\`tool:${toolName}\n`;
