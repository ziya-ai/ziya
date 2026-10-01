/**
 * ziya:// links — the in-app address of a project, a conversation, or one
 * message of a conversation.  Mirror of app/utils/ziya_links.py; keep the
 * two in step.
 *
 *   ziya://project/<projectId>
 *   ziya://chat/<conversationId>
 *   ziya://chat/<conversationId>/<messageIndex>    0-based, as chat_search reports
 *
 * The model writes these as ordinary markdown links (the chat-history tools
 * hand it a ready-made `link` per result).  ZiyaLinkNavigator intercepts the
 * click anywhere in the document and drives the same project-switch +
 * load-and-scroll path the sidebar search uses.
 */

export const ZIYA_LINK_SCHEME = 'ziya://';

export type ZiyaLinkTarget =
  | { kind: 'chat'; conversationId: string; messageIndex: number | null }
  | { kind: 'project'; projectId: string };

// Ids never contain a slash; a single token class rejects anything path-like.
const ID = '[A-Za-z0-9][A-Za-z0-9._-]*';
const CHAT_RE = new RegExp(`^ziya://chat/(${ID})(?:/(\\d+))?/?$`);
const PROJECT_RE = new RegExp(`^ziya://project/(${ID})/?$`);

export function isZiyaLink(href: string | null | undefined): boolean {
  return typeof href === 'string' && href.trim().startsWith(ZIYA_LINK_SCHEME);
}

/** Decode a ziya:// href; null for anything else, including malformed ones. */
export function parseZiyaLink(href: string | null | undefined): ZiyaLinkTarget | null {
  if (typeof href !== 'string') return null;
  const s = href.trim();
  const chat = CHAT_RE.exec(s);
  if (chat) {
    return {
      kind: 'chat',
      conversationId: chat[1],
      messageIndex: chat[2] !== undefined ? parseInt(chat[2], 10) : null,
    };
  }
  const project = PROJECT_RE.exec(s);
  if (project) return { kind: 'project', projectId: project[1] };
  return null;
}

export function chatLink(conversationId: string, messageIndex?: number | null): string {
  const base = `${ZIYA_LINK_SCHEME}chat/${conversationId}`;
  return messageIndex != null && messageIndex >= 0 ? `${base}/${messageIndex}` : base;
}

export function projectLink(projectId: string): string {
  return `${ZIYA_LINK_SCHEME}project/${projectId}`;
}

/**
 * The anchor a click landed on, if it is a ziya:// link.  Walks up from the
 * event target so a click on a <code> or <strong> inside the link counts.
 */
export function ziyaLinkFromEvent(target: EventTarget | null): { anchor: HTMLAnchorElement; link: ZiyaLinkTarget } | null {
  if (!(target instanceof Element)) return null;
  const anchor = target.closest('a[href]') as HTMLAnchorElement | null;
  if (!anchor) return null;
  const link = parseZiyaLink(anchor.getAttribute('href'));
  return link ? { anchor, link } : null;
}
