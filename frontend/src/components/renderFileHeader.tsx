import { parseDiff } from 'react-diff-view';

export const renderFileHeader = (file: ReturnType<typeof parseDiff>[number], fileIndex?: number): string => {
    // If we have paths in the file object, use them directly
    if (file.oldPath || file.newPath) {
        const path = file.newPath || file.oldPath;
        return `File: ${path}`;
    }

    // If no paths in file object, try to extract from content
    if (file.hunks?.[0]?.content) {
        // Extract paths directly from the first few lines of the content
        const contentLines = file.hunks[0].content.split('\n').slice(0, 10);
        let oldPath: string | null = null;
        let newPath: string | null = null;
        
        // First check for git diff header
        const gitMatch = contentLines[0]?.match(/^diff --git a\/(.*?) b\/(.*?)$/);
        if (gitMatch && gitMatch.length >= 3) {
            oldPath = gitMatch[1];
            newPath = gitMatch[2];
        } else {
            // Look for unified diff headers
            for (const line of contentLines) {
                if (line.startsWith('--- ')) {
                    if (line.startsWith('--- /dev/null')) {
                        oldPath = null; // New file
                    } else {
                        const match = line.match(/^--- (?:a\/)?(.+)$/);
                        if (match && match[1]) oldPath = match[1];
                    }
                } 
                else if (line.startsWith('+++ ')) {
                    if (line.startsWith('+++ /dev/null')) {
                        newPath = null; // Deleted file
                    } else {
                        const match = line.match(/^\+\+\+ (?:b\/)?(.+)$/);
                        if (match && match[1]) newPath = match[1];
                    }
                }
                
                // Stop if we hit a hunk header
                if (line.startsWith('@@ ')) break;
            }
        }

        // Handle file operations based on paths
        if ((oldPath === null || oldPath === '/dev/null') && newPath) {
            return `Create: ${newPath}`;
        } else if (oldPath && newPath && oldPath !== newPath) {
            return `Rename: ${oldPath} -> ${newPath}`;
        } else if ((oldPath && !newPath) || (oldPath && newPath === '/dev/null')) {
            return `Delete: ${oldPath}`;
        } else if (oldPath || newPath) {
            return `File: ${newPath || oldPath}`;
        }
    }

    // Fallback for any other cases
    return 'Unknown file operation';
};
