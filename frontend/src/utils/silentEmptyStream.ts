/**
 * A chat response that closed without a single data frame.
 *
 * Every turn the server completes sends at least one frame (content, an
 * error, `done`, or `stream_end`), and heartbeats are not counted.  Zero
 * frames therefore means the turn died before it could report anything.
 * Seen 2026-11-16 as a 200 with an empty event stream: the message turned
 * into a Retry label and nothing told the user why.
 */
export interface StreamEndState {
    /** Data frames processed, heartbeats excluded. */
    dataFrames: number;
    /** Content accumulated for this turn. */
    content: string;
    /** An error has already been shown for this turn. */
    errorShown: boolean;
    /** The user (or a newer send) stopped this stream. */
    aborted: boolean;
    /** This read rejoined an existing turn; nothing left to replay is normal. */
    reattach: boolean;
}

export const SILENT_EMPTY_STREAM_MESSAGE =
    'The server ended this response without sending anything, so the turn ' +
    'failed before it could report an error. The usual cause is expired ' +
    'credentials: refresh them, then retry. If it happens again, the ' +
    'server log has the reason.';

export function isSilentEmptyStream(s: StreamEndState): boolean {
    return s.dataFrames === 0 && !s.content && !s.errorShown
        && !s.aborted && !s.reattach;
}
