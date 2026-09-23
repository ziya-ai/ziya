/**
 * @jest-environment jsdom
 *
 * "Resolved for iteration #N" in the focused-block panel.
 *
 * The card says "Wave 3 ({{item}})"; a focused iteration's artifact
 * carries a TemplateResolution saying what that became.  Pinned here:
 *
 *   1. A focused iteration with resolutions shows the section — resolved
 *      name, the bindings box (shared with the inspector), instructions
 *      as sent — and the authored Configuration collapses beneath it.
 *   2. No resolutions → no section, Configuration stays open (the panel
 *      is unchanged for every untemplated card).
 *   3. A focused bare TASK shows its block artifact's resolutions.
 *   4. A focused LOOP block (no iteration) never shows resolutions, even
 *      though its block artifact — the last iteration's — carries them:
 *      one item's values must not stand in for the whole loop.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { BlockDetailPanel, resolutionsFor } from '../BlockDetailPanel';
import type { Artifact, Block, TemplateResolution } from '../../../types/task_card';
import type { TaskRun, TaskRunBlockState } from '../../../types/task_run';

jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => <div data-testid="md">{markdown}</div>,
}));
jest.mock('../ArtifactViewer', () => ({ ArtifactViewer: () => null }));

const RUN = {
  id: 'run-1', card_id: 'c1', status: 'done', block_states: {},
  cancel_requested: false, pause_requested: false,
  total_tokens: 0, total_tool_calls: 0, created_at: 0, updated_at: 0,
} as unknown as TaskRun;

const resolution: TemplateResolution = {
  task_block_id: 't1',
  authored_name: 'Wave 3 ({{item}})',
  resolved_name: 'Wave 3 (graphviz)',
  bindings: [
    { placeholder: 'item', value: 'graphviz', resolved: true },
    { placeholder: 'previous.summary', value: '', resolved: true },
  ],
  resolved_instructions: 'Engine under test: graphviz. Same contract as wave 1.',
};

function artifact(withResolution: boolean): Artifact {
  return {
    summary: 'iteration output', decisions: [], outputs: [],
    tokens: 0, tool_calls: 0, duration_ms: 0, created_at: 0,
    ...(withResolution ? { template_resolutions: [resolution] } : {}),
  };
}

const taskBlock: Block = {
  block_type: 'task', id: 't1', name: 'Wave 3 ({{item}})',
  instructions: 'Engine under test: {{item}}. Same contract as wave 1.', body: [],
} as unknown as Block;

const loopBlock: Block = {
  block_type: 'repeat', id: 'loop-1', name: 'Waves', repeat_mode: 'for_each',
  repeat_for_each_source: '["graphviz","mermaid"]', body: [taskBlock],
} as unknown as Block;

function renderPanel(opts: {
  block: Block; iterationIndex: number | null;
  iterationArtifact?: Artifact | null; blockArtifact?: Artifact | null;
}) {
  const blockState = {
    block_id: opts.block.id, block_type: opts.block.block_type, status: 'done',
    artifact: opts.blockArtifact ?? null, iteration_summaries: [],
  } as unknown as TaskRunBlockState;
  return render(
    <BlockDetailPanel
      block={opts.block} status="done" run={RUN} blockState={blockState}
      iterationIndex={opts.iterationIndex}
      iterationArtifact={opts.iterationArtifact ?? null}
      iterationLoading={false} iterationError={null}
    />,
  );
}

describe('focused iteration with a template resolution', () => {
  it('shows the resolved section with name, bindings and instructions as sent', () => {
    const { container } = renderPanel({
      block: loopBlock, iterationIndex: 0, iterationArtifact: artifact(true),
    });
    const section = screen.getByTestId('detail-resolution');
    expect(section).toHaveTextContent('Resolved for iteration #0');
    expect(section).toHaveTextContent('Wave 3 (graphviz)');
    // The bindings box is the same component the inspector uses.
    expect(section.querySelector("[data-testid='iter-bindings']")).not.toBeNull();
    expect(section.querySelector("tr[data-placeholder='item']")).toHaveTextContent('graphviz');
    expect(section).toHaveTextContent('Engine under test: graphviz.');
    // Authored configuration is still there, collapsed and relabelled.
    const config = Array.from(container.querySelectorAll('details.tc-detail__section'))
      .find(d => d.querySelector('summary')?.textContent?.includes('configuration')) as HTMLDetailsElement;
    expect(config).toBeDefined();
    expect(config.textContent).toContain('Authored configuration');
    expect(config.open).toBe(false);
    // The focused block is the LOOP, so its authored configuration is the
    // loop's (mode, source) — the body task's template text is reached
    // through the resolution section above, which shows it resolved.
    expect(config.textContent).toContain('for_each');
    expect(config.textContent).toContain('graphviz');
  });

  it('is absent, with Configuration open, when the artifact has no resolutions', () => {
    const { container } = renderPanel({
      block: loopBlock, iterationIndex: 0, iterationArtifact: artifact(false),
    });
    expect(screen.queryByTestId('detail-resolution')).toBeNull();
    const config = container.querySelector('details.tc-detail__section') as HTMLDetailsElement;
    expect(config.querySelector('summary')?.textContent).toBe('Configuration');
    expect(config.open).toBe(true);
  });
});

describe('block-level resolutions', () => {
  it('a focused bare task shows its own artifact\u2019s resolution', () => {
    renderPanel({ block: taskBlock, iterationIndex: null, blockArtifact: artifact(true) });
    expect(screen.getByTestId('detail-resolution')).toHaveTextContent('Resolved for this run');
  });

  it('a focused loop block never shows the last iteration\u2019s values as its own', () => {
    renderPanel({ block: loopBlock, iterationIndex: null, blockArtifact: artifact(true) });
    expect(screen.queryByTestId('detail-resolution')).toBeNull();
    expect(resolutionsFor(loopBlock, false, null, artifact(true))).toEqual([]);
    expect(resolutionsFor(loopBlock, true, artifact(true), null)).toHaveLength(1);
  });
});

// ── Past the pass-retention cap: the summary is all that is left ─────

import { buildDots } from '../runMapModel';
import type { IterationSummary } from '../../../types/task_run';

const summary = (index: number, over: Partial<IterationSummary> = {}): IterationSummary => ({
  index, status: 'passed', duration_ms: 1, tokens: 1, has_artifact: false, ...over,
});

describe('iteration name survives the retention cap', () => {
  it('panel shows a name-only resolution header from the summary when the artifact is gone', () => {
    const blockState = {
      block_id: loopBlock.id, block_type: 'repeat', status: 'done', artifact: null,
      iteration_summaries: [
        summary(56, { resolved_name: 'Wave 3 (eng-56)' }),
        summary(57, { resolved_name: 'Wave 3 (eng-57)' }),
      ],
    } as unknown as TaskRunBlockState;
    const { container } = render(
      <BlockDetailPanel
        block={loopBlock} status="done" run={RUN} blockState={blockState}
        iterationIndex={57} iterationArtifact={null}
        iterationLoading={false} iterationError={null}
      />,
    );
    const section = screen.getByTestId('detail-resolution');
    expect(section).toHaveTextContent('Resolved for iteration #57');
    expect(section).toHaveTextContent('Wave 3 (eng-57)');
    expect(section).not.toHaveTextContent('eng-56');
    expect(section).toHaveTextContent(/not retained/i);
    // No bindings table — there is none to show, and an empty box would
    // imply the task had no placeholders.
    expect(section.querySelector("[data-testid='iter-bindings']")).toBeNull();
    // The name also labels the output section, and Configuration folds.
    expect(container.textContent).toContain('Output — iteration #57 · Wave 3 (eng-57)');
    const config = container.querySelector('details.tc-detail__section') as HTMLDetailsElement;
    expect(config.open).toBe(false);
  });

  it('panel shows nothing extra when the summary has no resolved name', () => {
    const blockState = {
      block_id: loopBlock.id, block_type: 'repeat', status: 'done', artifact: null,
      iteration_summaries: [summary(57)],
    } as unknown as TaskRunBlockState;
    render(
      <BlockDetailPanel
        block={loopBlock} status="done" run={RUN} blockState={blockState}
        iterationIndex={57} iterationArtifact={null}
        iterationLoading={false} iterationError={null}
      />,
    );
    expect(screen.queryByTestId('detail-resolution')).toBeNull();
    expect(screen.getByText('Output — iteration #57')).toBeInTheDocument();
  });

  it('dot model carries the resolved name for the tooltip', () => {
    const dots = buildDots([
      summary(0, { resolved_name: 'Wave 3 (graphviz)', has_artifact: true }),
      summary(1),
    ], false);
    expect(dots.dots.find(d => d.index === 0)?.label).toBe('Wave 3 (graphviz)');
    expect(dots.dots.find(d => d.index === 1)?.label).toBeUndefined();
  });
});
