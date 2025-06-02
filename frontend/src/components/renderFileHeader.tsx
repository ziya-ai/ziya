import { parseDiff } from 'react-diff-view';

// Debug flag to control logging
const DEBUG_RENDER_FILE_HEADER = false; // Set to true for debugging
export const renderFileHeader = (file: ReturnType<typeof parseDiff>[number], originalDiffSegment?: string, fileIndex?: number): string => {
    if (DEBUG_RENDER_FILE_HEADER) {
        console.log('[renderFileHeader] Input:', {
            fileObject: JSON.parse(JSON.stringify(file)), // Deep copy for logging
            originalDiffSegmentPreview: originalDiffSegment ? originalDiffSegment.substring(0, 200) + (originalDiffSegment.length > 200 ? '...' : '') : 'undefined',
            fileIndex
        });
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
            if ((oldPath !== undefined && newPath !== undefined) || line.startsWith('@@ ')) break;
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
    // This function needs to handle the case where newP is not defined yet
    const extractPathsFromDiffSegmentInternal = (diffStr: string): [string | null, string | null, string | null] => { // Returns [oldPath, newPath, type]
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

        // Enhanced logging for debugging
        if (DEBUG_RENDER_FILE_HEADER && diffStr) {
            const gitMatch = diffStr.match(/^diff --git a\/(.*?) b\/(.*?)$/m);
            console.log('[extractPathsFromDiffSegmentInternal] Input diff segment preview:', diffStr.substring(0, 100) + "...");
            console.log('[extractPathsFromDiffSegmentInternal] Git match:', gitMatch);
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

    // Prioritize information from the parsed 'file' object
    const type = file.type;
    const oldP = file.oldPath;
    const newP = file.newPath;
    if (DEBUG_RENDER_FILE_HEADER) {
        console.log('[renderFileHeader] Parsed file object properties:', { type, oldP, newP, similarity: file.similarity });
    }

    // Quick filename extraction for streaming scenarios
    if (originalDiffSegment && (!oldP && !newP)) {
        const quickFilename = quickExtractFilename(originalDiffSegment);
        if (quickFilename) {
            return `File: ${quickFilename}`;
        }
    }

    // Try to extract file path from the original diff segment if we have it
    if (originalDiffSegment && (!oldP || !newP)) {
        const [extractedOldPath, extractedNewPath] = extractPathsFromDiffSegmentInternal(originalDiffSegment);
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Extracted paths from segment:', { extractedOldPath, extractedNewPath });

        // Use extracted paths if available
        if (extractedOldPath || extractedNewPath) {
            return `File: ${extractedNewPath || extractedOldPath}`;
        }
    }

    if (type === 'add') {
        // For 'add', newP should be the filename. oldP is /dev/null.
        if (newP && newP !== '/dev/null') {
            if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Determined: Create (from file.newPath)');
            return `Create: ${newP}`;
        }
        // Fallback if newP is missing or /dev/null (which is unusual for 'add' from parseDiff)
        if (originalDiffSegment) {
            const [, fallbackNewP] = extractPathsFromDiffSegmentInternal(originalDiffSegment);
            if (fallbackNewP && fallbackNewP !== '/dev/null') {
                if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Determined: Create (from fallback segment parsing)');
                return `Create: ${fallbackNewP}`;
            }
        }
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Determined: Create (unknown path)');
        return 'Create: (unknown path)';
    }

    if (type === 'delete') {
        // For 'delete', oldP should be the filename. newP is /dev/null.
        if (oldP && oldP !== '/dev/null') return `Delete: ${oldP}`;
        // Fallback
        if (originalDiffSegment) {
            const [fallbackOldP] = extractPathsFromDiffSegmentInternal(originalDiffSegment);
            if (fallbackOldP && fallbackOldP !== '/dev/null') return `Delete: ${fallbackOldP}`;
        }
        return 'Delete: (unknown path)';
    }

    if (type === 'rename') {
        if (oldP && oldP !== '/dev/null' && newP && newP !== '/dev/null') {
            const similarityIndex = file.similarity || 100;
            return `Rename${similarityIndex < 100 ? ' with changes' : ''}: ${oldP} -> ${newP} (${similarityIndex}% similar)`;
        }
    }

    if (type === 'modify') {
        const path = newP || oldP; // In modify, old and new path are usually the same
        if (path && path !== '/dev/null') {
            if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Determined: File (from file.newPath or file.oldPath)');
            return `File: ${path}`;
        }
    }
    // Fallback: If type is unclear or paths are missing, try to parse the segment
    if (originalDiffSegment) {
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Using fallback: parsing originalDiffSegment');
        const [parsedOldPath, parsedNewPath, detectedType] = extractPathsFromDiffSegmentInternal(originalDiffSegment);

        if (detectedType === 'add' && parsedNewPath) {
            if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Fallback determined: Create');
            return `Create: ${parsedNewPath}`;
        } else if (detectedType === 'delete' && parsedOldPath) {
            return `Rename: ${parsedOldPath} -> ${parsedNewPath}`;
        } else if (parsedNewPath || parsedOldPath) { // Covers modify and cases where one path is found
            return `File: ${parsedNewPath || parsedOldPath || '(unknown path)'}`;
        }        // ...
    }
    if (file.hunks && file.hunks.length > 0) {
        if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Fallback: Unknown path, changes detected');
        return 'File: (unknown path, changes detected)';
    }

    // Absolute fallback
    if (DEBUG_RENDER_FILE_HEADER) console.log('[renderFileHeader] Absolute fallback: Unknown file operation');
    return 'Unknown file operation';
};

