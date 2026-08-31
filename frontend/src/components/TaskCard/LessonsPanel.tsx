/**
 * LessonsPanel — a card's self-improvement history, rendered from the
 * project's lesson ledger via GET /task-cards/{id}/lessons.
 *
 * Shows every judge verdict newest-first; applied revisions expand to
 * a before/after view of each changed field and carry a one-click
 * Revert.  Revert posts the record's (patch_hash, block_id) to the
 * revert endpoint, which writes the recorded pre-image back through
 * the same guarded text-patch path the improvement used — so a revert
 * can no more touch privilege than the revision could.
 *
 * Mounted in the deck library's editor pane (not inside
 * TaskCardEditor): the library owns loadCard(), which is what must
 * re-run after a revert so the editor shows the restored text.
 *
 * Collapsed <details> by default and fetch-on-expand: most cards have
 * no lessons, and the deck badge (🌱 n) is what tells the user this
 * panel is worth opening.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Button, Tag, message } from 'antd';
import {
  taskCardApi, type CardLessons, type LessonRecord,
} from '../../services/taskCardApi';
import './task-card-editor.css';

interface Props {
  projectId: string;
  cardId: string | null;
  /** Ledger record count from the deck's one-request summary; the
   *  panel renders nothing when 0 so a lesson-less card carries no
   *  extra chrome. */
  lessonCount: number;
  /** Called after a successful revert so the owner reloads the card
   *  (the live definition's text just changed on disk). */
  onReverted: () => void;
}

const VERDICT_COLOR: Record<string, string> = {
  revise: 'cyan', accept: 'green', stop: 'orange',
};

function formatWhen(ts?: number): string {
  if (!ts) return '';
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return '';
  }
}

/** Stacked before/after for one applied revision's changed fields. */
const PatchDiff: React.FC<{ rec: LessonRecord }> = ({ rec }) => {
  const patch = rec.patch ?? {};
  const pre = rec.pre_image ?? {};
  return (
    <div className="tc-lesson-diff">
      {Object.entries(patch).map(([bid, fields]) => (
        Object.entries(fields).map(([fname, after]) => {
          const before = pre[bid]?.[fname] ?? '';
          return (
            <div key={`${bid}.${fname}`} className="tc-lesson-diff-field">
              <div className="tc-lesson-diff-label">
                {fname} · block {bid.slice(0, 8)}
              </div>
              {before ? (
                <pre className="tc-lesson-diff-before">{before}</pre>
              ) : (
                <div className="tc-lesson-diff-empty">(was empty)</div>
              )}
              <pre className="tc-lesson-diff-after">{after}</pre>
            </div>
          );
        })
      ))}
    </div>
  );
};

export const LessonsPanel: React.FC<Props> = ({
  projectId, cardId, lessonCount, onReverted,
}) => {
  const [data, setData] = useState<CardLessons | null>(null);
  const [loading, setLoading] = useState(false);
  const [reverting, setReverting] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    if (!projectId || !cardId) return;
    setLoading(true);
    try {
      setData(await taskCardApi.lessons(projectId, cardId));
    } catch (e) {
      message.error(`Failed to load lessons: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [projectId, cardId]);

  // Refetch on card switch while open; reset stale data on switch.
  useEffect(() => {
    setData(null);
    if (open) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardId]);

  const handleRevert = useCallback(async (rec: LessonRecord) => {
    if (!projectId || !cardId || !rec.patch_hash || !rec.block_id) return;
    setReverting(rec.patch_hash);
    try {
      await taskCardApi.revertLesson(projectId, cardId, {
        patch_hash: rec.patch_hash, block_id: rec.block_id,
      });
      message.success('Revision reverted — card text restored');
      onReverted();
      await load();
    } catch (e) {
      // 409 = a record predating pre-image capture; the server's
      // detail says to edit the text directly.  Surface it verbatim.
      message.error(String(e instanceof Error ? e.message : e));
    } finally {
      setReverting(null);
    }
  }, [projectId, cardId, onReverted, load]);

  if (!cardId || lessonCount === 0) return null;

  return (
    <details
      className="tc-lessons-panel"
      open={open}
      onToggle={e => {
        const isOpen = (e.target as HTMLDetailsElement).open;
        setOpen(isOpen);
        if (isOpen && !data && !loading) void load();
      }}
    >
      <summary className="tc-lessons-summary">
        <span className="tc-improve-emoji">🌱</span>
        Lessons learned
        <Tag color="cyan" style={{ marginInlineStart: 6, fontSize: 10, lineHeight: '16px' }}>
          {lessonCount}
        </Tag>
        {data && data.edits_applied > 0 && (
          <span className="tc-lessons-edits-note">
            {data.edits_applied} revision{data.edits_applied === 1 ? '' : 's'} applied to this card
          </span>
        )}
      </summary>
      <div className="tc-lessons-body">
        {loading && <div className="tc-lessons-loading">Loading…</div>}
        {data?.lessons.map((rec, i) => (
          <div
            key={`${rec.patch_hash ?? rec.run_id ?? i}-${rec.revision ?? i}`}
            className="tc-lesson-row"
          >
            <div className="tc-lesson-head">
              <Tag color={VERDICT_COLOR[rec.verdict ?? ''] ?? 'default'}
                   style={{ fontSize: 10, lineHeight: '16px' }}>
                {rec.verdict ?? '?'}
              </Tag>
              {rec.applied && (
                <Tag color="cyan" style={{ fontSize: 10, lineHeight: '16px' }}>
                  revision applied
                </Tag>
              )}
              <span className="tc-lesson-when">{formatWhen(rec.ts)}</span>
              {rec.applied && rec.patch_hash && rec.block_id && (
                <Button
                  size="small"
                  danger
                  loading={reverting === rec.patch_hash}
                  disabled={!rec.pre_image}
                  title={rec.pre_image
                    ? 'Restore the text this revision replaced (permissions are untouched either way)'
                    : 'This revision predates pre-image capture and cannot be auto-reverted'}
                  onClick={() => void handleRevert(rec)}
                >
                  Revert
                </Button>
              )}
            </div>
            {(rec.lesson || rec.rationale) && (
              <div className="tc-lesson-text">
                {rec.lesson || rec.rationale}
              </div>
            )}
            {rec.applied && rec.patch && <PatchDiff rec={rec} />}
          </div>
        ))}
        {data && data.lessons.length === 0 && (
          <div className="tc-lessons-loading">No records.</div>
        )}
      </div>
    </details>
  );
};
