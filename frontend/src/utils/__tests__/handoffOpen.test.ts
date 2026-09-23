/**
 * The token bar's "⇢ Hand off…" affordance and the sidebar's drawer live in
 * different component trees; this event is the seam between them.
 */
import { dispatchHandoffOpen, subscribeHandoffOpen, HANDOFF_OPEN_EVENT } from '../handoffOpen';

describe('handoffOpen event bridge', () => {
  it('delivers the conversation id to a subscriber and unsubscribes cleanly', () => {
    const seen: string[] = [];
    const off = subscribeHandoffOpen(id => seen.push(id));
    dispatchHandoffOpen('conv-a');
    expect(seen).toEqual(['conv-a']);
    off();
    dispatchHandoffOpen('conv-b');
    expect(seen).toEqual(['conv-a']);
  });

  it('ignores an empty id and a malformed event', () => {
    const seen: string[] = [];
    const off = subscribeHandoffOpen(id => seen.push(id));
    dispatchHandoffOpen('');
    document.dispatchEvent(new CustomEvent(HANDOFF_OPEN_EVENT, { detail: {} }));
    expect(seen).toEqual([]);
    off();
  });
});
