/**
 * MessageIdContext — carries the current chat message's ID down through
 * MarkdownRenderer so deeply-nested leaves (e.g. TaskCardLaunchButton)
 * can anchor actions to a specific message without having to thread the
 * ID through every token-rendering function.
 *
 * Undefined outside a Provider; callers fall back to null when absent.
 */

import { createContext, useContext } from 'react';

export const MessageIdContext = createContext<string | undefined>(undefined);

/** Returns the current message ID, or null if none is in scope. */
export function useMessageId(): string | null {
  const id = useContext(MessageIdContext);
  return id ?? null;
}
