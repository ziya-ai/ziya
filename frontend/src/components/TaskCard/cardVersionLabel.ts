/**
 * Header label for the card editor: which definition is on screen.
 *
 * ``version`` is bumped by TaskCardStorage.update only when the block
 * tree or scope changes (not on metadata edits, not on runs), and is
 * stamped into every run's card_snapshot — so it is exactly the number
 * a user needs to confirm an edit landed or to match a run tile's
 * "executed vN" against the deck.  It was returned by the API and
 * rendered nowhere.
 *
 * Pure; ``nowMs`` is injectable for tests.
 */
import type { TaskCard } from '../../types/task_card';
import { formatLastActivity } from './liveActivity';

export function cardVersionLabel(
  card: Pick<TaskCard, 'id' | 'version' | 'updated_at'>,
  nowMs: number = Date.now(),
): string {
  if (!card.id) return 'draft';
  // Cards written before the field existed load as v1 on the backend;
  // mirror that rather than printing "vundefined".
  const v = `v${card.version ?? 1}`;
  if (!card.updated_at) return v;
  // updated_at is epoch ms (BaseStorage convention), same unit
  // formatLastActivity takes.
  return `${v} · edited ${formatLastActivity(card.updated_at, nowMs).label}`;
}
