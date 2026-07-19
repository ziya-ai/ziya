/**
 * Regression tests for SendChatContainer.sendToolFeedback control flow.
 *
 * sendToolFeedback is a useCallback closing over component state, three
 * React contexts, editor DOM refs, and window.feedbackWebSocket — it is
 * neither exported nor pure, and this repo does not use @testing-library/react
 * (see MessageActions.test.tsx, which mirrors branching logic rather than
 * rendering). So we mirror the exact control flow here and assert which side
 * effects fire in each branch.
 *
 * The invariants under test mirror the current production behavior:
 *   1. NO placeholder message is EVER inserted into the transcript — a
 *      mid-stream human message lands between the original question and the
 *      not-yet-committed assistant response, flagging the question as
 *      unanswered (yellow marker) and stranding a duplicate "You:" line.
 *   2. 'queued' is claimed ONLY after sendFeedback() returns true
 *      (send → verify → reconnect → retry), never from a readiness flag.
 *   3. A failed send preserves the input text so the user can resend.
 * Keep this mirror in lockstep with sendToolFeedback in
 * components/SendChatContainer.tsx.
 */

type FeedbackStatus = 'idle' | 'pending' | 'queued' | 'delivered';

interface SimEnv {
    inputValue: string;
    isSendingFeedback: boolean;
    wsPresent: boolean;
    /** Singleton is bound to a different conversation than the active one */
    conversationMismatch?: boolean;
    /** Result of the first sendFeedback() attempt */
    firstSendOk: boolean;
    /** Whether the reconnect attempt (connect()) resolves or rejects */
    connectSucceeds?: boolean;
    /** Result of the post-reconnect sendFeedback() attempt */
    secondSendOk?: boolean;
    sendFeedbackThrows?: boolean;
}

interface SimEffects {
    earlyReturn: boolean;
    messagesAdded: Array<{ role: string; isFeedback?: boolean; feedbackStatus?: string }>;
    statusTransitions: FeedbackStatus[];
    sentFeedback: string | null;
    connectAttempts: number;
    inputCleared: boolean;
    infoShown: boolean;
    warningShown: boolean;
    errorShown: boolean;
    sendingFlagFinal: boolean;
}

/**
 * Faithful mirror of sendToolFeedback's control flow. Records effects instead
 * of mutating real state/DOM. Structure (guard → try{ rebind → send →
 * reconnect-retry → if(sent) else } catch → finally) matches the source 1:1.
 */
function simulateSendToolFeedback(env: SimEnv): SimEffects {
    const fx: SimEffects = {
        earlyReturn: false,
        messagesAdded: [],
        statusTransitions: [],
        sentFeedback: null,
        connectAttempts: 0,
        inputCleared: false,
        infoShown: false,
        warningShown: false,
        errorShown: false,
        sendingFlagFinal: false,
    };

    // if (!inputValue.trim() || isSendingFeedback) return;
    if (!env.inputValue.trim() || env.isSendingFeedback) {
        fx.earlyReturn = true;
        return fx;
    }

    const feedbackText = env.inputValue.trim();
    let isSendingFeedback = true; // setIsSendingFeedback(true)

    try {
        // Deliberately NO placeholder message is built or inserted — the
        // backend renders "📝 Feedback received:" inline at the actual
        // injection point, and the status chip above the input tracks
        // queued/delivered state.
        let sent = false;
        if (env.wsPresent) {
            // Rebind when the singleton is bound to another conversation.
            // connect() failure here is caught-and-logged in production;
            // flow continues to the send attempt regardless.
            if (env.conversationMismatch) {
                fx.connectAttempts++;
            }
            // sent = feedbackWebSocket.sendFeedback(toolId, text) === true;
            if (env.sendFeedbackThrows) throw new Error('send failed');
            sent = env.firstSendOk;
            if (!sent) {
                // One reconnect-and-retry before reporting failure.
                fx.connectAttempts++;
                if (env.connectSucceeds) {
                    sent = env.secondSendOk === true;
                }
                // connect() rejection is caught; sent remains false.
            }
        }

        if (sent) {
            fx.sentFeedback = feedbackText;
            // setFeedbackStatus('queued') — only after confirmed handoff
            fx.statusTransitions.push('queued');
            // clear input + drafts
            fx.inputCleared = true;
            // message.info(...)
            fx.infoShown = true;
        } else {
            // No socket, or send + reconnect-retry both failed: warn only —
            // NOTHING inserted into the transcript, input untouched.
            fx.warningShown = true;
        }
    } catch {
        // message.error('Failed to send feedback')
        fx.errorShown = true;
    } finally {
        isSendingFeedback = false; // setIsSendingFeedback(false)
    }

    fx.sendingFlagFinal = isSendingFeedback;
    return fx;
}

describe('sendToolFeedback control flow', () => {
    const ok: SimEnv = { inputValue: 'fix the thing', isSendingFeedback: false, wsPresent: true, firstSendOk: true };

    describe('guard', () => {
        it('returns early on empty/whitespace input with no effects', () => {
            const fx = simulateSendToolFeedback({ ...ok, inputValue: '   ' });
            expect(fx.earlyReturn).toBe(true);
            expect(fx.messagesAdded).toHaveLength(0);
            expect(fx.warningShown).toBe(false);
        });

        it('returns early when a feedback send is already in flight', () => {
            const fx = simulateSendToolFeedback({ ...ok, isSendingFeedback: true });
            expect(fx.earlyReturn).toBe(true);
            expect(fx.messagesAdded).toHaveLength(0);
        });
    });

    describe('successful send (first attempt)', () => {
        it('NEVER inserts a transcript message (the yellow-marker regression)', () => {
            const fx = simulateSendToolFeedback(ok);
            expect(fx.messagesAdded).toHaveLength(0);
        });

        it("claims 'queued' only after the confirmed send, and sends the text", () => {
            const fx = simulateSendToolFeedback(ok);
            expect(fx.statusTransitions).toEqual(['queued']);
            expect(fx.sentFeedback).toBe('fix the thing');
        });

        it('clears the input and shows the sent confirmation', () => {
            const fx = simulateSendToolFeedback(ok);
            expect(fx.inputCleared).toBe(true);
            expect(fx.infoShown).toBe(true);
            expect(fx.warningShown).toBe(false);
        });

        it('does not attempt reconnect when the first send succeeds', () => {
            const fx = simulateSendToolFeedback(ok);
            expect(fx.connectAttempts).toBe(0);
        });
    });

    describe('conversation rebind', () => {
        it('reconnects to the current conversation before sending on mismatch', () => {
            const fx = simulateSendToolFeedback({ ...ok, conversationMismatch: true });
            expect(fx.connectAttempts).toBe(1);
            expect(fx.sentFeedback).toBe('fix the thing');
            expect(fx.statusTransitions).toEqual(['queued']);
        });
    });

    describe('dead socket: reconnect-and-retry', () => {
        it('recovers when reconnect succeeds and the retry send delivers', () => {
            const fx = simulateSendToolFeedback({ ...ok, firstSendOk: false, connectSucceeds: true, secondSendOk: true });
            expect(fx.connectAttempts).toBe(1);
            expect(fx.statusTransitions).toEqual(['queued']);
            expect(fx.inputCleared).toBe(true);
            expect(fx.messagesAdded).toHaveLength(0);
        });

        it("never claims 'queued' when reconnect fails (the false-queued regression)", () => {
            const fx = simulateSendToolFeedback({ ...ok, firstSendOk: false, connectSucceeds: false });
            expect(fx.statusTransitions).toHaveLength(0);
            expect(fx.warningShown).toBe(true);
            expect(fx.infoShown).toBe(false);
        });

        it("never claims 'queued' when the retry send also fails", () => {
            const fx = simulateSendToolFeedback({ ...ok, firstSendOk: false, connectSucceeds: true, secondSendOk: false });
            expect(fx.statusTransitions).toHaveLength(0);
            expect(fx.warningShown).toBe(true);
        });

        it('preserves the input on total failure so the user can resend', () => {
            const fx = simulateSendToolFeedback({ ...ok, firstSendOk: false, connectSucceeds: false });
            expect(fx.inputCleared).toBe(false);
            expect(fx.messagesAdded).toHaveLength(0);
        });
    });

    describe('socket absent', () => {
        it('warns, inserts nothing, preserves input', () => {
            const fx = simulateSendToolFeedback({ ...ok, wsPresent: false });
            expect(fx.warningShown).toBe(true);
            expect(fx.messagesAdded).toHaveLength(0);
            expect(fx.inputCleared).toBe(false);
            expect(fx.statusTransitions).toHaveLength(0);
        });
    });

    describe('send throws', () => {
        it('surfaces an error, no transcript message, and always resets the in-flight flag', () => {
            const fx = simulateSendToolFeedback({ ...ok, sendFeedbackThrows: true });
            expect(fx.errorShown).toBe(true);
            expect(fx.sendingFlagFinal).toBe(false);
            expect(fx.messagesAdded).toHaveLength(0);
            expect(fx.statusTransitions).toHaveLength(0);
        });
    });
});
