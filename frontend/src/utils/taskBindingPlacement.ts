/**
 * Chronological placement of UNANCHORED task bindings in a chat.
 *
 * A binding carries ``anchor_message_id`` when a slash command or a Run
 * click created it, and nothing when the model created it mid-turn via
 * ``task_card_stage`` / ``task_card_launch``.  For the latter the only
 * placement datum is ``created_at``, and the obvious rule — "after the
 * last message timestamped at or before it" — is wrong in a way that is
 * easy to miss: the turn's human prompt is stamped BEFORE the tool call
 * and the assistant reply is stamped when the stream FINISHES, after it.
 * The tile therefore rendered between the prompt and the reply that
 * staged it — the start of the turn, where it reads as something the
 * user posted and is buried under the reply that explains it.
 *
 * The rule here is "the end of the turn that built it": resolve the
 * anchor as before, and when it is a human message walk forward past
 * every non-human message of that turn.  A binding created between
 * turns (a Run click after the reply) anchors to a non-human message
 * and is unaffected.
 *
 * Mirrors app/utils/binding_placement.py, which places the model-facing
 * synthetic status message; the two must agree or the model's view of
 * where a tile sits diverges from the user's.
 */

export interface PlacedBindings<B> {
    /** Older than every timestamped message: render above the thread. */
    head: B[];
    /** Render directly below the message at this index. */
    byMessageIdx: Map<number, B[]>;
    /** Belongs after the last persisted message. */
    tail: B[];
    /**
     * Belongs to a turn whose reply is not persisted yet — the live stream
     * is rendering it.  Render after the streamed content, not in the
     * thread, or the tile sits between the prompt and the reply it
     * belongs under for the whole duration of the stream.
     */
    inFlight: B[];
}

interface MessageLike {
    role?: string;
    _timestamp?: number;
}

const isHuman = (m: MessageLike | undefined): boolean => m?.role === 'human';

export function placeUnanchoredBindings<B extends { created_at?: number }>(
    bindings: ReadonlyArray<B>,
    msgs: ReadonlyArray<MessageLike>,
): PlacedBindings<B> {
    const out: PlacedBindings<B> = {
        head: [], byMessageIdx: new Map(), tail: [], inFlight: [],
    };
    if (bindings.length === 0) return out;
    const lastIdx = msgs.length - 1;
    for (const b of bindings) {
        const t = b.created_at || 0;
        // Last message timestamped at or before the binding.  Messages
        // without a timestamp never anchor but still occupy positions.
        let anchor = -1;
        for (let i = 0; i < msgs.length; i++) {
            const mt = msgs[i]._timestamp;
            if (typeof mt === 'number' && mt <= t) anchor = i;
        }
        if (anchor === -1) {
            out.head.push(b);
            continue;
        }
        let end = anchor;
        if (isHuman(msgs[anchor])) {
            let j = anchor + 1;
            while (j < msgs.length && !isHuman(msgs[j])) {
                end = j;
                j += 1;
            }
            if (end === anchor && anchor === lastIdx) {
                // The prompt is the newest message and nothing follows
                // it: its reply is still streaming.
                out.inFlight.push(b);
                continue;
            }
        }
        if (end === lastIdx) {
            out.tail.push(b);
        } else {
            const arr = out.byMessageIdx.get(end) ?? [];
            arr.push(b);
            out.byMessageIdx.set(end, arr);
        }
    }
    return out;
}
