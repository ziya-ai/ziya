/**
 * Wiring for task-card launch title seeding.
 *
 * The defect: a new conversation whose first content is a task-card run
 * (deck "Launch in current conversation" into a fresh chat) stayed
 * "New Conversation" forever — title derivation fires only on the first
 * HUMAN message (utils/conversationTitle.ts), which such a conversation
 * never receives.
 *
 * Static source assertions, following runStatusIndexWiring.test.ts: the
 * guard (shouldSeedTitleFromTaskCard) has its own unit tests in
 * utils/__tests__/conversationTitle.test.ts; what can silently break is
 * the JOIN — the library not calling the guard, seeding a constant
 * instead of the card name, or renaming through a path the next periodic
 * sync reverts.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');

const LIBRARY = () => read('components/TaskCard/TaskCardsLibrary.tsx');

/** The launchToChat callback body (up to its dependency array). */
const launchToChat = (): string => {
  const m = LIBRARY().match(
    /const launchToChat = useCallback\([\s\S]*?\n  \}, \[[^\]]*\]\);/,
  );
  expect(m).not.toBeNull();
  return m![0];
};

describe('deck launch seeds a placeholder conversation title from the card', () => {
  it('launchToChat consults the shared seed guard', () => {
    // Without this join the guard's unit tests pass while the feature
    // cannot work — the exact "two correct halves that never meet" shape.
    expect(launchToChat()).toMatch(/shouldSeedTitleFromTaskCard/);
  });

  it('renames through the unified metadata mutation path (survives sync)', () => {
    // A setConversations-only rename is reverted by the next periodic
    // sync whenever the server record's _version is newer — the exact
    // bug mutateConversationMeta exists to close (see its file header).
    expect(launchToChat()).toMatch(/mutateConversationMeta/);
  });

  it('seeds the CARD name, not a constant', () => {
    expect(launchToChat()).toMatch(/title:\s*draft\.name/);
  });

  it('the seed guard is imported from the shared title module', () => {
    expect(LIBRARY()).toMatch(
      /import \{ shouldSeedTitleFromTaskCard \} from '\.\.\/\.\.\/utils\/conversationTitle'/,
    );
  });

  it('the "launch in new conversation" path still seeds via startNewChat', () => {
    // Positive control for the pre-existing seeding path: both launch
    // buttons must name the chat after the card, each through its own
    // mechanism (seed-at-create vs rename-after-bind).
    expect(LIBRARY()).toMatch(/startNewChat\(null,\s*draft\.name\)/);
  });
});
