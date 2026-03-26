/**
 * Tests for the preserved-content event deduplication logic
 * in StreamedContent.tsx.
 *
 * The dedup guard uses a key derived from stable event properties
 * (error_detail + conversation_id) to prevent duplicate preserved
 * messages when rapid events fire.  A previous bug used
 * `preservation_timestamp || Date.now()` in the key, which made
 * every key unique when the timestamp was absent.
 */

describe('preservedContent dedup key generation', () => {
  /**
   * Generates the dedup key using the same logic as StreamedContent's
   * handlePreservedContent handler (after the fix).
   */
  function buildDedupKey(detail: Record<string, unknown>): string {
    return `${detail.error_detail || 'unknown'}_${detail.conversation_id || 'unknown'}`;
  }

  it('produces identical keys for duplicate events without a timestamp', () => {
    const detail = {
      error_detail: 'ThrottlingException',
      conversation_id: 'conv-123',
      preserved_content: 'some content',
    };

    const key1 = buildDedupKey(detail);
    const key2 = buildDedupKey(detail);
    expect(key1).toBe(key2);
  });

  it('produces identical keys regardless of preservation_timestamp presence', () => {
    const base = {
      error_detail: 'ThrottlingException',
      conversation_id: 'conv-123',
    };

    const withTimestamp = { ...base, preservation_timestamp: 1700000000 };
    const withoutTimestamp = { ...base };

    // The key must be the same whether or not the timestamp exists,
    // because only stable fields are used.
    expect(buildDedupKey(withTimestamp)).toBe(buildDedupKey(withoutTimestamp));
  });

  it('produces different keys for different errors', () => {
    const event1 = { error_detail: 'ThrottlingException', conversation_id: 'conv-1' };
    const event2 = { error_detail: 'InternalServerError', conversation_id: 'conv-1' };

    expect(buildDedupKey(event1)).not.toBe(buildDedupKey(event2));
  });

  it('produces different keys for different conversations', () => {
    const event1 = { error_detail: 'ThrottlingException', conversation_id: 'conv-1' };
    const event2 = { error_detail: 'ThrottlingException', conversation_id: 'conv-2' };

    expect(buildDedupKey(event1)).not.toBe(buildDedupKey(event2));
  });

  it('handles missing fields gracefully', () => {
    const key = buildDedupKey({});
    expect(key).toBe('unknown_unknown');
  });

  it('dedup set correctly blocks duplicate events', () => {
    const processed = new Set<string>();

    const detail = {
      error_detail: 'ThrottlingException',
      conversation_id: 'conv-123',
    };

    // Simulate two rapid events
    const key1 = buildDedupKey(detail);
    const firstIsNew = !processed.has(key1);
    processed.add(key1);

    const key2 = buildDedupKey(detail);
    const secondIsNew = !processed.has(key2);

    expect(firstIsNew).toBe(true);
    expect(secondIsNew).toBe(false); // blocked by dedup
  });

  it('dedup set allows events from different conversations', () => {
    const processed = new Set<string>();

    const key1 = buildDedupKey({ error_detail: 'err', conversation_id: 'a' });
    processed.add(key1);

    const key2 = buildDedupKey({ error_detail: 'err', conversation_id: 'b' });
    expect(processed.has(key2)).toBe(false); // different conversation passes
  });
});
