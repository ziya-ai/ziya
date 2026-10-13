/**
 * @jest-environment jsdom
 *
 * The focused-block panel says WHY a block failed, from the same artifact
 * fields task_finished / task_run_read derive their failure_reason from.
 * Before this the panel showed `failed` only as a status word and left the
 * rationale buried in the decisions list (or absent, for a verdict whose
 * rationale lives only in self_assessment).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { BlockDetailPanel } from '../BlockDetailPanel';
import type { Artifact, Block } from '../../../types/task_card';
import type { TaskRunBlockState } from '../../../types/task_run';

jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => <div data-testid="md">{markdown}</div>,
}));
jest.mock('../ArtifactViewer', () => ({ ArtifactViewer: () => null }));

const block: Block = {
  block_type: 'task', id: 't1', name: 'Lane 1B', instructions: 'scan', body: [],
} as unknown as Block;

function artifact(extra: Partial<Artifact>): Artifact {
  return {
    summary: 'did some of it', decisions: [], outputs: [],
    tokens: 0, tool_calls: 0, duration_ms: 0, created_at: 0, ...extra,
  };
}

function panel(art: Artifact, opts: { iteration?: boolean } = {}) {
  const blockState = { block_id: 't1', block_type: 'task', status: 'failed', artifact: art } as unknown as TaskRunBlockState;
  return render(
    <BlockDetailPanel
      block={block} status="failed" blockState={blockState}
      iterationIndex={opts.iteration ? 2 : null}
      iterationArtifact={opts.iteration ? art : null}
      iterationLoading={false} iterationError={null}
    />,
  );
}

describe('BlockDetailPanel failure reason', () => {
  it('shows the self-assessment verdict and rationale for a failed block', () => {
    panel(artifact({
      failed: true, signature: 'self_assessment_partial',
      self_assessment: { objective_met: 'partial', rationale: 'lane 1B skipped' },
    }));
    expect(screen.getByTestId('detail-failure-reason').textContent)
      .toContain('self_assessment objective_met=partial: lane 1B skipped');
  });

  it('shows it for a focused failed iteration too', () => {
    panel(artifact({ failed: true, signature: 'empty_summary', decisions: ['empty_summary: no prose'] }),
      { iteration: true });
    expect(screen.getByTestId('detail-failure-reason').textContent).toContain('empty_summary: no prose');
  });

  it('renders nothing for a passing artifact', () => {
    panel(artifact({ failed: false }));
    expect(screen.getByTestId('md')).toBeTruthy();
    expect(screen.queryByTestId('detail-failure-reason')).toBeNull();
  });
});
