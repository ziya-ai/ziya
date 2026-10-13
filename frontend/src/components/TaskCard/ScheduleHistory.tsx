/**
 * Status line under a Schedule block: what the scheduler did with the card.
 *
 * Block editors receive only {block, onChange}; the card and project they
 * belong to are known one level up in TaskCardEditor.  ScheduleCardContext
 * carries those two ids down so the Schedule editor can fetch its own
 * fire/skip record without every intermediate editor learning about them.
 * Absent context (a staged, unsaved card; a preview) renders nothing.
 */
import React from 'react';
import { taskCardApi, type ScheduleStateRecord } from '../../services/taskCardApi';
import { summarizeScheduleState } from './scheduleSummary';

export interface ScheduleCardIds {
  projectId?: string;
  cardId?: string;
}

export const ScheduleCardContext = React.createContext<ScheduleCardIds>({});

const REFRESH_MS = 30_000;

export const ScheduleHistory: React.FC<{ blockId: string }> = ({ blockId }) => {
  const { projectId, cardId } = React.useContext(ScheduleCardContext);
  const [rec, setRec] = React.useState<ScheduleStateRecord | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!projectId || !cardId || cardId === 'draft') { setRec(null); return; }
    let cancelled = false;
    const load = async () => {
      try {
        const r = await taskCardApi.scheduleState(projectId, cardId);
        if (!cancelled) { setRec(r); setErr(null); }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    void load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [projectId, cardId]);

  if (!projectId || !cardId) return null;
  // Only the topmost schedule in a card is honoured by the fire loop; a
  // record for a different block id belongs to that one, not to this.
  if (rec?.block_id && rec.block_id !== blockId) return null;

  const summary = summarizeScheduleState(rec, Date.now());
  if (err) {
    return <div className="tc-schedule-history tc-label-dim" data-testid="schedule-history">schedule state unavailable: {err}</div>;
  }
  if (!summary.phrases.length) {
    return <div className="tc-schedule-history tc-label-dim" data-testid="schedule-history">not fired yet</div>;
  }
  return (
    <div
      className={`tc-schedule-history tc-label-dim${summary.attention ? ' tc-schedule-history--attention' : ''}`}
      data-testid="schedule-history"
      title={rec?.skips?.map(s => s.reason).join('\n') || undefined}
    >
      {summary.attention && <span aria-label="needs attention">⚠ </span>}
      {summary.phrases.join(' · ')}
      {summary.lastRun && (
        <span className="tc-label-dim"> (run {summary.lastRun.run_id.slice(0, 8)})</span>
      )}
    </div>
  );
};
