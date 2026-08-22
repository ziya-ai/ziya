/**
 * Human-readable rendering of diff-application failures.
 *
 * The backend reports each failed hunk as an `error_details` dict plus the
 * pipeline stage it died in (see app/utils/diff_utils/pipeline/). The UI used
 * to JSON.stringify that dict straight into a notification, which told the
 * user nothing they could act on. This module turns it into a sentence, and
 * recovers the hunk's line range from the diff text so the message can say
 * *where* in the file the failure was.
 */

export interface HunkHeader {
    /** Hunk number as the backend counts it (1-based, or the "Hunk #N" tag). */
    number: number;
    oldStart: number;
    oldCount: number;
    newStart: number;
    newCount: number;
    /** Trailing text after the closing @@, e.g. a function signature. */
    context: string;
    /** Frontend-synthesized placeholder position — line numbers are meaningless. */
    synthesized: boolean;
}

const HUNK_HEADER_RE =
    /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/gm;

/**
 * Parse the @@ headers of a single-file diff.
 *
 * Numbering mirrors the server parser: an explicit "Hunk #N" tag in the header
 * wins, otherwise hunks are numbered by position starting at 1. Keeping the
 * two in agreement is what lets us join a failed-hunk id back to a line range.
 */
export function parseHunkHeaders(diffContent: string): HunkHeader[] {
    const headers: HunkHeader[] = [];
    if (!diffContent) return headers;
    HUNK_HEADER_RE.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = HUNK_HEADER_RE.exec(diffContent)) !== null) {
        const trailing = (match[5] || '').trim();
        const tagged = /Hunk #(\d+)/.exec(trailing);
        headers.push({
            number: tagged ? parseInt(tagged[1], 10) : headers.length + 1,
            oldStart: parseInt(match[1], 10),
            oldCount: match[2] !== undefined ? parseInt(match[2], 10) : 1,
            newStart: parseInt(match[3], 10),
            newCount: match[4] !== undefined ? parseInt(match[4], 10) : 1,
            context: trailing
                .replace(/Hunk #\d+/, '')
                .replace(/ZIYA_NOPOS/, '')
                .trim(),
            synthesized: trailing.includes('ZIYA_NOPOS'),
        });
    }
    return headers;
}

/** "lines 120-137", or null when the position is a synthesized placeholder. */
export function formatHunkRange(header: HunkHeader | undefined): string | null {
    if (!header || header.synthesized) return null;
    if (header.oldStart === 0 && header.oldCount === 0) return 'new file';
    if (header.oldCount <= 1) return `line ${header.oldStart}`;
    return `lines ${header.oldStart}-${header.oldStart + header.oldCount - 1}`;
}

const STAGE_LABELS: Record<string, string> = {
    initialization: 'initialization',
    system_patch: 'system patch',
    git_apply: 'git apply',
    difflib: 'fuzzy match',
    llm_resolver: 'LLM resolver',
    language_validation: 'language validation',
    complete: 'final verification',
};

export function formatStage(stage?: string): string {
    if (!stage) return 'unknown stage';
    return STAGE_LABELS[stage] || stage.replace(/_/g, ' ');
}

export interface FormattedHunkError {
    /** One-line, plain-language cause. */
    summary: string;
    /** Optional verbatim detail (stderr, validator message) worth showing raw. */
    detail?: string;
}

/**
 * Turn an `error_details` payload into a sentence.
 *
 * Codes are matched by substring rather than equality because several call
 * sites in the pipeline write prose into the `error` field rather than a code
 * ("Failed to apply hunk in all stages", "skipped due to newline at EOF
 * issue"). Anything unrecognised falls through to the raw text, so a new
 * backend code degrades to "readable-ish" instead of disappearing.
 */
export function formatHunkError(errorDetails: any): FormattedHunkError | null {
    if (!errorDetails) return null;
    if (typeof errorDetails === 'string') return { summary: errorDetails };

    const code = String(errorDetails.error ?? '').trim();
    const reason = errorDetails.reason ? String(errorDetails.reason) : undefined;
    const message = errorDetails.message ? String(errorDetails.message) : undefined;
    const extra = errorDetails.details ? String(errorDetails.details) : undefined;
    const stderr = errorDetails.stderr ? String(errorDetails.stderr).trim() : undefined;
    const lower = code.toLowerCase();

    const withDetail = (summary: string): FormattedHunkError => {
        const detail = stderr || message || (extra && extra !== summary ? extra : undefined);
        return detail ? { summary, detail } : { summary };
    };

    if (lower.includes('all stages')) {
        return withDetail(
            'No location in the file matched this hunk\u2019s context. The file has ' +
            'probably changed since the diff was generated.'
        );
    }
    if (lower.includes('ambiguous_context')) {
        const positions = errorDetails.equally_close_matches;
        if (Array.isArray(positions) && positions.length > 1) {
            return withDetail(
                `Context matches ${positions.length} places equally (lines ` +
                `${positions.join(', ')}) \u2014 no safe target.`
            );
        }
        if (typeof errorDetails.closest_distance === 'number') {
            return withDetail(
                `Nearest context match is ${errorDetails.closest_distance} lines ` +
                'away \u2014 too far to apply safely.'
            );
        }
        return withDetail(reason || 'Hunk context is ambiguous in this file.');
    }
    if (lower.includes('language_validation')) {
        return withDetail('Applying this hunk produced syntactically invalid code, so it was rolled back.');
    }
    if (lower.includes('misordered')) {
        return withDetail('Hunks are out of order relative to the file.');
    }
    if (lower.includes('low confidence')) {
        return withDetail('Best match was below the confidence threshold; skipped to avoid corrupting the file.');
    }
    if (lower.includes('newline at eof')) {
        return withDetail('Skipped because of a trailing-newline mismatch at end of file.');
    }
    if (lower.includes('timeout')) {
        return withDetail('Timed out while applying.');
    }
    if (lower.includes('patch_failed')) {
        return withDetail('The system patch tool rejected this hunk.');
    }
    if (lower === 'not applied') {
        return withDetail('Hunk was not applied (no change detected in the file).');
    }
    if (code) return withDetail(code);
    if (reason) return withDetail(reason);
    if (message) return { summary: message };
    if (extra) return { summary: extra };

    // Unknown shape: better to show the JSON than to silently drop it.
    try {
        return { summary: 'Unrecognised failure', detail: JSON.stringify(errorDetails, null, 2) };
    } catch {
        return { summary: 'Unrecognised failure' };
    }
}
