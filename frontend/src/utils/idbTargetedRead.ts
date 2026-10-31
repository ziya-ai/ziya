/**
 * Read only the named conversations from IndexedDB.
 *
 * db.getConversations() is a getAll over every project's conversations with
 * full message bodies.  queueSave and the cross-tab broadcast handler used
 * it even though they only ever act on a handful of known ids, so every
 * save, and every change in any other tab, deserialized the whole store.
 *
 * Ids are de-duplicated; missing or invalid records are dropped (getConversation
 * resolves null for them), so the result holds only records that exist.
 */
import type { Conversation } from './types';

export interface ConversationReader {
    getConversation(id: string): Promise<Conversation | null>;
}

export async function readConversationsByIds(
    reader: ConversationReader,
    ids: Iterable<string>,
): Promise<Conversation[]> {
    const unique = Array.from(new Set(Array.from(ids).filter(id => typeof id === 'string' && id !== '')));
    if (unique.length === 0) return [];
    const records = await Promise.all(unique.map(id => reader.getConversation(id).catch(() => null)));
    return records.filter((c): c is Conversation => !!c);
}
