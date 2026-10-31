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
import { registrationPhrase } from './scheduleCadence';
import { ScheduleRegisterControl, useScheduleState } from './ScheduleRegisterControl';
import { summarizeScheduleState } from './scheduleSummary';

export interface ScheduleCardIds {
  projectId?: string;
  cardId?: string;
}

export const ScheduleCardContext = React.createContext<ScheduleCardIds>({});

export const ScheduleHistory: React.FC<{ blockId: string }> = ({ blockId }) => {
  const { projectId, cardId } = React.useContext(ScheduleCardContext);
  const { rec, err, reload } = useScheduleState(projectId, cardId);

  if (!projectId || !cardId) return null;
  // Only the topmost schedule in a card is honoured by the fire loop; a
  // record for a different block id belongs to that one, not to this.
  if (rec?.block_id && rec.block_id !== blockId) return null;

  if (err) {
    return <div className="tc-schedule-history tc-label-dim" data-testid="schedule-history">schedule state unavailable: {err}</div>;
  }
  const summary = summarizeScheduleState(rec, Date.now());
  const reg = registrationPhrase(rec);
  const attention = summary.attention || reg.attention;
  return (
    <div
      className={`tc-schedule-history tc-label-dim${attention ? ' tc-schedule-history--attention' : ''}`}
      data-testid="schedule-history"
      title={rec?.skips?.map(s => s.reason).join('\n') || undefined}
    >
      {attention && <span aria-label="needs attention">⚠ </span>}
      <ScheduleRegisterControl projectId={projectId} cardId={cardId} rec={rec} onChanged={reload} />
      {rec?.cadence_error && <span style={{ color: '#cf1322' }}>cannot fire: {rec.cadence_error} · </span>}
      {reg.text && ' · '}
      {summary.phrases.length ? summary.phrases.join(' · ') : 'not fired yet'}
      {summary.lastRun && (
        <span className="tc-label-dim"> (run {summary.lastRun.run_id.slice(0, 8)})</span>
      )}
    </div>
  );
};
