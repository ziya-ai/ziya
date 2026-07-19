/**
 * Conversation flag catalog.
 *
 * Flags are a lightweight, purely client-side triage mechanism for the
 * conversation list: a set of toggleable label attributes (multi-select)
 * plus a single color flag (like a colored label/tag).  Storage lives on
 * the Conversation record (see Conversation.flags / Conversation.flagColor
 * in utils/types.ts). Filtering and a dedicated "flagged" display surface
 * are intentionally deferred — this slice only adds the ability to set and
 * see flags in the conversation list row + its "..." menu.
 */

export interface ConversationFlagLabelDef {
  id: string;
  label: string;
  emoji: string;
}

// Multi-select label flags. Add more here as needed — the menu and row
// indicators render this list generically.
export const CONVERSATION_FLAG_LABELS: ConversationFlagLabelDef[] = [
  { id: 'awaiting-verification', label: 'Awaiting Human Verification', emoji: '🔍' },
  { id: 'priority', label: 'Priority', emoji: '⭐' },
  { id: 'come-back-later', label: 'Come Back Later', emoji: '↩️' },
  { id: 'deferred', label: 'Deferred', emoji: '⏸️' },
  { id: 'blocked', label: 'Blocked', emoji: '🚧' },
  { id: 'needs-review', label: 'Needs Review', emoji: '👀' },
  { id: 'follow-up', label: 'Follow Up', emoji: '📌' },
  { id: 'question', label: 'Has Open Question', emoji: '❓' },
];

export interface ConversationFlagColorDef {
  id: string;
  label: string;
  hex: string;
}

// Single-select color flag (mutually exclusive), rendered as a small dot.
export const CONVERSATION_FLAG_COLORS: ConversationFlagColorDef[] = [
  { id: 'red', label: 'Red', hex: '#f5222d' },
  { id: 'orange', label: 'Orange', hex: '#fa8c16' },
  { id: 'yellow', label: 'Yellow', hex: '#fadb14' },
  { id: 'green', label: 'Green', hex: '#52c41a' },
  { id: 'blue', label: 'Blue', hex: '#1890ff' },
  { id: 'purple', label: 'Purple', hex: '#722ed1' },
];

export function getFlagLabelDef(id: string): ConversationFlagLabelDef | undefined {
  return CONVERSATION_FLAG_LABELS.find(f => f.id === id);
}

export function getFlagColorDef(id: string | null | undefined): ConversationFlagColorDef | undefined {
  if (!id) return undefined;
  return CONVERSATION_FLAG_COLORS.find(c => c.id === id);
}
