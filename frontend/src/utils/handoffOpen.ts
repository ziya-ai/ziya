/**
 * Open the Hand off drawer from anywhere (design/conversation-handoff.md).
 *
 * The drawer is mounted once, in MUIChatHistory next to the info modal, and
 * was reachable only from the sidebar row's context menu.  The token bar's
 * pressure affordance lives in a different tree, so it asks for the drawer
 * through a document event — same pattern as composerInject.
 */

export const HANDOFF_OPEN_EVENT = 'ziya:handoff-open';

export interface HandoffOpenDetail {
  conversationId: string;
}

export function dispatchHandoffOpen(conversationId: string): void {
  if (!conversationId) return;
  document.dispatchEvent(new CustomEvent<HandoffOpenDetail>(HANDOFF_OPEN_EVENT, {
    detail: { conversationId },
  }));
}

export function subscribeHandoffOpen(handler: (conversationId: string) => void): () => void {
  const listener = (e: Event) => {
    const id = (e as CustomEvent<HandoffOpenDetail>).detail?.conversationId;
    if (id) handler(id);
  };
  document.addEventListener(HANDOFF_OPEN_EVENT, listener);
  return () => document.removeEventListener(HANDOFF_OPEN_EVENT, listener);
}
