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
 * The invariant under test is the production bug this fix closed: the feedback
 * placeholder (a human message with _feedbackStatus:'pending') must be
 * inserted ONLY when the feedback WebSocket is ready to deliver it. Inserting
 * it before the readiness check stranded a permanent 'pending' human message
 * in the transcript when delivery never happened. Keep this mirror in lockstep
 * with sendToolFeedback in components/SendChatContainer.tsx.
 */

type FeedbackStatus = 'idle' | 'pending' | 'queued' | 'delivered';

interface SimEnv {
    inputValue: string;
    isSendingFeedback: boolean;
    wsPresent: boolean;
    wsReady: boolean;
    sendFeedbackThrows?: boolean;
}

interface SimEffects {
    earlyReturn: boolean;
    messagesAdded: Array<{ role: string; isFeedback?: boolean; feedbackStatus?: string }>;
    statusTransitions: FeedbackStatus[];
    sentFeedback: string | null;
    inputCleared: boolean;
    infoShown: boolean;
    warningShown: boolean;
    errorShown: boolean;
    sendingFlagFinal: boolean;
}

/**
 * Faithful mirror of sendToolFeedback's control flow. Records effects instead
 * of mutating real state/DOM. Structure (guard → try{ if(ready) else } catch
 * → finally) matches the source 1:1.
 */
function simulateSendToolFeedback(env: SimEnv): SimEffects {
    const fx: SimEffects = {
        earlyReturn: false,
        messagesAdded: [],
        statusTransitions: [],
        sentFeedback: null,
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
        // Placeholder is BUILT here but NOT inserted yet.
        const feedbackMessage = {
            role: 'human',
            isFeedback: true,
            feedbackStatus: 'pending',
        };

        if (env.wsPresent && env.wsReady) {
            // addMessageToConversation(feedbackMessage)
            fx.messagesAdded.push(feedbackMessage);
            // setFeedbackStatus('pending')
            fx.statusTransitions.push('pending');
            // feedbackWebSocket.sendFeedback(toolId, feedbackText)
            if (env.sendFeedbackThrows) throw new Error('send failed');
            fx.sentFeedback = feedbackText;
            // setFeedbackStatus('queued')
            fx.statusTransitions.push('queued');
            // clear input + drafts
            fx.inputCleared = true;
            // message.info(...)
            fx.infoShown = true;
        } else {
            // WS not ready: warn only — NOTHING inserted, input untouched.
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
    const ready: SimEnv = { inputValue: 'fix the thing', isSendingFeedback: false, wsPresent: true, wsReady: true };

    describe('guard', () => {
        it('returns early on empty/whitespace input with no effects', () => {
            const fx = simulateSendToolFeedback({ ...ready, inputValue: '   ' });
            expect(fx.earlyReturn).toBe(true);
            expect(fx.messagesAdded).toHaveLength(0);
            expect(fx.warningShown).toBe(false);
        });

        it('returns early when a feedback send is already in flight', () => {
            const fx = simulateSendToolFeedback({ ...ready, isSendingFeedback: true });
            expect(fx.earlyReturn).toBe(true);
            expect(fx.messagesAdded).toHaveLength(0);
        });
    });

    describe('WebSocket ready', () => {
        it('inserts exactly one pending feedback placeholder', () => {
            const fx = simulateSendToolFeedback(ready);
            expect(fx.messagesAdded).toHaveLength(1);
            expect(fx.messagesAdded[0]).toMatchObject({ role: 'human', isFeedback: true, feedbackStatus: 'pending' });
        });

        it('transitions pending -> queued and sends the feedback text', () => {
            const fx = simulateSendToolFeedback(ready);
            expect(fx.statusTransitions).toEqual(['pending', 'queued']);
            expect(fx.sentFeedback).toBe('fix the thing');
        });

        it('clears the input and shows the sent confirmation', () => {
            const fx = simulateSendToolFeedback(ready);
            expect(fx.inputCleared).toBe(true);
            expect(fx.infoShown).toBe(true);
            expect(fx.warningShown).toBe(false);
        });
    });

    describe('WebSocket NOT ready (the stranding regression)', () => {
        it('inserts NO placeholder when the socket is absent', () => {
            const fx = simulateSendToolFeedback({ ...ready, wsPresent: false, wsReady: false });
            expect(fx.messagesAdded).toHaveLength(0);
            expect(fx.statusTransitions).toHaveLength(0);
        });

        it('inserts NO placeholder when the socket exists but is not ready', () => {
            const fx = simulateSendToolFeedback({ ...ready, wsPresent: true, wsReady: false });
            expect(fx.messagesAdded).toHaveLength(0);
        });

        it('preserves the input (does not clear) and warns the user', () => {
            const fx = simulateSendToolFeedback({ ...ready, wsReady: false });
            expect(fx.inputCleared).toBe(false);
            expect(fx.warningShown).toBe(true);
            expect(fx.infoShown).toBe(false);
        });
    });

    describe('send throws after a ready check', () => {
        it('surfaces an error and always resets the in-flight flag', () => {
            const fx = simulateSendToolFeedback({ ...ready, sendFeedbackThrows: true });
            expect(fx.errorShown).toBe(true);
            expect(fx.sendingFlagFinal).toBe(false);
            // placeholder was inserted before the throw (pending only, no queued)
            expect(fx.statusTransitions).toEqual(['pending']);
        });
    });
});
