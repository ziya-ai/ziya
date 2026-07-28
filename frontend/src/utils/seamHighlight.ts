/**
 * Seam highlight — module-level store for the "seam ribbon" marker.
 *
 * When the Backlog Browser jumps to or resumes a parked bead, it records the
 * seam here; Conversation.tsx subscribes and renders an inline SeamRibbon
 * directly below the seam message, so the user can SEE where the thread was
 * parked and act on it in place (resume / branch / dismiss).
 *
 * Module-level (not React context) because the setter lives in a sidebar tab
 * and the consumer deep inside the chat render tree — an event + snapshot
 * pair is the lightest coupling.  Only one seam is highlighted at a time;
 * setting a new one replaces the old.  The marker survives conversation
 * switches (it simply doesn't render elsewhere) until dismissed or replaced.
 */

export interface SeamHighlight {
  conversationId: string;
  /** Message index the ribbon renders after (bead.message_index - 1). */
  seamIndex: number;
  beadId: string;
  /** The bead's content — "what thread was parked here". */
  label: string;
  contextHint?: string | null;
  canBranch: boolean;
  /**
   * 'jump' = the bead is still parked (ribbon offers Resume + Branch);
   * 'resumed' = the bead was just made active (ribbon confirms + Branch).
   */
  mode: 'jump' | 'resumed';
}

export const SEAM_HIGHLIGHT_EVENT = 'ziya:seam-highlight';

let current: SeamHighlight | null = null;

export function setSeamHighlight(next: SeamHighlight | null): void {
  current = next;
  window.dispatchEvent(new CustomEvent(SEAM_HIGHLIGHT_EVENT));
}

export function getSeamHighlight(): SeamHighlight | null {
  return current;
}

export function clearSeamHighlight(): void {
  setSeamHighlight(null);
}
