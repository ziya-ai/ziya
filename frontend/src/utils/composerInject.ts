/**
 * Composer injection — the channel external surfaces use to drop text into
 * the chat composer (focused, never auto-sent).
 *
 * Dispatched by: Backlog Browser (resume pickup message), the seam ribbon,
 * and useBranchFromBead (branch pickup message).  Consumed by
 * SendChatContainer, which applies it immediately when the target
 * conversation is current, or stashes it and applies it right after the
 * conversation switch completes (post draft-restore, so the restore can't
 * clobber it).
 */

export const COMPOSER_INJECT_EVENT = 'ziya:composer-inject';

export interface ComposerInjectDetail {
  conversationId: string;
  text: string;
}

/** Ask the composer to load `text` for `conversationId`. */
export function dispatchComposerInject(conversationId: string, text: string): void {
  document.dispatchEvent(new CustomEvent<ComposerInjectDetail>(COMPOSER_INJECT_EVENT, {
    detail: { conversationId, text },
  }));
}
