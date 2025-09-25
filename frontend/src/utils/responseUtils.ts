/**
 * Utilities for detecting and handling incomplete responses
 */

export const detectIncompleteResponse = (content: string): boolean => {
    if (!content || content.trim().length < 100) {
        return false;
    }

    const contentLower = content.toLowerCase();
    const contentEnd = content.trim().slice(-200); // Last 200 characters
    const lines = content.split('\n');
    const lastLine = lines[lines.length - 1]?.trim() || '';

    // Strong indicators of incomplete responses
    const strongIndicators = [
        content.endsWith('...'),
        content.includes('**Note: This response may be incomplete'),
        content.includes('Feel free to ask me to continue'),
        content.includes('reached the iteration limit'),
        contentLower.includes('let me continue'),
        contentLower.includes('i need to continue'),
        contentLower.includes('to be continued'),
    ];

    // Moderate indicators (need multiple to trigger)
    const moderateIndicators = [
        // Unclosed code blocks
        (content.match(/```/g) || []).length % 2 !== 0,
        // Unclosed inline code (simple check)
        !content.includes('```') && (content.match(/`/g) || []).length % 2 !== 0,
        // Sentence ends with conjunction
        /\b(and|but|however|therefore|thus|so|as|because)\s*$/.test(contentEnd.toLowerCase()),
        // Very long last line without proper ending
        lastLine.length > 120 && !/[.!?```)\]}]$/.test(lastLine),
        // Single very long paragraph
        content.split('\n\n').length < 3 && content.length > 1500,
        // Mentions more details without proper conclusion
        contentLower.includes('more details') && !content.trim().endsWith('.'),
    ];

    // Return true if any strong indicator or multiple moderate indicators
    return strongIndicators.some(Boolean) || moderateIndicators.filter(Boolean).length >= 2;
};

export const generateContinuePrompt = (lastResponse: string): string => {
    // Analyze the last response to generate appropriate continuation prompt
    if (lastResponse.includes('```') && (lastResponse.match(/```/g) || []).length % 2 !== 0) {
        return "Please complete the code block and continue your response.";
    }
    
    if (lastResponse.toLowerCase().includes('let me') || lastResponse.toLowerCase().includes('i need to')) {
        return "Please continue.";
    }
    
    if (lastResponse.includes('more details') || lastResponse.includes('elaborate')) {
        return "Please provide the additional details and continue your explanation.";
    }
    
    return "Please continue your previous response.";
};
