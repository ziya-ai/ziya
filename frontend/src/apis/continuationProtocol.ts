export interface ContinuationRewindEvent {
    rewind_line: number;
}

export interface ContinuationRewindResult {
    content: string;
    applied: boolean;
}

export function applyContinuationRewind(
    content: string,
    event: ContinuationRewindEvent,
): ContinuationRewindResult {
    const lineCount = content.split('\n').length;
    const target = event.rewind_line;
    if (!Number.isInteger(target) || target < 0 || target > lineCount) {
        return { content, applied: false };
    }
    return {
        content: content.split('\n').slice(0, target).join('\n'),
        applied: true,
    };
}
