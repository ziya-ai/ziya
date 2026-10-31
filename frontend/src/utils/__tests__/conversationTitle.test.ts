/**
 * Title derivation for conversations (conversationTitle.ts).
 *
 * Regression target: a new conversation that starts with a utility action
 * (e.g. a model change) gets a system message inserted BEFORE the user's
 * first query.  The old logic keyed on `messages.length === 0`, so the
 * first human message no longer triggered title derivation and the
 * conversation stayed "New Conversation" forever.
 */
import {
    PLACEHOLDER_TITLES,
    shouldDeriveTitleFromMessage,
    shouldSeedTitleFromTaskCard,
    deriveTitleFromContent,
} from '../conversationTitle';

const human = (content = 'q') => ({ role: 'human', content });
const system = (content = 's') => ({ role: 'system', content });
const assistant = (content = 'a') => ({ role: 'assistant', content });

describe('shouldDeriveTitleFromMessage', () => {
    it('derives on the first human message of an empty conversation', () => {
        expect(shouldDeriveTitleFromMessage(human(), [], 'New Conversation')).toBe(true);
    });

    it('REGRESSION: derives when a model-change system message precedes the first query', () => {
        // This is the bug scenario: model changed before the user typed anything.
        const priorMessages = [system('Model changed from sonnet to opus')];
        expect(shouldDeriveTitleFromMessage(human('real question'), priorMessages, 'New Conversation')).toBe(true);
    });

    it('derives with multiple utility messages before the first query', () => {
        const priorMessages = [system(), system(), assistant('auto-notice')];
        expect(shouldDeriveTitleFromMessage(human(), priorMessages, 'New Conversation')).toBe(true);
    });

    it('does not derive when a human message already exists', () => {
        expect(shouldDeriveTitleFromMessage(human('second'), [human('first')], 'New Conversation')).toBe(false);
    });

    it('does not derive for non-human messages', () => {
        expect(shouldDeriveTitleFromMessage(system(), [], 'New Conversation')).toBe(false);
        expect(shouldDeriveTitleFromMessage(assistant(), [], 'New Conversation')).toBe(false);
    });

    it('never clobbers a seeded or user-renamed (non-placeholder) title', () => {
        // e.g. TaskCardsLibrary seeds the conversation title with the card name.
        expect(shouldDeriveTitleFromMessage(human(), [], 'My Task Card')).toBe(false);
    });

    it.each([...PLACEHOLDER_TITLES])('treats placeholder title %j as derivable', (title) => {
        expect(shouldDeriveTitleFromMessage(human(), [], title)).toBe(true);
    });

    it('treats an undefined title and undefined messages as derivable (brand-new shell)', () => {
        expect(shouldDeriveTitleFromMessage(human(), undefined, undefined)).toBe(true);
    });
});

describe('deriveTitleFromContent', () => {
    it('passes short content through unchanged', () => {
        expect(deriveTitleFromContent('short question', 50)).toBe('short question');
    });

    it('truncates long content with an ellipsis', () => {
        const long = 'x'.repeat(60);
        expect(deriveTitleFromContent(long, 50)).toBe('x'.repeat(50) + '...');
    });

    it('defaults to the stored cap, not a sidebar-width slice (design/visual-refresh.md, stage 4)', () => {
        // The old caller passed width/6 (clamped 30-80); a 120-char first line
        // then persisted as 50 chars + "...", and truncated mid-screen forever.
        const line = 'w'.repeat(120);
        expect(deriveTitleFromContent(line)).toBe(line);
        expect(deriveTitleFromContent('x'.repeat(250))).toBe('x'.repeat(200) + '...');
    });

    it('takes the first non-blank line only', () => {
        expect(deriveTitleFromContent('\n\n  why does this fail?  \n```log\nstack...')).toBe('why does this fail?');
    });

    it('does not add an ellipsis at exactly the limit', () => {
        const exact = 'y'.repeat(50);
        expect(deriveTitleFromContent(exact, 50)).toBe(exact);
    });
});

/**
 * Task-card launch title seeding (shouldSeedTitleFromTaskCard).
 *
 * Regression target: a new conversation whose FIRST content is a task-card
 * run (deck "Launch in current conversation" into a fresh chat) stayed
 * "New Conversation" forever — title derivation fires only on the first
 * human message, which such a conversation never receives.
 */
describe('shouldSeedTitleFromTaskCard', () => {
    const conv = (over: Record<string, unknown> = {}) => ({
        title: 'New Conversation', messages: [], ...over,
    });

    it('seeds a fresh placeholder conversation with no messages', () => {
        expect(shouldSeedTitleFromTaskCard(conv())).toBe(true);
    });

    it('seeds when only non-human messages precede the run (model-change notice)', () => {
        // Same semantics as shouldDeriveTitleFromMessage: utility/system
        // messages are not "dialog".
        expect(shouldSeedTitleFromTaskCard(
            conv({ messages: [system(), assistant()] }))).toBe(true);
    });

    it('does not seed once human dialog exists', () => {
        expect(shouldSeedTitleFromTaskCard(conv({ messages: [human()] }))).toBe(false);
    });

    it('never clobbers a user-set (non-placeholder) title', () => {
        expect(shouldSeedTitleFromTaskCard(conv({ title: 'My analysis' }))).toBe(false);
    });

    it('rejects shell records — a stripped message list is not "no dialog"', () => {
        expect(shouldSeedTitleFromTaskCard(conv({ _isShell: true }))).toBe(false);
    });

    it('rejects a missing conversation', () => {
        expect(shouldSeedTitleFromTaskCard(undefined)).toBe(false);
        expect(shouldSeedTitleFromTaskCard(null)).toBe(false);
    });

    it.each([...PLACEHOLDER_TITLES])('treats placeholder title %j as seedable', (title) => {
        expect(shouldSeedTitleFromTaskCard(conv({ title }))).toBe(true);
    });
});
