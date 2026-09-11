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
  /**
   * Append to whatever the composer already holds instead of replacing it.
   *
   * Default (absent/false) is replace, which is what the resume, seam-ribbon
   * and branch-pickup dispatchers want: the user asked for that text.
   *
   * SHELL_GUARD's hold path is different — it is RETURNING text the user
   * already wrote, possibly seconds later (bounded by the getChat deadline),
   * by which time they may have typed something new.  Replacing there would
   * destroy newer keystrokes to restore older ones.
   */
  preserveExisting?: boolean;
}

/** Ask the composer to load `text` for `conversationId`. */
export function dispatchComposerInject(
  conversationId: string,
  text: string,
  opts: { preserveExisting?: boolean } = {},
): void {
  document.dispatchEvent(new CustomEvent<ComposerInjectDetail>(COMPOSER_INJECT_EVENT, {
    detail: { conversationId, text, preserveExisting: !!opts.preserveExisting },
  }));
}
