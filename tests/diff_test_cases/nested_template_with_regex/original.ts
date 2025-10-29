function findToolBlock(content: string, toolName: string) {
    const pattern = toolName.replace(/^mcp_mcp_/, 'mcp_');
    const prefix = `\n\`\`\`tool:${pattern}\n`;
    const suffix = `\n\`\`\`\n\n`;
    const index = content.lastIndexOf(prefix);
    return index;
}
