export interface SseDrainResult {
    frames: string[];
    remainder: string;
}

export function drainSseFrames(
    remainder: string,
    chunk: string,
    flush: boolean = false,
): SseDrainResult {
    const combined = remainder + chunk;
    const frames: string[] = [];
    const separator = /\r?\n\r?\n/g;
    let start = 0;
    let match: RegExpExecArray | null;

    while ((match = separator.exec(combined)) !== null) {
        frames.push(combined.slice(start, match.index));
        start = match.index + match[0].length;
    }

    let nextRemainder = combined.slice(start);
    if (flush && nextRemainder.trim()) {
        frames.push(nextRemainder);
        nextRemainder = '';
    }

    return { frames, remainder: nextRemainder };
}
