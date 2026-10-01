/**
 * DUPLICATE_GUARD decision (utils/messageDedup.ts).
 *
 * Regression: a user's second "applied" in one conversation was dropped from
 * state because an earlier message had the same role+content, while it was
 * still sent to the model — the record read assistant → assistant (conv
 * 8063d5b2, 2026-09-28).  The guard must still stop history replays, which
 * re-add messages that carry their original id / timestamp.
 */
import { isDuplicateMessage, DOUBLE_SUBMIT_WINDOW_MS } from '../messageDedup';

const NOW = 1_700_000_000_000;
const history = [
  { id: 'h1', role: 'human', content: 'applied', _timestamp: NOW - 600_000 },
  { id: 'a1', role: 'assistant', content: 'Diffs are in.', _timestamp: NOW - 590_000 },
];

test('a human retyping the same short prompt minutes later is NOT a duplicate', () => {
  // Fresh submission: no id on arrival (the caller mints one), new timestamp.
  const incoming = { id: 'minted', role: 'human', content: 'applied', _timestamp: NOW };
  expect(isDuplicateMessage(incoming, history, 'minted', NOW)).toBe(false);
});

test('a replayed message that arrived with a known id IS a duplicate', () => {
  const replay = { id: 'h1', role: 'human', content: 'applied', _timestamp: NOW };
  expect(isDuplicateMessage(replay, history, undefined, NOW)).toBe(true);
});

test('same role+content with the identical timestamp is a replay', () => {
  const replay = { id: 'minted', role: 'human', content: 'applied', _timestamp: NOW - 600_000 };
  expect(isDuplicateMessage(replay, history, 'minted', NOW)).toBe(true);
});

test('an identical submission within the double-submit window is a duplicate', () => {
  const recent = [{ id: 'h9', role: 'human', content: 'go', _timestamp: NOW - 1_000 }];
  const twice = { id: 'minted', role: 'human', content: 'go', _timestamp: NOW };
  expect(isDuplicateMessage(twice, recent, 'minted', NOW)).toBe(true);
  const later = { id: 'minted', role: 'human', content: 'go', _timestamp: NOW + DOUBLE_SUBMIT_WINDOW_MS + 1 };
  expect(isDuplicateMessage(later, recent, 'minted', NOW + DOUBLE_SUBMIT_WINDOW_MS + 1)).toBe(false);
});

test('different content is never a duplicate (positive control)', () => {
  const incoming = { id: 'minted', role: 'human', content: 'applied again', _timestamp: NOW };
  expect(isDuplicateMessage(incoming, history, 'minted', NOW)).toBe(false);
});

test('legacy timestamp-less assistant duplicates keep the conservative behaviour', () => {
  const legacy = [{ role: 'assistant', content: 'same' }];
  expect(isDuplicateMessage({ id: 'm', role: 'assistant', content: 'same' }, legacy, 'm', NOW)).toBe(true);
  // ...but a human retype is exactly the case this exists to allow.
  const legacyHuman = [{ role: 'human', content: 'applied' }];
  expect(isDuplicateMessage({ id: 'm', role: 'human', content: 'applied' }, legacyHuman, 'm', NOW)).toBe(false);
});
