/**
 * BlockDetailPanel — the detail view for a single focused block.
 *
 * Rendered by TaskCardInlineTile in the output region when a block is
 * focused (selected in the run map).  It replaces the top-level
 * artifact view for as long as a block is focused, so "output" always
 * reflects the focused element rather than only the whole run.
 *
 * Shows three things:
 *   1. Configuration — instructions / mode / counts / scope, pure from
 *      the card's Block definition (blockConfigLines).
 *   2. Output — live streaming text while running (live.text[blockId]),
 *      the persisted artifact once terminal (block_states[id].artifact),
 *      or a specific iteration's artifact when a loop dot was focused.
 *   3. Error — block_states[id].error, if any.
 */

import React from 'react';
import { Spin } from 'antd';
import type { Block, Artifact, TemplateResolution } from '../../types/task_card';
import type { TaskRun, TaskRunBlockState } from '../../types/task_run';
import { blockOrigin, formatCompletedAt } from './partialOutcome';
import { TaskMarkdown } from './TaskMarkdown';
import { ArtifactViewer } from './ArtifactViewer';
import { stripTaskMetaTags } from './completionCheck';
import { blockConfigLines, blockEmoji, blockLabel } from './runMapModel';
import { IterationBindingsBox } from './IterationBindings';

interface Props {
  block: Block;
  status: string;
  /**
   * The run being viewed.  Needed for temporal provenance: the attempt
   * ordinal, and whether this block's output was replayed from an
   * earlier attempt rather than produced here.
   */
  run?: TaskRun | null;
  /** Persisted per-block state (artifact + error), from the REST snapshot. */
  blockState?: TaskRunBlockState;
  /** Live-streamed text accumulated for this block this session. */
  liveText?: string;
  /** When non-null, show this loop iteration's artifact instead of the
   * block-level output (set by clicking an iteration dot). */
  iterationIndex: number | null;
  iterationArtifact: Artifact | null;
  iterationLoading: boolean;
  iterationError: string | null;
  /** Needed to resolve frozen-render blob URLs for emitted artifacts. */
  projectId?: string;
  runId?: string;
  /**
   * Mid-loop resume.  Offered ONLY when an iteration is focused, and
   * placed here rather than on the dot itself: a dot is 8px and already
   * carries status + openability, whereas resuming a loop needs a
   * sentence saying WHAT will be replayed.  Absent when the run is not
   * resumable (live, or no card_snapshot), so the panel shows no
   * affordance rather than one that always errors.
   */
  onRetryIteration?: (blockId: string, index: number) => void;
  onContinueIteration?: (blockId: string, index: number) => void;
  /** Index whose resume request is in flight, for the busy state. */
  resumingIteration?: number | null;
}

const ArtifactBody: React.FC<{
  artifact: Artifact; projectId?: string; runId?: string;
}> = ({ artifact, projectId, runId }) => (
  <>
    {artifact.summary
      ? <TaskMarkdown markdown={artifact.summary} enableCodeApply={false}
          isStreaming={false} isSubRender={true} />
      : <div className="tc-detail__empty">(no summary)</div>}
    {artifact.decisions && artifact.decisions.length > 0 && (
      <ul className="tc-detail__decisions">
        {artifact.decisions.slice(0, 12).map((d, i) => <li key={i}>{d}</li>)}
      </ul>
    )}
    {/* Emitted output artifacts.  Without this the parts a task
        declared via emit_artifact are persisted, served by the API,
        and then never shown for a focused block/iteration — the same
        starved-render-path class as the run tile's earlier gap. */}
    {artifact.outputs && artifact.outputs.length > 0 && projectId && runId && (
      <div className="tc-detail__section">
        <div className="tc-detail__section-label">
          Output artifacts ({artifact.outputs.length})
        </div>
        <ArtifactViewer parts={artifact.outputs} projectId={projectId} runId={runId} />
      </div>
    )}
  </>
);

/**
 * The resolutions a focused view should show, if any.
 *
 * A focused ITERATION shows its own artifact's records.  A focused bare
 * TASK shows its block artifact's.  A focused LOOP block with no
 * iteration focused shows none: its block artifact is the last
 * iteration's, so its resolutions would present one item's values as
 * though they described the whole loop — exactly the misreading the
 * authored "({{item}})" title avoids.
 */
export function resolutionsFor(
  block: Block,
  isIter: boolean,
  iterationArtifact: Artifact | null,
  blockArtifact: Artifact | null,
): TemplateResolution[] {
  const source = isIter
    ? iterationArtifact
    : block.block_type === 'task' ? blockArtifact : null;
  return source?.template_resolutions ?? [];
}

/**
 * "Resolved for iteration #N" — what the card's template fields became
 * for this dispatch.  Rendered above the authored Configuration, which
 * collapses when this is present: the authored text is provenance, the
 * resolved text is what actually ran.
 */
const ResolutionSection: React.FC<{
  resolutions: TemplateResolution[];
  iterationIndex: number | null;
  /** Fallback from the always-retained IterationSummary. */
  summaryName?: string | null;
}> = ({ resolutions, iterationIndex, summaryName }) => {
  const multi = resolutions.length > 1;
  if (resolutions.length === 0) {
    // Past the pass-retention cap the artifact — and with it the full
    // resolution — is gone, but the summary still names the iteration.
    // Say so rather than presenting the loop as though it had no
    // templating at all.
    if (!summaryName || iterationIndex == null) return null;
    return (
      <section className="tc-detail__resolution" data-testid="detail-resolution">
        <div className="tc-detail__resolution-head">
          <strong>Resolved for iteration #{iterationIndex}</strong>
          <span>full bindings not retained for this iteration</span>
        </div>
        <div className="tc-detail__resolution-task">
          <dl className="tc-detail__config">
            <dt>Resolved name</dt>
            <dd><strong>{summaryName}</strong></dd>
          </dl>
        </div>
      </section>
    );
  }
  return (
    <section className="tc-detail__resolution" data-testid="detail-resolution">
      <div className="tc-detail__resolution-head">
        <strong>
          {iterationIndex != null
            ? `Resolved for iteration #${iterationIndex}`
            : 'Resolved for this run'}
        </strong>
        <span>Only fields containing template variables are shown</span>
      </div>
      {resolutions.map((r, i) => (
        <div key={`${r.task_block_id || 'task'}-${i}`} className="tc-detail__resolution-task">
          {multi && (
            <div className="tc-detail__section-label">
              {r.resolved_name ?? r.authored_name ?? r.task_block_id}
            </div>
          )}
          {r.resolved_name != null && (
            <dl className="tc-detail__config">
              <dt>Resolved name</dt>
              <dd><strong>{r.resolved_name}</strong></dd>
            </dl>
          )}
          <IterationBindingsBox bindings={r.bindings} />
          {r.resolved_instructions != null && (
            <details className="tc-detail__section" open>
              <summary>Instructions as sent</summary>
              <div className="tc-detail__pre">
                {r.resolved_instructions}
                {r.instructions_truncated && (
                  <span className="tc-detail__empty"> … (clipped for the record)</span>
                )}
              </div>
            </details>
          )}
        </div>
      ))}
    </section>
  );
};

export const BlockDetailPanel: React.FC<Props> = ({
  block, status, run, blockState, liveText,
  iterationIndex, iterationArtifact, iterationLoading, iterationError,
  projectId, runId,
  onRetryIteration, onContinueIteration, resumingIteration,
}) => {
  // Mirrors app/utils/resume_targets.resolve_iteration_resume's first
  // refusal.  Read off the block rather than passed in, so the gate
  // cannot be forgotten by a caller: every surface that renders this
  // panel gets the same answer from the same field the server checks.
  // Only `repeat` carries the flag — an `until` loop is serial by
  // construction, so it is never gated here.
  const isParallelLoop = !!(
    block && block.block_type === 'repeat' && block.repeat_parallel
  );

  const config = blockConfigLines(block);
  const artifact = blockState?.artifact ?? null;
  const error = blockState?.error ?? null;
  const isIter = iterationIndex != null;
  const resolutions = resolutionsFor(block, isIter, iterationArtifact, artifact);
  const summaryName = isIter
    ? (blockState?.iteration_summaries ?? [])
        .find(s => s.index === iterationIndex)?.resolved_name ?? null
    : null;
  const hasResolutions = resolutions.length > 0 || !!summaryName;
  // Temporal provenance.  Without this the panel showed state with no
  // "when", so a replayed stage from attempt 1 was indistinguishable
  // from one this attempt just produced — the "can't tell past from
  // current" confusion.
  const origin = blockOrigin(run, blockState, status);
  const finishedAt = formatCompletedAt(origin.completedAt);

  return (
    <div className="tc-detail">
      <div className="tc-detail__head">
        <span>{blockEmoji(block)}</span>
        <span className="tc-detail__title">{blockLabel(block)}</span>
        {/* Says WHERE the output came from before saying what it is: a
            reader who mistakes replayed output for fresh output draws
            the wrong conclusion from everything below. */}
        {origin.replayed ? (
          <span
            className="tc-detail__origin tc-detail__origin--replayed"
            title={
              'This stage did not run in this attempt.  Its recorded result ' +
              'was replayed from an earlier attempt so later stages see the ' +
              'same deck state they would have.'
            }
          >
            replayed from an earlier attempt
          </span>
        ) : (
          <span className="tc-detail__origin">
            attempt {origin.attempt}
          </span>
        )}
        {finishedAt && (
          <span className="tc-detail__when" title="When this stage finished">
            {finishedAt}
          </span>
        )}
        <span
          className={`tc-detail__status tc-detail__status--${origin.displayStatus}`}
        >{origin.displayStatus}</span>
      </div>

      <ResolutionSection
        resolutions={resolutions}
        iterationIndex={iterationIndex}
        summaryName={summaryName}
      />

      {/* Collapsed when a resolution is shown: the authored template is
          still the source of truth for provenance, but the resolved
          form above is what this iteration actually ran, and showing
          both open doubled the panel for no new information. */}
      <details className="tc-detail__section" open={!hasResolutions}>
        <summary>{hasResolutions ? 'Authored configuration' : 'Configuration'}</summary>
        <dl className="tc-detail__config">
          {config.map((c, i) => (
            <React.Fragment key={i}>
              <dt>{c.label}</dt>
              <dd className={c.pre ? 'tc-detail__pre' : undefined}>{c.value}</dd>
            </React.Fragment>
          ))}
        </dl>
      </details>

      <div className="tc-detail__section">
        <div className="tc-detail__section-label">
          {isIter
            ? `Output — iteration #${iterationIndex}`
              + (summaryName ? ` · ${summaryName}` : '')
            : 'Output'}
          {/* Restated on the output section itself, not only in the
              header: with a long config block open, the header scrolls
              out of view and the output would again look like this
              attempt's own work. */}
          {origin.replayed && !isIter && (
            <span className="tc-detail__origin-note">
              {' '}· from an earlier attempt, not re-run here
            </span>
          )}
        {/* Mid-loop resume.  Rendered above the output rather than below
            it: a loop's output can be long, and an action the user came
            here to take should not be reachable only by scrolling past
            the thing they were reading. */}
        {/* Parallel loops refuse mid-loop resume outright — the server
            422s with "they do not depend on each other, so there is no
            meaningful point to resume from" — because iterations receive
            previous=None and honouring a start index would silently run
            FEWER iterations than the card asks for while reporting the
            loop complete.  The refusal is correct; offering a control
            that can only ever fail is not, and on a 20-wide fan-out the
            dot strip is exactly where a user reaches first after a hold.
            Explain instead, naming the resume that does work. */}
        {isIter && isParallelLoop && (
          <div className="tc-iter-resume tc-iter-resume--unavailable">
            <div className="tc-iter-resume__note">
              This loop ran its iterations <strong>in parallel</strong>, so
              they do not build on each other and there is no meaningful
              point to resume from — re-running one would leave the others
              unrun while the loop reported complete. Use{' '}
              <strong>↻ Retry</strong> on the loop itself, which re-runs
              every iteration; stages <em>before</em> the loop still replay
              from record.
            </div>
          </div>
        )}
        {isIter && !isParallelLoop && (onRetryIteration || onContinueIteration) && (
          <div className="tc-iter-resume">
            <div className="tc-iter-resume__note">
              Iterations before the one you pick are <strong>replayed from
              record</strong>, not re-run — so the first iteration that
              executes still receives the same input it had originally.
            </div>
            <div className="tc-iter-resume__actions">
              {onRetryIteration && (
                <button
                  className="tc-iter-resume__btn"
                  disabled={resumingIteration != null}
                  title={
                    `Start a NEW run that replays iterations 0–`
                    + `${Math.max(0, iterationIndex! - 1)} and re-runs `
                    + `#${iterationIndex}. This run is kept as a record.`
                  }
                  onClick={() => onRetryIteration(block.id, iterationIndex!)}
                >
                  {resumingIteration === iterationIndex
                    ? '…' : `↻ re-run #${iterationIndex}`}
                </button>
              )}
              {onContinueIteration && (
                <button
                  className="tc-iter-resume__btn tc-iter-resume__btn--continue"
                  disabled={resumingIteration != null}
                  title={
                    `Start a NEW run that accepts #${iterationIndex}'s `
                    + `recorded result and runs #${iterationIndex! + 1} `
                    + `onward. Use after fixing the cause by hand.`
                  }
                  onClick={() => onContinueIteration(block.id, iterationIndex!)}
                >
                  ▶ continue from #{iterationIndex! + 1}
                </button>
              )}
            </div>
          </div>
        )}
        </div>
        {isIter ? (
          <>
            {iterationLoading && <Spin size="small" />}
            {iterationError && (
              <div className="tc-detail__error">Failed to load: {iterationError}</div>
            )}
            {iterationArtifact && <ArtifactBody artifact={iterationArtifact} />}
          </>
        ) : liveText ? (
          // The Inspector's Live tab already strips <progress>/
          // <self_assessment> meta tags before rendering (stripTaskMetaTags);
          // this focused-block view rendered the raw stream and either
          // showed the literal tag text or had it silently swallowed as HTML.
          <TaskMarkdown markdown={stripTaskMetaTags(liveText)} enableCodeApply={false}
            isStreaming={status === 'running'} isSubRender={true} />
        ) : artifact ? (
          <ArtifactBody artifact={artifact} />
        ) : (
          <div className="tc-detail__empty">
            {status === 'queued' ? '(not started)'
              : status === 'skipped' ? '(skipped — did not run)'
              : '(no output captured)'}
          </div>
        )}
      </div>

      {error && !isIter && (
        <div className="tc-detail__error-box">
          <div className="tc-detail__error-label">Error</div>
          <pre>{error}</pre>
        </div>
      )}
    </div>
  );
};

export default BlockDetailPanel;
