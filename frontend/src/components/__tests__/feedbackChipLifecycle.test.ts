/**
 * Regression tests for the feedback status chip below the composer.
 *
 * The chip (`feedbackStatus`) is a SINGLE slot on one SendChatContainer
 * instance shared by every conversation — the component is not remounted per
 * conversation. Every observed bug in this area is the same mistake: a guard
 * that is either missing (an ack for conversation A mutating state while B is
 * on screen) or applied to the wrong subject (pruning gated on the viewed
 * conversation rather than the conversation that owns the text).
 *
 * Scope note: the chip is now PRESENTATION ONLY. Retention lives in the
 * feedbackRetention store (see utils/__tests__/feedbackRetention.test.ts) and
 * recovery lives in FeedbackRecoveryWatcher (see
 * feedbackRecoveryWatcher.test.tsx, which renders the real component). The
 * chip must therefore never resubmit anything — a composer that recovers is
 * a composer that can only recover the conversation it is looking at, which
 * is the defect the watcher exists to remove.
 *
 * This repo does not render SendChatContainer in tests, so the effect logic is
 * mirrored here. `ChipSim` mirrors the CURRENT source; `legacySettle` mirrors
 * the previous reset rule so the defect it caused is pinned rather than
 * described. Keep both in lockstep with SendChatContainer.tsx.
 */

type Status = 'idle' | 'pending' | 'queued' | 'delivered' | 'resubmitting';

/** Stand-in for the module-level feedbackRetention store, keyed per conversation. */
class RetentionStub {
    private retained = new Map<string, string[]>();
    record(conversationId: string, text: string): void {
        const existing = this.retained.get(conversationId);
        if (existing) existing.push(text);
        else this.retained.set(conversationId, [text]);
    }
    retire(conversationId: string, ackMessage: string): void {
        const texts = this.retained.get(conversationId);
        if (!texts) return;
        const remaining = ackMessage.length >= 80
            ? []
            : texts.filter(t => !ackMessage.includes(t));
        if (remaining.length === 0) this.retained.delete(conversationId);
        else this.retained.set(conversationId, remaining);
    }
    has(conversationId: string): boolean {
        return (this.retained.get(conversationId)?.length ?? 0) > 0;
    }
    peek(conversationId: string): string[] {
        return [...(this.retained.get(conversationId) ?? [])];
    }
}

class ChipSim {
    status: Status = 'idle';
    /** feedbackChipConvRef — which conversation the chip describes */
    chipConv: string | null = null;
    store = new RetentionStub();
    /**
     * Anything the composer resubmitted. Must stay empty: recovery belongs to
     * FeedbackRecoveryWatcher, which is not gated on the viewed conversation.
     */
    resubmitted: string[] = [];

    constructor(public viewing: string) { }

    /** sendToolFeedback, success branch */
    send(text: string): void {
        this.store.record(this.viewing, text);
        this.status = 'queued';
    }

    /** WebSocket feedback_status ack (backend stamps conversation_id) */
    wsQueuedAck(convId: string | undefined): void {
        if (convId && convId !== this.viewing) return;
        this.status = 'queued';
    }

    /** SSE feedbackDelivered ack. Retirement keys off the ACK's conversation. */
    deliveredAck(convId: string, message: string): void {
        const ack = message.slice(0, 80); // executor caps at [:80]
        this.store.retire(convId, ack);
        if (convId === this.viewing) this.status = 'delivered';
    }

    /** FEEDBACK_RESUBMITTED_EVENT from the watcher. */
    resubmittedEvent(convId: string): void {
        if (convId !== this.viewing) return;
        this.status = 'resubmitting';
    }

    /** The [isCurrentlyStreaming, currentConversationId] chip effect. */
    settle(streaming: boolean): void {
        const retainedHere = this.store.has(this.viewing);
        const switched = this.chipConv !== this.viewing;
        this.chipConv = this.viewing;
        if (switched || (!streaming && !retainedHere)) {
            this.status = retainedHere ? 'queued' : 'idle';
        }
    }

    switchTo(conv: string, streaming: boolean): void {
        this.viewing = conv;
        this.settle(streaming);
    }
}

/** The pre-fix reset rule, kept only to pin the defect it produced. */
function legacySettle(sim: ChipSim, streaming: boolean): void {
    const stranded = sim.store.has(sim.viewing);
    if (stranded && !streaming) {
        sim.status = 'resubmitting';
        sim.resubmitted.push(sim.store.peek(sim.viewing).join('\n\n'));
        return;
    }
    if (!streaming && !stranded) sim.status = 'idle';
}

describe('feedback chip: conversation switching', () => {
    it("does not carry 'queued' into a different conversation that is streaming", () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('use the other index');
        expect(sim.status).toBe('queued');
        // B is streaming, so the legacy reset (which required !streaming) never ran.
        sim.switchTo('conv-B', true);
        expect(sim.status).toBe('idle');
    });

    it("does not carry 'resubmitting' into a different conversation that is streaming", () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('never consumed');
        sim.resubmittedEvent('conv-A');
        expect(sim.status).toBe('resubmitting');
        sim.switchTo('conv-B', true);
        expect(sim.status).toBe('idle');
    });

    it('the legacy reset rule leaves the chip stuck (the reported defect)', () => {
        const sim = new ChipSim('conv-A');
        sim.send('use the other index');
        sim.status = 'queued';
        sim.viewing = 'conv-B';
        legacySettle(sim, true);   // B is streaming
        expect(sim.status).toBe('queued');   // stuck: no path back to idle
    });

    it("another conversation's retained feedback does not block this one's reset", () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('for A');
        sim.switchTo('conv-B', false);
        expect(sim.status).toBe('idle');
    });

    it("restores 'queued' when returning to a conversation whose feedback is still in flight", () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('for A');
        sim.switchTo('conv-B', false);
        expect(sim.status).toBe('idle');
        sim.switchTo('conv-A', true);
        expect(sim.status).toBe('queued');
    });
});

describe('feedback chip: WebSocket queued ack', () => {
    it('ignores an ack stamped with a conversation the user has left', () => {
        const sim = new ChipSim('conv-B');
        sim.settle(false);
        sim.wsQueuedAck('conv-A');
        expect(sim.status).toBe('idle');
    });

    it('accepts an ack for the viewed conversation', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.wsQueuedAck('conv-A');
        expect(sim.status).toBe('queued');
    });

    it('accepts an unstamped ack (older backend) rather than dropping it', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.wsQueuedAck(undefined);
        expect(sim.status).toBe('queued');
    });
});

describe('feedback chip: delivered ack retires the retained copy', () => {
    it('retires while the user is viewing ANOTHER conversation (the duplicate-send bug)', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('adjust the query');
        sim.switchTo('conv-B', true);
        sim.deliveredAck('conv-A', 'adjust the query');
        expect(sim.store.has('conv-A')).toBe(false);
        expect(sim.status).toBe('idle');   // B's chip must not light for A's ack
    });

    it('retires exactly the acked text when only one of two is delivered', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('first note');
        sim.send('second note');
        sim.deliveredAck('conv-A', 'first note');
        expect(sim.store.peek('conv-A')).toEqual(['second note']);
    });

    it('clears everything on a truncated ack, since it cannot be matched', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        const long = 'x'.repeat(120);
        sim.send(long);
        sim.deliveredAck('conv-A', long);  // arrives capped at 80 chars
        expect(sim.store.has('conv-A')).toBe(false);
    });

    it("never retires another conversation's retained feedback", () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('for A');
        sim.deliveredAck('conv-B', 'for A');   // same text, different conversation
        expect(sim.store.peek('conv-A')).toEqual(['for A']);
    });
});

describe('feedback chip: retention is per conversation', () => {
    it('sending in B does not discard A (the single-slot data loss)', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('for A');
        sim.switchTo('conv-B', true);
        sim.send('for B');
        expect(sim.store.peek('conv-A')).toEqual(['for A']);
        expect(sim.store.peek('conv-B')).toEqual(['for B']);
    });

    it('accumulates two sends in the same conversation (the observed reproduction)', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('first point');
        sim.send('second point');
        // No ack ever arrives, so both are still retained for the watcher.
        expect(sim.store.peek('conv-A')).toEqual(['first point', 'second point']);
    });
});

describe('feedback chip: the composer never resubmits', () => {
    it('a turn ending with retained feedback resubmits nothing here', () => {
        const sim = new ChipSim('conv-A');
        sim.settle(true);
        sim.send('never consumed');
        sim.settle(false);            // turn ends while viewing conv-A
        expect(sim.resubmitted).toEqual([]);
    });

    it('the legacy rule DID resubmit from the composer (what was moved out)', () => {
        const sim = new ChipSim('conv-A');
        sim.send('never consumed');
        legacySettle(sim, false);
        expect(sim.resubmitted).toEqual(['never consumed']);
    });
});
