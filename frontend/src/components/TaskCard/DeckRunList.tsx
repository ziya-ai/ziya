/**
 * DeckRunList — the selected card's runs, each a link into the
 * conversation it is running in.
 *
 * Exists because the deck's only statement about run history was a
 * `run_count` integer.  A run launched into a conversation was reachable
 * only if the user remembered which one, so a study still running — or
 * one that had failed hours earlier — was invisible from the surface
 * where cards are managed.
 *
 * Deliberately a link list, not a second inspector.  The inline tile in
 * the conversation is the run's home: it carries the run map, the
 * artifacts, the attempt rail and the pause/step/resume controls.
 * Reimplementing any of that here would produce a second surface to
 * keep in sync, and the two would disagree.
 */

import React from 'react';
import { Empty, Tag, Tooltip } from 'antd';
import { ExportOutlined } from '@ant-design/icons';
import type { TaskRun } from '../../types/task_run';
import { deckStatusColor, isLiveRun, needsAttention } from './deckRunIndex';
import { formatLastActivity } from './liveActivity';

interface Props {
  runs: TaskRun[];
  /** Called with a run that has a conversation to navigate to. */
  onOpen: (run: TaskRun) => void;
}

/**
 * `created_at` is epoch MILLIseconds (TaskRunStorage stamps
 * `int(time.time() * 1000)`), while `formatLastActivity` takes epoch
 * SECONDS — it is shared with the tile's heartbeat label, which reads
 * `last_activity_at`, a float in seconds.  Converting at the call site
 * rather than "fixing" either side: both units are correct for their own
 * field, and normalizing one would break the other's caller.
 */
const ageLabel = (ms: number): string =>
  formatLastActivity(ms / 1000).label;

/**
 * One run row.  Rows for runs with no conversation are rendered but not
 * clickable, and say so — an older unbound launch has nowhere to
 * navigate to, and a click that silently does nothing reads as a bug in
 * the deck rather than as a property of that run.
 */
const RunRow: React.FC<{ run: TaskRun; onOpen: (r: TaskRun) => void }> = ({
  run, onOpen,
}) => {
  const canOpen = !!run.source_conversation_id;
  const live = isLiveRun(run.status);
  const attention = needsAttention(run.status);
  return (
    <Tooltip
      title={canOpen
        ? 'Open the conversation this run is anchored in'
        : 'This run has no conversation to open (launched without one)'}
      mouseEnterDelay={0.4}
    >
      <div
        role={canOpen ? 'button' : undefined}
        tabIndex={canOpen ? 0 : undefined}
        onClick={canOpen ? () => onOpen(run) : undefined}
        onKeyDown={canOpen
          ? (e) => { if (e.key === 'Enter' || e.key === ' ') onOpen(run); }
          : undefined}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '5px 8px',
          borderBottom: '1px solid rgba(128,128,128,0.14)',
          cursor: canOpen ? 'pointer' : 'default',
          opacity: canOpen ? 1 : 0.55,
        }}
      >
        <Tag
          color={deckStatusColor(run.status)}
          style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}
        >
          {run.status}
        </Tag>
        {(run.attempt ?? 1) > 1 && (
          <span style={{ fontSize: 10, opacity: 0.6 }}>
            attempt {run.attempt}
          </span>
        )}
        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>
          {ageLabel(run.created_at)}
        </span>
        {/* The progress note is the only thing that distinguishes two
            simultaneously-running attempts of the same card, so it takes
            the row's flexible space rather than the id. */}
        <span style={{
          flex: 1, minWidth: 0, fontSize: 11, opacity: 0.75,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {run.progress_note
            || run.error
            || run.artifact?.summary
            || run.id.slice(0, 8)}
        </span>
        {live && (
          <span style={{ fontSize: 10, color: '#1f6feb', whiteSpace: 'nowrap' }}>
            live
          </span>
        )}
        {attention && (
          <span style={{ fontSize: 10, color: '#d29922', whiteSpace: 'nowrap' }}>
            needs attention
          </span>
        )}
        {canOpen
          ? <ExportOutlined style={{ fontSize: 11, opacity: 0.6 }} />
          : <span style={{ fontSize: 10, opacity: 0.7 }}>no conversation</span>}
      </div>
    </Tooltip>
  );
};

export const DeckRunList: React.FC<Props> = ({ runs, onOpen }) => {
  if (runs.length === 0) {
    return (
      <Empty
        image={null}
        description={
          <span style={{ fontSize: 12, opacity: 0.6 }}>
            No runs yet — this card has never run
          </span>
        }
        style={{ margin: '10px 0' }}
      />
    );
  }
  return (
    <div style={{
      border: '1px solid rgba(128,128,128,0.2)', borderRadius: 4,
      maxHeight: 150, overflowY: 'auto',
    }}>
      {runs.map(r => <RunRow key={r.id} run={r} onOpen={onOpen} />)}
    </div>
  );
};

export default DeckRunList;
