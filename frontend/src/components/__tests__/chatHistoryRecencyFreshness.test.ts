/**
 * Recency filter must see current activity timestamps, and a fork must not
 * inherit its source's activity time.
 *
 * Symptom (2026-11-03): a fork created minutes ago vanished from the
 * 24h / 7d view after a reload, and reappeared as soon as another fork was
 * made.  Two causes, both asserted here on source (MUIChatHistory and
 * ChatContext cannot be mounted cheaply in jest):
 *
 *  1. The tree memo's fast exit returns the cached tree when neither the
 *     structural hash nor the sort hash changed.  The sort hash covered only
 *     lastAccessedAt, but the recency filter (conversationActivityAt) and the
 *     sort comparator both prefer lastActiveAt.  A sync that corrected
 *     lastActiveAt left the cached node holding the stale conversation
 *     object, so the filter judged the row by an old time until an unrelated
 *     structural change (the second fork) forced a rebuild.
 *  2. forkConversation spreads the source (\`...source\`), copying the
 *     source's lastActiveAt.  conversationActivityAt prefers lastActiveAt
 *     over lastAccessedAt, so a brand-new fork of an old conversation was
 *     dated by its source and filtered out locally, while the server (which
 *     receives lastAccessedAt as lastActiveAt) held the correct time.
 *
 * DIRECTION: both fail against the unpatched code.
 */
import * as fs from 'fs';
import * as path from 'path';

const strip = (s: string) => s.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');
const HISTORY = strip(fs.readFileSync(path.resolve(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8'));
const CHAT = strip(fs.readFileSync(path.resolve(__dirname, '..', '..', 'context', 'ChatContext.tsx'), 'utf8'));

function between(src: string, start: string, end: string): string {
  const s = src.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = src.indexOf(end, s);
  expect(e).toBeGreaterThan(s);
  return src.slice(s, e);
}

describe('sidebar tree cache: lastActiveAt invalidates the cached order', () => {
  it('the sort hash covers lastActiveAt as well as lastAccessedAt', () => {
    const sortHash = between(HISTORY, 'const oh = fnv1a();', 'const sortHash = oh.value();');
    expect(sortHash).toMatch(/c\.lastAccessedAt/);
    expect(sortHash).toMatch(/\(?c(?: as any\))?\.lastActiveAt/);
  });

  it('the recency filter consumes the memo output it depends on', () => {
    // Positive check that the path this hash protects is the one the filter reads.
    expect(HISTORY).toContain('applyRecencyFilter(treeDataRaw,');
  });
});

describe('addMessageToConversation stamps lastActiveAt', () => {
  // A local write is activity; without this the filter judged a conversation
  // being written in this browser by whatever lastActiveAt the last
  // server-newer sync left, which could be weeks old.
  it('on the existing-conversation append', () => {
    const body = between(CHAT, 'const shouldMarkUnread = message.role', 'const newConversation');
    const existing = between(body, 'messages: [...conv.messages, message],', '_version: Date.now(),');
    expect(existing).toMatch(/lastActiveAt:\s*Date\.now\(\)/);
  });
  it('on the inline new conversation', () => {
    const body = between(CHAT, 'const shouldMarkUnread = message.role', 'const newConversation');
    const created = between(body, 'messages: [message],', 'isActive: true,');
    expect(created).toMatch(/lastActiveAt:\s*Date\.now\(\)/);
  });
});

describe('forkConversation dates the fork by its own creation', () => {
  it('sets lastActiveAt on the fork after spreading the source', () => {
    const body = between(CHAT, 'const forkConversation = useCallback(', 'const toggleConversationGlobal = useCallback(');
    const literal = between(body, 'const forked:', 'isActive: true,');
    expect(literal).toMatch(/lastActiveAt:\s*\w/);
    // It must come after the spread, or the spread would overwrite it.
    // (The type annotation may also name lastActiveAt; match the property.)
    expect(literal.indexOf('lastActiveAt: Date.now()')).toBeGreaterThan(literal.indexOf('...source,'));
  });
});
