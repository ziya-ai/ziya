/**
 * Diff utilities - helpers for extracting and processing diff content
 */

/**
 * Extract all file paths referenced in a diff
 */
export function extractAllFilesFromDiff(diffContent: string): string[] {
    const files: string[] = [];
    const newFiles = new Set<string>(); // Track new file creations
    const lines = diffContent.split('\n');

    // First pass: identify new file creations
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];

        // Check for new file mode marker
        if (line.includes('new file mode')) {
            // Look backwards and forwards for the file path
            for (let j = Math.max(0, i - 5); j < Math.min(lines.length, i + 5); j++) {
                const checkLine = lines[j];
                const plusMatch = checkLine.match(/^\+\+\+ b\/(.+)$/);
                if (plusMatch && plusMatch[1] !== '/dev/null') {
                    newFiles.add(plusMatch[1]);
                }
            }
        }
    }

    for (const line of lines) {
        // Helper to check if a path is the special /dev/null sentinel (with or without leading slash)
        const isDevNull = (p: string) => p === '/dev/null' || p === 'dev/null';
        // Extract from git diff headers
        const gitMatch = line.match(/^diff --git (?:a\/)?([^\s]+) (?:b\/)?([^\s]+)$/);
        if (gitMatch) {
            const oldPath = gitMatch[1];
            const newPath = gitMatch[2];
            if (!isDevNull(newPath)) files.push(newPath);
            if (!isDevNull(oldPath) && oldPath !== newPath) files.push(oldPath);
        }

        // Extract from unified diff headers as backup
        const minusMatch = line.match(/^--- a\/(.+)$/);
        if (minusMatch && !isDevNull(minusMatch[1])) {
            files.push(minusMatch[1]);
        }

        const plusMatch = line.match(/^\+\+\+ b\/(.+)$/);
        if (plusMatch && !isDevNull(plusMatch[1])) {
            files.push(plusMatch[1]);
        }
    }

    // Remove duplicates and filter out new file creations
    const uniqueFiles = [...new Set(files)];
    const existingFiles = uniqueFiles.filter(file =>
        !newFiles.has(file) &&
        // Filter out regex patterns and invalid filenames
        !file.includes('(?:') &&
        !file.includes('$/)') &&
        !file.includes('[^') &&
        !file.endsWith(');') &&
        !file.includes('\\')
    );

    return existingFiles;
}

/**
 * Check which files are in current context (local check, no API call)
 */
export function checkFilesInContext(
    filePaths: string[], 
    currentFiles: string[] = []
): { missingFiles: string[], availableFiles: string[] } {
    const missingFiles: string[] = [];
    const availableFiles: string[] = [];

    for (const filePath of filePaths) {
        // Clean up the file path (remove a/ or b/ prefixes from git diffs)
        let cleanPath = filePath.trim();
        if (cleanPath.startsWith('a/') || cleanPath.startsWith('b/')) {
            cleanPath = cleanPath.substring(2);
        }

        // Check if the file is in the current selected context
        const isInContext = currentFiles.some(currentFile =>
            currentFile === cleanPath ||
            cleanPath.startsWith(currentFile + '/') ||
            (currentFile.endsWith('/') && cleanPath.startsWith(currentFile))
        );

        if (isInContext) {
            availableFiles.push(cleanPath);
        } else {
            missingFiles.push(cleanPath);
        }
    }

    return { missingFiles, availableFiles };
}

/**
 * Extract a single file's diff from a multi-file diff
 */
export function extractSingleFileDiff(fullDiff: string, filePath: string): string {
    // If the diff doesn't contain multiple files, return it as is
    if (!fullDiff.includes("diff --git") || fullDiff.indexOf("diff --git") === fullDiff.lastIndexOf("diff --git")) {
        return fullDiff;
    }

    try {
        const lines: string[] = fullDiff.split('\n');
        const result: string[] = [];

        // Clean up file path for matching
        const cleanFilePath = filePath.replace(/^[ab]\//, '');

        let inTargetFile = false;
        let collectingHunk = false;
        let currentHunkHeader: string | null = null;
        let currentHunkContent: string[] = [];

        // Process each line
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const nextLine = i < lines.length - 1 ? lines[i + 1] : '';

            // Check for file header
            if (line.startsWith('diff --git')) {
                // If we were collecting a hunk, add it to the result
                if (collectingHunk && inTargetFile && currentHunkHeader !== null) {
                    result.push(currentHunkHeader);
                    result.push(...currentHunkContent);
                }

                // Reset state for new file
                collectingHunk = false;
                currentHunkHeader = null;
                currentHunkContent = [];
                inTargetFile = false;

                // Check if this is our target file
                const fileMatch = line.match(/diff --git (?:a\/)?([^\/]*(?:\/[^\/]*)*) (?:b\/)?(.*)$/);
                if (fileMatch) {
                    const oldPath = fileMatch[1];
                    const newPath = fileMatch[2];

                    // Check if this file matches our target
                    if (oldPath === cleanFilePath || newPath === cleanFilePath ||
                        oldPath.endsWith(`/${cleanFilePath}`) || newPath.endsWith(`/${cleanFilePath}`)) {
                        inTargetFile = true;
                        result.push(line);

                        // Also check the next line for index info
                        if (nextLine.startsWith('index ')) {
                            result.push(nextLine);
                            i++; // Skip this line in the next iteration
                        }
                    }
                }
            }
            // If we're in the target file, collect all headers and content
            else if (inTargetFile) {
                // File headers (index, ---, +++)
                if (line.startsWith('index ') || line.startsWith('--- ') || line.startsWith('+++ ')) {
                    result.push(line);
                }
                // Hunk header
                else if (line.startsWith('@@ ')) {
                    // If we were collecting a previous hunk, add it to the result
                    if (collectingHunk && currentHunkHeader !== null) {
                        result.push(currentHunkHeader);
                        result.push(...currentHunkContent);
                    }

                    // Start collecting a new hunk
                    collectingHunk = true;
                    currentHunkHeader = line;
                    currentHunkContent = [];
                }
                // Hunk content (context, additions, deletions)
                else if (collectingHunk && (line.startsWith(' ') || line.startsWith('+') || line.startsWith('-') || line.startsWith('\\'))) {
                    currentHunkContent.push(line);
                }
                // Empty lines within a hunk
                else if (collectingHunk && line.trim() === '') {
                    currentHunkContent.push(line);
                }
            }
        }

        // Add the last hunk if we were collecting one
        if (collectingHunk && inTargetFile && currentHunkHeader !== null) {
            result.push(currentHunkHeader);
            result.push(...currentHunkContent);
        }

        // If we found our target file, return the extracted diff
        if (result.length > 0) {
            return result.join('\n').trim();
        }

        // If we didn't find the target file, return the original diff
        console.warn(`Could not find file ${cleanFilePath} in the diff`);
        return fullDiff;

    } catch (error) {
        console.error("Error extracting single file diff:", error);
        return fullDiff.trim();
    }
}
