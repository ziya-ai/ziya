            // Fix 0b: Code fence after any markdown formatting without blank line
            // Fix 3: Code fence glued directly to preceding text with NO newline at all
            // e.g., "curve:
            processedMarkdown = processedMarkdown.replace(/(\*\*)\n(```)/g, '$1\n\n$2');

            // Fix 1: Code fence on same line as heading (e.g., "### Title ```language")
            processedMarkdown = processedMarkdown.replace(
                /(^#{1,6}\s+[^\n\`]+?)\s+(\`\`\`[a-zA-Z0-9_-]*)(?=\s|$)/gm,
                '$1\n\n$2'
            );

            // Fix 2: Code fence immediately after numbered list (e.g., "1. Item ```language")
            processedMarkdown = processedMarkdown.replace(
                /(\d+\.\s+[^\n\`]+?)\s+(\`\`\`[a-zA-Z0-9_-]*)(?=\s|$)/gm,
                '$1\n\n$2'
            );

            // Also fix after paragraphs or text that directly precedes code fences
            processedMarkdown = processedMarkdown.replace(/([^\n])\n(\`\`\`[a-zA-Z0-9_-]*)/g, '$1\n\n$2');

            // Fix: Code fence directly concatenated to text with no newline at all
            // e.g. "some text:```vega-lite" → "some text:\n\n```vega-lite"
            // LLMs sometimes omit the newline before a code fence entirely
            processedMarkdown = processedMarkdown.replace(/([^\n\`])(\`{3,}[a-zA-Z][a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2');
