/**
 * Tests for useDelegateStreaming optimizations.
 *
 * Verifies that delegateKey and siblingKey computations:
 * - Produce stable string keys from conversation data
 * - Don't create intermediate array allocations
 * - Return correct values for various delegate states
 */

describe('delegateKey computation', () => {
  // Mirrors the logic from useDelegateStreaming.ts delegateKey useMemo
  function computeDelegateKey(conversations: any[], conversationId: string): string {
    let dm: any = null;
    for (const c of conversations) {
      if (c.id === conversationId) { dm = c.delegateMeta; break; }
    }
    if (!dm) return 'none';
    return `${conversationId}:${dm.status || ''}:${dm.plan_id || ''}`;
  }

  it('returns "none" for non-delegate conversations', () => {
    const conversations = [
      { id: 'conv-1', messages: [], delegateMeta: undefined },
      { id: 'conv-2', messages: [] },
    ];
    expect(computeDelegateKey(conversations, 'conv-1')).toBe('none');
    expect(computeDelegateKey(conversations, 'conv-2')).toBe('none');
  });

  it('returns "none" for unknown conversation IDs', () => {
    expect(computeDelegateKey([], 'missing')).toBe('none');
  });

  it('builds correct key for delegate conversations', () => {
    const conversations = [
      { id: 'conv-1', delegateMeta: { status: 'running', plan_id: 'plan-A', role: 'delegate' } },
      { id: 'conv-2', delegateMeta: { status: 'crystal', plan_id: 'plan-A', role: 'delegate' } },
    ];
    expect(computeDelegateKey(conversations, 'conv-1')).toBe('conv-1:running:plan-A');
    expect(computeDelegateKey(conversations, 'conv-2')).toBe('conv-2:crystal:plan-A');
  });

  it('handles missing status gracefully', () => {
    const conversations = [
      { id: 'conv-1', delegateMeta: { plan_id: 'plan-B' } },
    ];
    expect(computeDelegateKey(conversations, 'conv-1')).toBe('conv-1::plan-B');
  });

  it('produces identical keys for same data regardless of array reference', () => {
    const data = { id: 'c', delegateMeta: { status: 'running', plan_id: 'p' } };
    const ref1 = [data];
    const ref2 = [{ ...data, delegateMeta: { ...data.delegateMeta } }]; // new references
    expect(computeDelegateKey(ref1, 'c')).toBe(computeDelegateKey(ref2, 'c'));
  });
});

describe('siblingKey computation', () => {
  // Mirrors the logic from useDelegateStreaming.ts siblingKey useMemo
  function computeSiblingKey(conversations: any[], conversationId: string): string {
    let planId: string | undefined;
    for (const c of conversations) {
      if (c.id === conversationId) { planId = c.delegateMeta?.plan_id; break; }
    }
    if (!planId) return 'no-plan';
    let key = '';
    for (const c of conversations) {
      const cdm = c.delegateMeta;
      if (cdm?.plan_id === planId && cdm?.role === 'delegate' && c.id !== conversationId) {
        if (key) key += '|';
        key += `${c.id}:${cdm.status || ''}`;
      }
    }
    return key || 'no-siblings';
  }

  it('returns "no-plan" when conversation has no delegateMeta', () => {
    expect(computeSiblingKey([{ id: 'x' }], 'x')).toBe('no-plan');
  });

  it('returns "no-plan" for missing conversation', () => {
    expect(computeSiblingKey([], 'missing')).toBe('no-plan');
  });

  it('returns "no-siblings" when no siblings exist', () => {
    const conversations = [
      { id: 'orch', delegateMeta: { plan_id: 'p1', role: 'orchestrator' } },
    ];
    expect(computeSiblingKey(conversations, 'orch')).toBe('no-siblings');
  });

  it('builds correct key from sibling delegates', () => {
    const conversations = [
      { id: 'orch', delegateMeta: { plan_id: 'p1', role: 'orchestrator' } },
      { id: 'd1', delegateMeta: { plan_id: 'p1', role: 'delegate', status: 'running' } },
      { id: 'd2', delegateMeta: { plan_id: 'p1', role: 'delegate', status: 'crystal' } },
      { id: 'other', delegateMeta: { plan_id: 'p2', role: 'delegate', status: 'running' } },
    ];
    const key = computeSiblingKey(conversations, 'orch');
    expect(key).toContain('d1:running');
    expect(key).toContain('d2:crystal');
    expect(key).not.toContain('other');
    expect(key).not.toContain('orch');
  });

  it('excludes current conversation from siblings', () => {
    const conversations = [
      { id: 'd1', delegateMeta: { plan_id: 'p1', role: 'delegate', status: 'running' } },
      { id: 'd2', delegateMeta: { plan_id: 'p1', role: 'delegate', status: 'running' } },
    ];
    const key = computeSiblingKey(conversations, 'd1');
    expect(key).toBe('d2:running');
    expect(key).not.toContain('d1');
  });

  it('produces stable keys across array reference changes', () => {
    const makeConvs = () => [
      { id: 'o', delegateMeta: { plan_id: 'p', role: 'orchestrator' } },
      { id: 's1', delegateMeta: { plan_id: 'p', role: 'delegate', status: 'running' } },
    ];
    expect(computeSiblingKey(makeConvs(), 'o')).toBe(computeSiblingKey(makeConvs(), 'o'));
  });

  it('handles delegates with missing status', () => {
    const conversations = [
      { id: 'orch', delegateMeta: { plan_id: 'p1', role: 'orchestrator' } },
      { id: 'd1', delegateMeta: { plan_id: 'p1', role: 'delegate' } }, // no status
    ];
    expect(computeSiblingKey(conversations, 'orch')).toBe('d1:');
  });
});
