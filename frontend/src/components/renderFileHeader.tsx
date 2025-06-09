import { parseDiff } from 'react-diff-view';

// Debug flag to control logging
const DEBUG_RENDER_FILE_HEADER = true;

// Cache for file headers to prevent disappearing during streaming
const fileHeaderCache = new Map<string, string>();

export const renderFileHeader = (file: ReturnType<typeof parseDiff>[number], originalDiffSegment?: string, fileIndex?: number): string => {
    if (DEBUG_RENDER_FILE_HEADER) {
        console.log('[renderFileHeader] Input:', {
            calledFrom: new Error().stack?.split('\n')[2]?.trim(),
            fileObject: JSON.parse(JSON.stringify(file)), // Deep copy for logging
            originalDiffSegmentPreview: originalDiffSegment ? originalDiffSegment.substring(0, 200) + (originalDiffSegment.length > 200 ? '...' : '') : 'undefined',
            fileIndex
        });
    }

    // Create a cache key for this file
    const cacheKey = `${fileIndex || 0}-${file?.oldPath || ''}-${file?.newPath || ''}-${originalDiffSegment?.substring(0, 100) || ''}`;

    // If we have a cached header and the current file data seems incomplete, use the cache
    if (fileHeaderCache.has(cacheKey) && (!file || (!file.oldPath && !file.newPath && !originalDiffSegment))) {
        const cachedHeader = fileHeaderCache.get(cacheKey)!;
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Using cached header:', cachedHeader);
        return cachedHeader;
    }

    // Quick extraction for streaming scenarios - prioritize speed over completeness
    const quickExtractFilename = (diffStr: string): string | null => {
        if (!diffStr) return null;

        const lines = diffStr.split('\n');
        for (const line of lines) {
            if (line.startsWith('diff --git')) {
                const match = line.match(/diff --git a\/(.*?) b\/(.*?)$/);
                if (match) return match[2] || match[1];
            }
            if (line.startsWith('+++ b/')) {
                return line.substring(6);
            }
            if (line.startsWith('--- a/') && !line.includes('/dev/null')) {
                return line.substring(6);
            }
        }
        return null;
    };

    // Helper to extract paths from unified diff header
    const extractPathFromUnifiedHeader = (line: string): string | null => {
        const match = line.match(/^(?:---|\+\+\+)\s+(?:(?:[ab]\/)?(.+)|\/dev\/null)$/);
        if (match) {
            if (line.trim() === '--- /dev/null' || line.trim() === '+++ /dev/null') {
                return null;
            }
            return match[1] || null;
        }
        return null;
    };

    // Helper to extract paths from git diff header
    const extractPathsFromDiffSegmentInternal = (diffStr: string): [string | null, string | null, string | null] => {
        const gitMatch = diffStr.match(/^diff --git a\/(.*?) b\/(.*?)$/m);
        if (gitMatch) {
            const oldP = gitMatch[1] === '/dev/null' ? null : gitMatch[1].trim();
            const newP = gitMatch[2] === '/dev/null' ? null : gitMatch[2].trim();
            let detectedType: string | null = null;
            if (oldP === null && newP !== null) detectedType = 'add';
            else if (oldP !== null && newP === null) detectedType = 'delete';
            else if (oldP && newP && oldP !== newP) detectedType = 'rename';
            else if (oldP || newP) detectedType = 'modify';
            return [oldP, newP, detectedType];
        }

        const lines = diffStr.split('\n');
        let oldPath: string | null = null;
        let newPath: string | null = null;
        let detectedType: string | null = null;

        const isNewFile = lines.some(line => line.includes('new file mode'));
        const isDeletedFile = lines.some(line => line.includes('deleted file mode'));

        // Parse the unified diff headers
        for (const line of lines) {
            if (line.startsWith('--- ')) {
                oldPath = extractPathFromUnifiedHeader(line);
            } else if (line.startsWith('+++ ')) {
                newPath = extractPathFromUnifiedHeader(line);
            }
            if ((oldPath !== undefined && newPath !== undefined) || line.startsWith('@@ ')) break;
        }

        if (isNewFile || (oldPath === null && newPath !== null && newPath !== '/dev/null')) {
            detectedType = 'add';
        } else if (isDeletedFile || (oldPath !== null && oldPath !== '/dev/null' && newPath === null)) {
            detectedType = 'delete';
        } else if (oldPath && newPath && oldPath !== newPath && oldPath !== '/dev/null' && newPath !== '/dev/null') {
            detectedType = 'rename';
        } else if ((oldPath && oldPath !== '/dev/null') || (newPath && newPath !== '/dev/null')) {
            detectedType = 'modify';
        }
        const result: [string | null, string | null, string | null] = [oldPath, newPath, detectedType];
        if (DEBUG_RENDER_FILE_HEADER) console.log('[extractPathsFromDiffSegmentInternal] Extracted:', result);
        return result;
    }

    // Helper function to cache and return header
    const cacheAndReturn = (header: string): string => {
        console.log('[renderFileHeader] FINAL RESULT:', header);
        fileHeaderCache.set(cacheKey, header);
        if (DEBUG_RENDER_FILE_HEADER) {
            console.log('[renderFileHeader] Cached and returning:', header);
        }
        return header;
    };

    // Prioritize information from the parsed 'file' object
    const type = file.type;
    const oldP = file.oldPath;
    const newP = file.newPath;
    if (DEBUG_RENDER_FILE_HEADER) {
        console.log('[renderFileHeader] Parsed file object properties:', { type, oldP, newP, similarity: file.similarity });
    }

    // Check for rename even when type is not explicitly set - do this early
    if (oldP && newP && oldP !== newP && oldP !== '/dev/null' && newP !== '/dev/null') {
        return cacheAndReturn(`Rename: ${oldP} -> ${newP}`);
    }

    // Quick filename extraction for streaming scenarios
    if (originalDiffSegment && (!oldP && !newP)) {
        const quickFilename = quickExtractFilename(originalDiffSegment);
        if (quickFilename) {
            // First check for renames by comparing git diff header paths
            const gitMatch = originalDiffSegment.match(/^diff --git a\/(.+) b\/(.+)$/m);
            if (gitMatch && gitMatch[1] !== gitMatch[2]) {
                return cacheAndReturn(`Rename: ${gitMatch[1]} -> ${gitMatch[2]}`);
            }
            
            // Check if this is clearly a modification (has both --- a/ and +++ b/ headers)
            if (originalDiffSegment.includes('--- a/') && originalDiffSegment.includes('+++ b/')) {
                return cacheAndReturn(`File: ${quickFilename}`);
            }
            
            // Check for new file creation (--- /dev/null)
            if (originalDiffSegment.includes('--- /dev/null')) {
                return cacheAndReturn(`Create: ${quickFilename}`);
            }
            
            // Check for rename indicators before other operations
            if (originalDiffSegment.includes('new file mode')) {
                return cacheAndReturn(`Create: ${quickFilename}`);
            } else if (originalDiffSegment.includes('deleted file mode')) {
                return cacheAndReturn(`Delete: ${quickFilename}`);
            } else if (originalDiffSegment.includes('rename from') || originalDiffSegment.includes('similarity index')) {
                // Try to extract both old and new names for renames
                const renameFromMatch = originalDiffSegment.match(/rename from (.+)/);
                const renameToMatch = originalDiffSegment.match(/rename to (.+)/);
                if (renameFromMatch && renameToMatch) {
                    return cacheAndReturn(`Rename: ${renameFromMatch[1]} -> ${renameToMatch[1]}`);
                } else {
                    return cacheAndReturn(`Rename: ${quickFilename}`);
                }
            } else if (originalDiffSegment.includes('similarity index')) {
                // Handle similarity index renames
                const gitMatch = originalDiffSegment.match(/^diff --git a\/(.+) b\/(.+)$/m);
                if (gitMatch && gitMatch[1] !== gitMatch[2]) {
                    return cacheAndReturn(`Rename: ${gitMatch[1]} -> ${gitMatch[2]}`);
                }
            }

            // First check if this is clearly a modification (has both --- a/ and +++ b/ headers)
            if (originalDiffSegment.includes('--- a/') && originalDiffSegment.includes('+++ b/')) {
                return cacheAndReturn(`File: ${quickFilename}`);
            }
            return cacheAndReturn(`File: ${quickFilename}`);
        }
    }

    // Try to extract file path from the original diff segment if we have it
    if (originalDiffSegment && (!oldP || !newP)) {
        const [extractedOldPath, extractedNewPath] = extractPathsFromDiffSegmentInternal(originalDiffSegment);
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Extracted paths from segment:', { extractedOldPath, extractedNewPath });

        // Use extracted paths if available
        if (extractedOldPath || extractedNewPath) {
            // If we have both paths and neither is /dev/null, it's likely a modification or rename
            if (extractedOldPath && extractedNewPath && extractedOldPath !== '/dev/null' && extractedNewPath !== '/dev/null') {
                if (extractedOldPath === extractedNewPath) {
                    return cacheAndReturn(`File: ${extractedNewPath}`);
                }
            }

            // Determine operation type from extracted paths
            if ((!extractedOldPath || extractedOldPath == '/dev/null') && extractedNewPath) {
                return cacheAndReturn(`Create: ${extractedNewPath}`);
            } else if (extractedOldPath && (!extractedNewPath || extractedNewPath == '/dev/null')) {
                return cacheAndReturn(`Delete: ${extractedOldPath}`);
            } else if (extractedOldPath && extractedNewPath && extractedOldPath !== extractedNewPath) {
                return cacheAndReturn(`Rename: ${extractedOldPath} -> ${extractedNewPath}`);
            }
            // Default to File: for modifications
            return cacheAndReturn(`File: ${extractedNewPath || extractedOldPath}`);
        }
    }

    if (type === 'add') {
        // For 'add', newP should be the filename. oldP is /dev/null or undefined.
        if (newP && newP !== '/dev/null') {
            return cacheAndReturn(`Create: ${newP}`);
        }
        // Fallback if newP is missing or /dev/null (which is unusual for 'add' from parseDiff)
        if (originalDiffSegment) {
            const [, fallbackNewP] = extractPathsFromDiffSegmentInternal(originalDiffSegment);
            if (fallbackNewP && fallbackNewP !== '/dev/null') {
                return cacheAndReturn(`Create: ${fallbackNewP}`);
            }
        }
        return cacheAndReturn('Create: (unknown path)');
    }

    if (type === 'delete' || (!newP && oldP && oldP !== '/dev/null')) {
        // For 'delete', oldP should be the filename. newP is /dev/null or undefined.
        if (oldP && oldP !== '/dev/null') {
            return cacheAndReturn(`Delete: ${oldP}`);
        }
        // Fallback
        if (originalDiffSegment) {
            const [fallbackOldP] = extractPathsFromDiffSegmentInternal(originalDiffSegment);
            if (fallbackOldP && fallbackOldP !== '/dev/null') {
                return cacheAndReturn(`Delete: ${fallbackOldP}`);
            }
        }
        return cacheAndReturn('Delete: (unknown path)');
    }

    if (type === 'rename' || type === 'copy') {
        if (oldP && oldP !== '/dev/null' && newP && newP !== '/dev/null') {
            const similarityIndex = file.similarity || 100;
            return cacheAndReturn(`Rename${similarityIndex < 100 ? ' with changes' : ''}: ${oldP} -> ${newP}`);
        }
    }

    if (type === 'modify' || (!type && (oldP || newP))) {
        const path = newP || oldP; // In modify, old and new path are usually the same
        if (path && path !== '/dev/null') {
            return cacheAndReturn(`File: ${path}`);
        }
    }

    // Fallback: If type is unclear or paths are missing, try to parse the segment
    if (originalDiffSegment) {
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Using fallback: parsing originalDiffSegment');
        const [parsedOldPath, parsedNewPath, detectedType] = extractPathsFromDiffSegmentInternal(originalDiffSegment);

        if (detectedType === 'add' && parsedNewPath) {
            return cacheAndReturn(`Create: ${parsedNewPath}`);
        } else if (detectedType === 'delete' && parsedOldPath) {
            return cacheAndReturn(`Delete: ${parsedOldPath}`);
        } else if (detectedType === 'rename' && parsedOldPath && parsedNewPath) {
            return cacheAndReturn(`Rename: ${parsedOldPath} -> ${parsedNewPath}`);
        } else if (parsedNewPath || parsedOldPath) { // Covers modify and cases where one path is found
            return cacheAndReturn(`File: ${parsedNewPath || parsedOldPath || '(unknown path)'}`);
        }
    }

    // Final fallback checks
    if (file.hunks && file.hunks.length > 0) {
        return cacheAndReturn('File: (unknown path, changes detected)');
    }

    // Absolute fallback
    return cacheAndReturn('Unknown file operation');
};
