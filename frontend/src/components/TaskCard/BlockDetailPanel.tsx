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
import type { Block, Artifact } from '../../types/task_card';
import type { TaskRunBlockState } from '../../types/task_run';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { blockConfigLines, blockEmoji, blockLabel } from './runMapModel';

interface Props {
  block: Block;
  status: string;
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
}

const ArtifactBody: React.FC<{ artifact: Artifact }> = ({ artifact }) => (
  <>
    {artifact.summary
      ? <MarkdownRenderer markdown={artifact.summary} enableCodeApply={false}
          isStreaming={false} isSubRender={true} />
      : <div className="tc-detail__empty">(no summary)</div>}
    {artifact.decisions && artifact.decisions.length > 0 && (
      <ul className="tc-detail__decisions">
        {artifact.decisions.slice(0, 12).map((d, i) => <li key={i}>{d}</li>)}
      </ul>
    )}
  </>
);

export const BlockDetailPanel: React.FC<Props> = ({
  block, status, blockState, liveText,
  iterationIndex, iterationArtifact, iterationLoading, iterationError,
}) => {
  const config = blockConfigLines(block);
  const artifact = blockState?.artifact ?? null;
  const error = blockState?.error ?? null;
  const isIter = iterationIndex != null;

  return (
    <div className="tc-detail">
      <div className="tc-detail__head">
        <span>{blockEmoji(block)}</span>
        <span className="tc-detail__title">{blockLabel(block)}</span>
        <span className={`tc-detail__status tc-detail__status--${status}`}>{status}</span>
      </div>

      <details className="tc-detail__section" open>
        <summary>Configuration</summary>
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
          {isIter ? `Output — iteration #${iterationIndex}` : 'Output'}
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
          <MarkdownRenderer markdown={liveText} enableCodeApply={false}
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
