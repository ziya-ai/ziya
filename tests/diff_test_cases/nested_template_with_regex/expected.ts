function findToolBlock(content: string, toolName: string, headerCmd?: string) {
    const pattern = toolName.replace(/^mcp_mcp_/, 'mcp_');
    let prefix;
    if (headerCmd) {
        prefix = `\n\`\`\`tool:${pattern}|${headerCmd}\n`;
    } else {
        prefix = `\n\`\`\`tool:${pattern}\n`;
    }
    const suffix = `\n\`\`\`\n\n`;
    const index = content.lastIndexOf(prefix);
    return index;
}
