/**
 * Unit tests for the mid-stream feedback retention store.
 *
 * This store replaced a single `pendingFeedbackRef` slot inside
 * SendChatContainer that held ONE conversation's texts at a time. Two defects
 * followed from that shape, and both are pinned here:
 *
 *   1. Sending feedback in conversation B replaced conversation A's retained
 *      copy outright, so A's text could never be recovered.
 *   2. Recovery was gated on the retained conversation being the one on
 *      screen, which is the opposite of the intended behaviour: feedback
 *      belongs to the conversation it was typed into regardless of where the
 *      user is looking.
 *
 * The store is deliberately a plain module Map so it is independent of mount
 * and of the viewed conversation. `take` is atomic so two observers of the
 * same turn end cannot both resubmit.
 */

import {
    recordSentFeedback,
    retireDeliveredFeedback,
    peekRetainedFeedback,
    hasRetainedFeedback,
    takeRetainedFeedback,
    discardRetainedFeedback,
    EXECUTOR_ACK_CAP,
    __resetFeedbackRetentionForTests,
} from '../feedbackRetention';

beforeEach(() => {
    __resetFeedbackRetentionForTests();
});

describe('recording', () => {
    it('accumulates multiple sends within one conversation in order', () => {
        recordSentFeedback('conv-A', 'first');
        recordSentFeedback('conv-A', 'second');
        expect(peekRetainedFeedback('conv-A')).toEqual(['first', 'second']);
    });

    it('keeps conversations independent (the silent-replacement defect)', () => {
        recordSentFeedback('conv-A', 'for A');
        recordSentFeedback('conv-B', 'for B');
        expect(peekRetainedFeedback('conv-A')).toEqual(['for A']);
        expect(peekRetainedFeedback('conv-B')).toEqual(['for B']);
    });

    it('ignores empty text and empty conversation ids', () => {
        recordSentFeedback('conv-A', '');
        recordSentFeedback('', 'orphan');
        expect(hasRetainedFeedback('conv-A')).toBe(false);
        expect(hasRetainedFeedback('')).toBe(false);
    });

    it('reports nothing retained for an unknown conversation', () => {
        expect(peekRetainedFeedback('never-seen')).toEqual([]);
        expect(hasRetainedFeedback('never-seen')).toBe(false);
    });
});

describe('retiring on a delivery ack', () => {
    it('retires exactly the text the ack names, leaving the rest', () => {
        recordSentFeedback('conv-A', 'use the other index');
        recordSentFeedback('conv-A', 'and skip the cache');
        retireDeliveredFeedback('conv-A', 'use the other index');
        expect(peekRetainedFeedback('conv-A')).toEqual(['and skip the cache']);
    });

    it('retires every text named by a joined batch ack', () => {
        recordSentFeedback('conv-A', 'alpha');
        recordSentFeedback('conv-A', 'beta');
        // The executor joins a drained batch into one injection.
        retireDeliveredFeedback('conv-A', 'alpha beta');
        expect(hasRetainedFeedback('conv-A')).toBe(false);
    });

    it('clears everything when the ack is truncated and cannot be matched', () => {
        const long = 'x'.repeat(200);
        recordSentFeedback('conv-A', long);
        recordSentFeedback('conv-A', 'also this');
        // Executor caps its ack at EXECUTOR_ACK_CAP characters.
        retireDeliveredFeedback('conv-A', long.slice(0, EXECUTOR_ACK_CAP));
        expect(hasRetainedFeedback('conv-A')).toBe(false);
    });

    it('never touches another conversation, even for identical text', () => {
        recordSentFeedback('conv-A', 'same words');
        recordSentFeedback('conv-B', 'same words');
        retireDeliveredFeedback('conv-B', 'same words');
        expect(peekRetainedFeedback('conv-A')).toEqual(['same words']);
        expect(hasRetainedFeedback('conv-B')).toBe(false);
    });

    it('is a no-op for a conversation holding nothing', () => {
        expect(() => retireDeliveredFeedback('conv-A', 'anything')).not.toThrow();
        expect(hasRetainedFeedback('conv-A')).toBe(false);
    });

    it('retains an unrelated text when the ack names neither of two', () => {
        recordSentFeedback('conv-A', 'alpha');
        retireDeliveredFeedback('conv-A', 'something else entirely');
        expect(peekRetainedFeedback('conv-A')).toEqual(['alpha']);
    });
});

describe('taking for recovery', () => {
    it('returns the texts in send order and empties the slot', () => {
        recordSentFeedback('conv-A', 'first');
        recordSentFeedback('conv-A', 'second');
        expect(takeRetainedFeedback('conv-A')).toEqual(['first', 'second']);
        expect(hasRetainedFeedback('conv-A')).toBe(false);
    });

    it('is atomic: a second take yields nothing (the double-submit guard)', () => {
        recordSentFeedback('conv-A', 'only once');
        expect(takeRetainedFeedback('conv-A')).toEqual(['only once']);
        expect(takeRetainedFeedback('conv-A')).toEqual([]);
    });

    it('takes only the named conversation', () => {
        recordSentFeedback('conv-A', 'for A');
        recordSentFeedback('conv-B', 'for B');
        expect(takeRetainedFeedback('conv-A')).toEqual(['for A']);
        expect(peekRetainedFeedback('conv-B')).toEqual(['for B']);
    });

    it('yields nothing for an unknown conversation', () => {
        expect(takeRetainedFeedback('never-seen')).toEqual([]);
    });

    it('yields nothing once the ack has retired the text', () => {
        recordSentFeedback('conv-A', 'delivered already');
        retireDeliveredFeedback('conv-A', 'delivered already');
        expect(takeRetainedFeedback('conv-A')).toEqual([]);
    });

    it('does not expose the internal array for mutation', () => {
        recordSentFeedback('conv-A', 'first');
        const taken = takeRetainedFeedback('conv-A');
        taken.push('injected by caller');
        recordSentFeedback('conv-A', 'second');
        expect(peekRetainedFeedback('conv-A')).toEqual(['second']);
    });
});

describe('discarding', () => {
    it('drops a conversation\'s retained feedback without returning it', () => {
        recordSentFeedback('conv-A', 'abandon me');
        discardRetainedFeedback('conv-A');
        expect(hasRetainedFeedback('conv-A')).toBe(false);
    });

    it('leaves other conversations alone', () => {
        recordSentFeedback('conv-A', 'for A');
        recordSentFeedback('conv-B', 'for B');
        discardRetainedFeedback('conv-A');
        expect(peekRetainedFeedback('conv-B')).toEqual(['for B']);
    });
});
