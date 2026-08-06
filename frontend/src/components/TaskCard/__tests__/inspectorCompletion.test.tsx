/**
 * Tests for the inspector's completion footer (B5).
 *
 * The reported symptom: "there's no indication of completion in the live
 * view so I keep watching for updates while it's moved on above."  The
 * inspector had no terminal state of its own — ``runStatus`` was used
 * only to word the EMPTY-state text, so a finished run left the trace
 * looking exactly as it did mid-flight, streaming cursor included.
 *
 * Three things are pinned here:
 *   1. A completion footer appears when the run is over, and says what
 *      happened rather than only naming a status.
 *   2. It does NOT appear while the run is live — a premature "complete"
 *      is worse than none.
 *   3. The terminal set matches the controls layer, so 'partial' and
 *      'held' (previously omitted by the inspector's private list, and
 *      the two statuses most in need of the cue) are covered.
 */

import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { render, screen } from '@testing-library/react';
import { TaskRunInspector, runOverLabel } from '../TaskRunInspector';
import { isRunOver } from '../runControls';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';
import type { RunStatus } from '../../../types/task_run';

// MarkdownRenderer pulls in the full markdown/katex/mermaid stack, which
// is irrelevant here and slow; the footer is plain DOM.
jest.mock('../../MarkdownRenderer', () => ({
  MarkdownRenderer: ({ markdown }: { markdown: string }) => (
    <div data-testid="md">{markdown}</div>
  ),
}));

const EMPTY_LIVE: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

/** Live state with one finished iteration, so a body renders. */
function liveWithOutput(
  status: 'running' | 'passed' | 'failed' = 'passed',
): LiveTaskState {
  return {
    ...EMPTY_LIVE,
    text: { b1: 'some output' },
    events: [{ type: 'task_started', ts: 1 }],
    iterations: [{
      index: 0, blockId: 'b1', streamText: 'some output',
      toolCalls: [], events: [], status,
    }],
  };
}

function renderInspector(runStatus: RunStatus, live = liveWithOutput()) {
  return render(
    <TaskRunInspector live={live} defaultOpen runStatus={runStatus} />,
  );
}

// ── the footer's presence ───────────────────────────────────────────

describe('inspector completion footer', () => {
  it('is absent while the run is running', () => {
    renderInspector('running', liveWithOutput('running'));
    expect(screen.queryByText(/no further output/i)).toBeNull();
    expect(screen.queryByText(/live trace ends here/i)).toBeNull();
  });

  it('is absent while the run is queued', () => {
    renderInspector('queued', liveWithOutput('running'));
    expect(screen.queryByText(/no further output/i)).toBeNull();
  });

  it('is absent while the run is paused — held is not over', () => {
    // A paused run is mid-flight under manual control: more output is
    // coming as soon as the user steps or resumes.
    renderInspector('paused', liveWithOutput('running'));
    expect(screen.queryByText(/no further output/i)).toBeNull();
  });

  it('appears when the run is done', () => {
    renderInspector('done');
    expect(screen.getByText(/Run complete/i)).toBeInTheDocument();
    expect(screen.getByText(/live trace ends here/i)).toBeInTheDocument();
  });

  it('appears for partial, which the old private list omitted', () => {
    renderInspector('partial');
    expect(screen.getByText(/stopped after partial progress/i)).toBeInTheDocument();
  });

  it('appears for held, which the old private list omitted', () => {
    renderInspector('held');
    expect(screen.getByText(/held on an infrastructure fault/i)).toBeInTheDocument();
  });

  it('appears for failed and cancelled', () => {
    const { unmount } = renderInspector('failed');
    expect(screen.getByText(/Run failed/i)).toBeInTheDocument();
    unmount();
    renderInspector('cancelled');
    expect(screen.getByText(/Run cancelled/i)).toBeInTheDocument();
  });

  it('carries a status-specific class so colour can differ per outcome', () => {
    const { container } = renderInspector('partial');
    expect(
      container.querySelector('.tc-tile__inspector-done--partial'),
    ).not.toBeNull();
  });

  it('is announced to assistive tech as a status', () => {
    renderInspector('done');
    // role=status so a screen-reader user learns the run ended without
    // having to re-navigate the trace.
    const footer = screen.getByRole('status');
    expect(footer.textContent).toMatch(/Run complete/i);
  });
});

// ── wording ─────────────────────────────────────────────────────────

describe('runOverLabel', () => {
  it('never calls a held run a failure', () => {
    // 'held' means the infrastructure broke, not the work — calling it
    // failed would send the user to debug the wrong thing.
    const label = runOverLabel('held');
    expect(label).not.toMatch(/fail/i);
    expect(label).toMatch(/resume/i);
  });

  it('distinguishes partial from both done and failed', () => {
    const partial = runOverLabel('partial');
    expect(partial).not.toBe(runOverLabel('done'));
    expect(partial).not.toBe(runOverLabel('failed'));
    expect(partial).toMatch(/partial/i);
  });

  it('states that no more output is coming for every finished status', () => {
    for (const s of ['done', 'partial', 'failed', 'cancelled'] as RunStatus[]) {
      expect(runOverLabel(s)).toMatch(/no further output/i);
    }
  });
});

// ── one shared terminal definition ──────────────────────────────────

describe('isRunOver shares one definition with the controls layer', () => {
  it('covers every status whose executor has unwound', () => {
    for (const s of ['done', 'partial', 'failed', 'cancelled', 'held']) {
      expect(isRunOver(s)).toBe(true);
    }
  });

  it('excludes the statuses that are still live', () => {
    for (const s of ['queued', 'running', 'paused']) {
      expect(isRunOver(s)).toBe(false);
    }
  });

  it('treats null/undefined as not-over rather than throwing', () => {
    expect(isRunOver(null)).toBe(false);
    expect(isRunOver(undefined)).toBe(false);
  });
});

// ── streaming cursor honesty ─────────────────────────────────────────

describe('streaming flags stop claiming a finished run is streaming', () => {
  const SRC = fs.readFileSync(
    path.resolve(__dirname, '../TaskRunInspector.tsx'), 'utf-8',
  );

  it('no longer hardcodes isStreaming={true}', () => {
    // The flat per-block view passed a literal true, so a finished run
    // kept a blinking cursor on its last block indefinitely.
    expect(SRC).not.toMatch(/isStreaming=\{true\}/);
  });

  it('gates the per-iteration cursor on terminality too', () => {
    expect(SRC).toMatch(/isStreaming=\{it\.status === 'running' && !isTerminal\}/);
  });
});

// ── stylesheet (unreachable from jsdom, so assert on source) ─────────

describe('completion footer styling', () => {
  const CSS = fs.readFileSync(
    path.resolve(__dirname, '../task-card-inline-tile.css'), 'utf-8',
  );

  it('defines the footer rule', () => {
    expect(CSS).toMatch(/\.tc-tile__inspector-done\s*\{/);
  });

  it('gives every terminal status its own colour', () => {
    for (const s of ['done', 'partial', 'failed', 'cancelled', 'held']) {
      expect(CSS).toMatch(
        new RegExp(`\\.tc-tile__inspector-done--${s}\\s*\\{`),
      );
    }
  });

  it('separates the footer from the trace with a top border', () => {
    // It terminates the list above it, so it should read as the end of
    // that list rather than as a competing card.
    const m = CSS.match(/\.tc-tile__inspector-done\s*\{([^}]*)\}/);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/border-top/);
  });
});
