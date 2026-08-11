/**
 * RunRecoveryBanner — the tile-level "you do not have to start over".
 *
 * Why this exists as its own surface rather than relying on the run
 * map's per-row buttons: those were reported as undiscoverable, and the
 * reasons are structural, not cosmetic.
 *
 *   1. The map is suppressed for a single-node card (`rows.length <= 1`),
 *      so on the commonest card shape the resume buttons have NO host
 *      and the only control on screen is Restart.
 *   2. Where the map does render, the buttons are 10px, partially
 *      transparent until row-hover, and repeated identically on every
 *      row — so the recovery path is spread across N rows with nothing
 *      marking which one the user actually wants.
 *   3. The header offers only Restart/Rerun, which relaunches the card
 *      from scratch. A user who cannot find the resume path reasonably
 *      concludes that discarding all prior work is the only option.
 *
 * So this names the ONE natural target, states plainly what is kept,
 * and contrasts itself with Restart — the choice being made is
 * "preserve progress" vs "discard it", and that has to be legible
 * without hovering anything.
 */

import React from 'react';
import type { TaskRun } from '../../types/task_run';
import type { RecoveryTarget } from './recoveryTarget';
import { progressCounts } from './partialOutcome';

interface Props {
  run: TaskRun;
  target: RecoveryTarget;
  /** Human label for the target block; falls back to its id. */
  targetLabel: string;
  /** Re-execute the target block. */
  onRetry: (blockId: string) => void;
  /** Accept the target's recorded outcome; start at the NEXT block. */
  onContinue?: (blockId: string) => void;
  /** True while either request is in flight. */
  busy: boolean;
}

export const RunRecoveryBanner: React.FC<Props> = ({
  run, target, targetLabel, onRetry, onContinue, busy,
}) => {
  const p = progressCounts(run);
  // Only claim preserved work when there is some.  A run that failed at
  // its first block has nothing to replay, and saying otherwise would
  // oversell what resuming buys.
  const kept = p.completed > 0 || p.passedIterations > 0;
  const keptParts: string[] = [];
  if (p.completed > 0) {
    keptParts.push(`${p.completed} completed stage${p.completed === 1 ? '' : 's'}`);
  }
  if (p.passedIterations > 0) {
    const n = p.passedIterations;
    keptParts.push(`${n} passed loop iteration${n === 1 ? '' : 's'}`);
  }

  return (
    <div className="tc-recover" role="region" aria-label="Resume this run">
      <div className="tc-recover__head">
        {target.reason === 'held'
          ? 'This run stopped on an infrastructure fault — it can be continued'
          : 'This run can be continued without starting over'}
      </div>

      <div className="tc-recover__note">
        {kept ? (
          <>
            <strong>{keptParts.join(' and ')}</strong> will be{' '}
            <strong>replayed from record</strong>, not re-run. Execution
            resumes at <strong>{targetLabel}</strong>.
          </>
        ) : (
          <>
            Execution resumes at <strong>{targetLabel}</strong>. This run had
            no completed stages, so there is little to replay — but the
            original run is kept as a record either way.
          </>
        )}
      </div>

      <div className="tc-recover__actions">
        <button
          className="tc-recover__btn"
          disabled={busy}
          onClick={() => onRetry(target.blockId)}
          title={
            'Start a new run that replays the earlier stages\u2019 recorded '
            + 'results, then re-runs this block.'
          }
        >
          {busy ? '…' : `↻ Retry ${targetLabel}`}
        </button>
        {onContinue && (
          <button
            className="tc-recover__btn tc-recover__btn--continue"
            disabled={busy}
            onClick={() => onContinue(target.blockId)}
            title={
              'Accept this block\u2019s recorded result and start at the NEXT '
              + 'block. Use this after fixing the cause by hand.'
            }
          >
            ▶ Continue past it
          </button>
        )}
      </div>

      {/* States the contrast explicitly.  Without it, Restart is the
          loudest control on the tile and reads as the intended action —
          which is how a user ends up discarding work that was
          recoverable. */}
      <div className="tc-recover__alt">
        <strong>Restart</strong> in the header does something different: it
        relaunches the whole card from the beginning and keeps none of this
        run’s progress.
      </div>
    </div>
  );
};

export default RunRecoveryBanner;
