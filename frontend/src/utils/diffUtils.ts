/**
 * Diff utilities - helpers for extracting and processing diff content
 */

/**
 * Extract all file paths referenced in a diff
 *
 * Uses a state machine to only extract from diff structural headers
 * (the preamble between 'diff --git' and the first '@@' hunk header),
 * preventing false matches from code content inside hunks that happens
 * to resemble diff headers.
 */
export function extractAllFilesFromDiff(diffContent: string): string[] {
    // Quick validation: reject if the content doesn't contain any diff headers
    if (!diffContent.includes('diff --git') && !diffContent.includes('--- a/')) {
        return [];
    }

    // Path sanity check: reject paths with characters that don't belong in real file paths
    const isValidPath = (p: string): boolean => {
        if (!p || p.length > 500) return false;
        if (/[)(;{}!@#$%^&*+=<>?\s]/.test(p)) return false;
        return true;
    };

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

    // State machine: only extract paths from diff preamble sections,
    // not from hunk content where code lines may mimic diff headers.
    let inPreamble = false;

    for (const line of lines) {
        // Utility helpers
        const isDevNull = (p: string) => p === '/dev/null' || p === 'dev/null';

        // git diff strips the leading slash from absolute paths into the a/b/ prefix.
        // e.g. a file at /Users/foo/bar.py becomes '+++ b/Users/foo/bar.py' in the diff.
        // Detect and restore the leading slash for common absolute path roots.
        const restoreLeadingSlash = (p: string): string => {
            if (p.startsWith('/')) return p;
            const absoluteRoots = ['Users/', 'home/', 'opt/', 'var/', 'usr/', 'tmp/', 'etc/', 'srv/', 'private/'];
            return absoluteRoots.some(r => p.startsWith(r)) ? '/' + p : p;
        };

        const gitMatch = line.match(/^diff --git (?:a\/)?([^\s]+) (?:b\/)?([^\s]+)$/);
        if (gitMatch) {
            // Entering a new file section — preamble until first @@
            inPreamble = true;
            const oldPath = restoreLeadingSlash(gitMatch[1]);
            const newPath = restoreLeadingSlash(gitMatch[2]);
            if (!isDevNull(newPath) && isValidPath(newPath)) files.push(newPath);
            if (!isDevNull(oldPath) && oldPath !== newPath && isValidPath(oldPath)) files.push(oldPath);
            continue;
        }

        // Hunk header — switch from preamble to hunk content
        if (line.startsWith('@@')) {
            inPreamble = false;
            continue;
        }

        // Only extract from preamble lines (between 'diff --git' and first '@@')
        if (inPreamble) {
            const minusMatch = line.match(/^--- a\/(.+)$/);
            if (minusMatch && !isDevNull(minusMatch[1])) {
                const p = restoreLeadingSlash(minusMatch[1]);
                if (isValidPath(p)) files.push(p);
            }

            const plusMatch = line.match(/^\+\+\+ b\/(.+)$/);
            if (plusMatch && !isDevNull(plusMatch[1])) {
                const p = restoreLeadingSlash(plusMatch[1]);
                if (isValidPath(p)) files.push(p);
            }
        }
    }

    // Remove duplicates and filter out new file creations
    const uniqueFiles = [...new Set(files)];
    const existingFiles = uniqueFiles.filter(file =>
        !newFiles.has(file)
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

/**
 * Parse @@ hunk headers from a diff to get the original-file line ranges it touches.
 * Returns an array of [start, end] tuples (inclusive, 1-based).
 * New-file-creation hunks (@@ -0,0 …) produce no ranges.
 */
export function parseHunkRanges(diffContent: string): [number, number][] {
    const ranges: [number, number][] = [];
    const hunkHeaderRegex = /@@ -(\d+)(?:,(\d+))? \+/g;
    let match;
    while ((match = hunkHeaderRegex.exec(diffContent)) !== null) {
        const start = parseInt(match[1], 10);
        const count = match[2] !== undefined ? parseInt(match[2], 10) : 1;
        if (start === 0 && count === 0) continue; // new-file creation
        const end = start + Math.max(count - 1, 0);
        ranges.push([start, end]);
    }
    return ranges;
}

/**
 * Extract the ZIYA_NOPOS functional locators (section hints) from a
 * frontend-synthesized diff. Synthesized hunks carry a placeholder
 * "@@ -1,N +1,M @@ ZIYA_NOPOS <locator>" header whose line range is
 * meaningless, so overlap must be judged by the named locator instead.
 * Returns the set of non-empty locator strings; an empty set means the
 * diff carries real line positions (not synthesized).
 */
export function extractNoPosLocators(diffContent: string): Set<string> {
    const locators = new Set<string>();
    const re = /^@@.*@@\s*ZIYA_NOPOS\s*(.*)$/gm;
    let match;
    while ((match = re.exec(diffContent)) !== null) {
        const loc = match[1].trim();
        if (loc) locators.add(loc);
    }
    return locators;
}

/**
 * Extract the target file path from a single-file diff block.
 * Looks for `+++ b/path` first, then `+++ path`.
 */
export function extractDiffFilePath(diffContent: string): string | null {
    for (const line of diffContent.split('\n')) {
        if (line.startsWith('+++ b/')) return line.substring(6).trim();
        if (line.startsWith('+++ ')) {
            let path = line.substring(4).trim();
            if (path.startsWith('b/')) path = path.substring(2);
            if (path === '/dev/null') continue;
            return path;
        }
    }
    return null;
}

/**
 * Return true if any range in `a` overlaps any range in `b`.
 */
function rangesOverlap(a: [number, number][], b: [number, number][]): boolean {
    for (const [aStart, aEnd] of a) {
        for (const [bStart, bEnd] of b) {
            if (aStart <= bEnd && bStart <= aEnd) {
                // Calculate actual overlap size. Adjacent hunks sharing
                // only a few lines of context are complementary changes,
                // not revisions. Require >50% of the smaller hunk to
                // overlap before treating it as a superseding revision.
                const overlapStart = Math.max(aStart, bStart);
                const overlapEnd = Math.min(aEnd, bEnd);
                const overlapSize = overlapEnd - overlapStart + 1;
                const smallerHunk = Math.min(aEnd - aStart + 1, bEnd - bStart + 1);
                if (smallerHunk > 0 && overlapSize / smallerHunk > 0.5) return true;
            }
        }
    }
    return false;
}

/**
 * Check if two overlapping diffs are sequential (first prepares for the
 * second) rather than the later superseding the earlier.
 *
 * Heuristic: if the earlier diff is predominantly subtractive (removing
 * code to make way) and the later adds new content, they're complementary.
 */
function isSequentialPair(earlierDiff: string, laterDiff: string): boolean {
    let earlierAdds = 0, earlierRemoves = 0, laterAdds = 0;
    for (const line of earlierDiff.split('\n')) {
        if (line.startsWith('@@') || line.startsWith('diff ') ||
            line.startsWith('---') || line.startsWith('+++')) continue;
        if (line.startsWith('+')) earlierAdds++;
        else if (line.startsWith('-')) earlierRemoves++;
    }
    for (const line of laterDiff.split('\n')) {
        if (line.startsWith('@@') || line.startsWith('diff ') ||
            line.startsWith('---') || line.startsWith('+++')) continue;
        if (line.startsWith('+')) laterAdds++;
    }
    return earlierRemoves > 0 && earlierAdds <= 1 && laterAdds > 0;
}

// -- Content-based redo detection -------------------------------------------
//
// Position alone cannot recognise the most common correction an LLM makes to
// its own failed hunk: it re-emits the same body under a different `@@`
// header (re-anchored after a context mismatch).  Adjacent-but-disjoint
// ranges then read as two independent edits.  These helpers judge by the
// change lines themselves so a redo is caught wherever it was anchored.

/** Change lines shorter than this, or without an alphanumeric character, are
 *  structural noise (`}`, `);`, blank) shared by unrelated edits. */
const MIN_SUBSTANTIVE_LINE_CHARS = 4;
/** A body with at least this many distinct substantive lines is specific
 *  enough that matching another body is evidence of a redo wherever the two
 *  were anchored. */
const MIN_LINES_FOR_POSITION_FREE_MATCH = 6;
/** Fraction of the smaller body that must reappear in the other. */
const REDO_OVERLAP_THRESHOLD = 0.8;
/** Smaller bodies also need positional proximity (original-file lines
 *  between the nearest hunk edges) — the same one-liner added at two distant
 *  call sites is two edits, not a redo. */
const NEAR_GAP_LINES = 10;

/**
 * The substantive change lines of a diff, sign-prefixed and trimmed.
 * Context lines and file headers are excluded: context is positional and a
 * re-anchored correction changes it.  Sign is kept so a later diff that
 * REMOVES what an earlier one added is not mistaken for a repeat of it.
 */
export function changeSignature(diffContent: string): Set<string> {
    const sig = new Set<string>();
    for (const line of diffContent.split('\n')) {
        if (line.startsWith('+++') || line.startsWith('---')) continue;
        const sign = line[0];
        if (sign !== '+' && sign !== '-') continue;
        const body = line.slice(1).trim();
        if (body.length < MIN_SUBSTANTIVE_LINE_CHARS || !/[A-Za-z0-9]/.test(body)) continue;
        sig.add(sign + body);
    }
    return sig;
}

/** |A ∩ B| / min(|A|, |B|): 1.0 when the smaller body is fully contained,
 *  so a correction that EXTENDS the original body still scores as a redo
 *  (Jaccard would be diluted by the additions). */
function overlapCoefficient(a: Set<string>, b: Set<string>): number {
    if (a.size === 0 || b.size === 0) return 0;
    let shared = 0;
    for (const x of a) if (b.has(x)) shared++;
    return shared / Math.min(a.size, b.size);
}

/** Smallest number of original-file lines between any two hunks of `a` and
 *  `b` (0 when they touch or overlap; Infinity when either has no ranges). */
function rangeGap(a: [number, number][], b: [number, number][]): number {
    let best = Infinity;
    for (const [aStart, aEnd] of a) {
        for (const [bStart, bEnd] of b) {
            best = Math.min(best, Math.max(aStart - bEnd - 1, bStart - aEnd - 1, 0));
        }
    }
    return best;
}

/**
 * True if `later` re-does `earlier`: same file (caller checks), bodies that
 * substantially coincide, and — for bodies too small to be self-identifying —
 * hunks anchored near each other.  New-file diffs are handled positionally
 * by the caller and never reach here.
 */
function isRedo(earlier: string, later: string,
                earlierRanges: [number, number][], laterRanges: [number, number][]): boolean {
    if (earlierRanges.length === 0 || laterRanges.length === 0) return false;
    const a = changeSignature(earlier);
    const b = changeSignature(later);
    const smaller = Math.min(a.size, b.size);
    if (smaller === 0) return false;
    if (overlapCoefficient(a, b) < REDO_OVERLAP_THRESHOLD) return false;
    if (smaller >= MIN_LINES_FOR_POSITION_FREE_MATCH) return true;
    return rangeGap(earlierRanges, laterRanges) <= NEAR_GAP_LINES;
}

/**
 * Given an ordered array of single-file diff strings, return the set of
 * indices that are superseded by a later diff for the same file — either
 * because the later hunk overlaps the earlier's line range, or because the
 * later diff re-does the earlier's change body at a different anchor.
 *
 * Two diffs for the same file that target non-overlapping line ranges with
 * different bodies are treated as independent changes and both kept.  A
 * superseded diff is faded, not forbidden: once the message settles it can
 * still be applied, so a false positive costs a click, while a miss offers
 * the user a hunk the model itself retracted.
 */
export function findSupersededDiffIndices(diffs: string[]): Set<number> {
    if (diffs.length <= 1) return new Set();

    const filePaths = diffs.map(extractDiffFilePath);
    const hunkRanges = diffs.map(parseHunkRanges);
    const noPosLocators = diffs.map(extractNoPosLocators);
    const superseded = new Set<number>();

    for (let i = 0; i < diffs.length; i++) {
        if (!filePaths[i]) continue;
        for (let j = i + 1; j < diffs.length; j++) {
            if (filePaths[j] !== filePaths[i]) continue;

            // Both are new-file diffs for the same path — later wins
            if (hunkRanges[i].length === 0 && hunkRanges[j].length === 0) {
                superseded.add(i);
                break;
            }

            // Synthesized (ZIYA_NOPOS) hunks carry placeholder line ranges, so
            // positional overlap is meaningless. Treat them as superseding only
            // when they share a named functional locator with the other diff.
            if (noPosLocators[i].size > 0 || noPosLocators[j].size > 0) {
                const sharesLocator = [...noPosLocators[i]].some(loc => noPosLocators[j].has(loc));
                if (sharesLocator && !isSequentialPair(diffs[i], diffs[j])) {
                    superseded.add(i);
                    break;
                }
                continue; // different / unknown locators → independent changes
            }

            if (rangesOverlap(hunkRanges[i], hunkRanges[j])) {
                if (isSequentialPair(diffs[i], diffs[j])) continue;
                superseded.add(i);
                break;
            }

            // Disjoint (or barely overlapping) ranges: judge by content.  This
            // is the re-anchored-correction case the positional check misses.
            if (isRedo(diffs[i], diffs[j], hunkRanges[i], hunkRanges[j])) {
                if (isSequentialPair(diffs[i], diffs[j])) continue;
                superseded.add(i);
                break;
            }
        }
    }
    return superseded;
}

/**
 * Find superseded file sections across fenced diff blocks.
 *
 * A single ```diff fence may contain several `diff --git` sections. The
 * lower-level detector intentionally operates on single-file diffs, so flatten
 * every block into file sections before comparing them, then map matches back
 * to their block and file-section indices. This prevents a correction to one
 * file from superseding unrelated files that happened to share its fence.
 */
export function findSupersededDiffParts(
    diffBlocks: string[]
): Map<number, Set<number>> {
    const flattened: {
        blockIndex: number;
        fileIndex: number;
        text: string;
    }[] = [];

    diffBlocks.forEach((block, blockIndex) => {
        const starts: number[] = [];
        const headerRegex = /^diff --git .*$/gm;
        let match: RegExpExecArray | null;

        while ((match = headerRegex.exec(block)) !== null) {
            starts.push(match.index);
        }

        // Headerless or single-file blocks remain one comparison unit.
        if (starts.length <= 1) {
            flattened.push({ blockIndex, fileIndex: 0, text: block });
            return;
        }

        starts.forEach((start, fileIndex) => {
            const end = starts[fileIndex + 1] ?? block.length;
            flattened.push({
                blockIndex,
                fileIndex,
                text: block.slice(start, end).trim(),
            });
        });
    });

    const supersededFlatIndices = findSupersededDiffIndices(
        flattened.map(part => part.text)
    );
    const result = new Map<number, Set<number>>();

    supersededFlatIndices.forEach(flatIndex => {
        const part = flattened[flatIndex];
        if (!result.has(part.blockIndex)) {
            result.set(part.blockIndex, new Set());
        }
        result.get(part.blockIndex)!.add(part.fileIndex);
    });

    return result;
}
