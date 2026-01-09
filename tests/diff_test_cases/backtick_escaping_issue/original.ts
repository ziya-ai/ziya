export const sendPayload = async (
    payload: any
) => {
    const formatted = processResults(payload);
    
    if (formatted.hierarchicalResults) {
        const hierarchicalDisplay = formatted.hierarchicalResults.map((result, index) => {
            // Only use code fence for actual code content (not text/markdown)
            const isCode = result.language && result.language !== 'text' && result.language !== 'markdown';
            const resultContent = isCode
                ? `\`\`\`\`${result.language}\n${result.content}\n\`\`\`\``
                : result.content;

            // Clean formatting with title and indented content
            return `### ${result.title}\n\n${resultContent}`;
        }).join('\n\n---\n\n');
        
        return hierarchicalDisplay;
    }
    
    return payload;
};
