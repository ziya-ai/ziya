/**
 * consentStore — the pure reducer the ConsentPanel sits on.
 *
 * The contract: the panel's view of "what is waiting on me" is the union of
 * live stream frames and the server's authoritative open-list for the
 * conversation being viewed, and an answered/closed request never lingers.
 */

import {
  applyAnswered,
  applyOpened,
  applyServerList,
  describe as describeConsent,
  openFor,
  type ConsentRecord,
} from '../consentStore';

const rec = (id: string, conv: string, extra: Partial<ConsentRecord> = {}): ConsentRecord => ({
  request_id: id,
  conversation_id: conv,
  payload: { kind: 'tool_call', tool: 'git', op: 'commit', args: { message: 'x' }, preflight: null },
  opened_at: 1,
  ...extra,
});

describe('applyOpened', () => {
  it('adds a new open request and orders by opened_at', () => {
    let list = applyOpened([], rec('b', 'c1', { opened_at: 20 }));
    list = applyOpened(list, rec('a', 'c1', { opened_at: 10 }));
    expect(list.map(r => r.request_id)).toEqual(['a', 'b']);
  });

  it('replaces an existing entry rather than duplicating it', () => {
    let list = applyOpened([], rec('a', 'c1'));
    list = applyOpened(list, rec('a', 'c1', { opened_at: 5 }));
    expect(list).toHaveLength(1);
    expect(list[0].opened_at).toBe(5);
  });

  it('drops a record that arrives already settled or closed', () => {
    const settled = rec('a', 'c1', { answer: { decision: 'approve' } });
    expect(applyOpened([rec('a', 'c1')], settled)).toEqual([]);
    expect(applyOpened([], rec('z', 'c1', { closed: true }))).toEqual([]);
  });
});

describe('applyAnswered', () => {
  it('removes only the answered request', () => {
    const list = [rec('a', 'c1'), rec('b', 'c1')];
    expect(applyAnswered(list, { request_id: 'a' }).map(r => r.request_id)).toEqual(['b']);
  });

  it('is idempotent (panel-local removal + stream frame both fire)', () => {
    const once = applyAnswered([rec('a', 'c1')], { request_id: 'a' });
    expect(applyAnswered(once, { request_id: 'a' })).toEqual([]);
  });
});

describe('applyServerList (reconnect handshake)', () => {
  it('replaces the viewed conversation from the server, keeps other conversations', () => {
    const list = [rec('stale', 'c1'), rec('other', 'c2')];
    const fromServer = [rec('fresh', 'c1')];
    const out = applyServerList(list, 'c1', fromServer);
    expect(out.map(r => r.request_id).sort()).toEqual(['fresh', 'other']);
  });

  it('fills in conversation_id for records the server keyed by owner_id only', () => {
    const fromServer = [{ ...rec('x', 'c1'), conversation_id: undefined, owner_id: 'c1' }];
    const out = applyServerList([], 'c1', fromServer);
    expect(openFor(out, 'c1')).toHaveLength(1);
  });

  it('never surfaces a settled record the server still returns', () => {
    const fromServer = [rec('done', 'c1', { answer: { decision: 'reject' } })];
    expect(applyServerList([], 'c1', fromServer)).toEqual([]);
  });
});

describe('openFor / describe', () => {
  it('scopes to one conversation using conversation_id or owner_id', () => {
    const list = [rec('a', 'c1'), { ...rec('b', 'c2'), conversation_id: undefined, owner_id: 'c2' }];
    expect(openFor(list, 'c2').map(r => r.request_id)).toEqual(['b']);
  });

  it('describes a tool call by tool and op, and a question by its text', () => {
    expect(describeConsent(rec('a', 'c1'))).toBe('Allow git commit?');
    expect(describeConsent(rec('a', 'c1', {
      payload: { kind: 'tool_call', tool: 'file_write', op: '*', args: {}, preflight: null },
    }))).toBe('Allow file_write?');
    expect(describeConsent(rec('a', 'c1', {
      payload: { kind: 'question', text: 'Ship it?', choices: [] },
    }))).toBe('Ship it?');
  });
});
