            // Fix 1: Code fence on same line as heading (e.g., "### Title ```language")
            processedMarkdown = processedMarkdown.replace(
                /(^#{1,6}\s+[^\n\`]+?)\s+(\`\`\`[a-zA-Z0-9_-]*)/gm,
                '$1\n\n$2'
            );
