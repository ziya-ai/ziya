/**
 * Tests for ArtifactViewer — the run-tile output-artifact surface.
 *
 * The viewer's contract: it renders whatever shape the emitting agent
 * produced, choosing a layout from group shape (see
 * frontend/src/utils/artifactGroups.ts) and never dropping a part.
 * Frozen images are fetched from the run's artifact-serving route.
 *
 * These tests deliberately assert on *behaviour visible to the user*
 * (part content is reachable, images point at the blob route, the list
 * fallback is available) rather than on class names, so a styling
 * refactor doesn't produce false failures.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { ArtifactViewer } from '../ArtifactViewer';
import type { ArtifactPart } from '../../../types/task_card';

// TaskMarkdown delegates to MarkdownRenderer, which pulls in the whole
// markdown/plugin stack; the viewer
// only needs it to display text, so render text verbatim in tests.
jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => <div>{markdown}</div>,
}));

const PROJECT = 'proj-1';
const RUN = 'run-1';

function textPart(over: Partial<ArtifactPart> = {}): ArtifactPart {
  return { part_type: 'text', text: 'some prose', name: 'note', ...over } as ArtifactPart;
}
function imagePart(over: Partial<ArtifactPart> = {}): ArtifactPart {
  return {
    part_type: 'file',
    file_uri: '/home/u/.ziya/projects/p/task_runs/run-1/artifacts/chart.png',
    media_type: 'image/png',
    name: 'chart',
    ...over,
  } as ArtifactPart;
}
function dataPart(over: Partial<ArtifactPart> = {}): ArtifactPart {
  return {
    part_type: 'data',
    data: { answer: 42 },
    name: 'counters',
    ...over,
  } as ArtifactPart;
}

function renderViewer(parts: ArtifactPart[]) {
  return render(
    <ArtifactViewer parts={parts} projectId={PROJECT} runId={RUN} />,
  );
}

describe('ArtifactViewer — empty and degenerate input', () => {
  it('renders nothing for an empty parts array', () => {
    const { container } = renderViewer([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when parts is null', () => {
    const { container } = render(
      <ArtifactViewer parts={null as any} projectId={PROJECT} runId={RUN} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a part with no usable content without crashing', () => {
    // A malformed part (text type, no text) must not take the viewer down.
    const { container } = renderViewer([
      { part_type: 'text', name: 'empty' } as ArtifactPart,
    ]);
    expect(container).not.toBeEmptyDOMElement();
  });
});

describe('ArtifactViewer — part content is reachable', () => {
  it('shows text part content', () => {
    renderViewer([textPart({ text: 'the rationale' })]);
    expect(screen.getByText('the rationale')).toBeInTheDocument();
  });

  it('shows a data part as JSON', () => {
    renderViewer([dataPart({ data: { hits: 7 } })]);
    // JSON.stringify output, whitespace-insensitive match.
    expect(screen.getByText(/"hits"/)).toBeInTheDocument();
    expect(screen.getByText(/7/)).toBeInTheDocument();
  });

  it('renders an image part as an <img> pointing at the blob route', () => {
    renderViewer([imagePart()]);
    const img = screen.getByRole('img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe(
      `/api/v1/projects/${PROJECT}/task-runs/${RUN}/artifacts/chart.png`,
    );
  });

  it('does not inline an SVG file part (mirrors server policy)', () => {
    renderViewer([
      imagePart({ media_type: 'image/svg+xml', file_uri: '/x/y/diagram.svg' }),
    ]);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    // It should still be reachable as a link/download.
    expect(screen.getByText(/diagram\.svg/)).toBeInTheDocument();
  });

  it('shows a non-image file part by name', () => {
    renderViewer([
      imagePart({ media_type: 'text/markdown', file_uri: '/x/y/ledger.md' }),
    ]);
    expect(screen.getByText(/ledger\.md/)).toBeInTheDocument();
  });
});

describe('ArtifactViewer — group labels and headers', () => {
  it('shows the group name for a named group', () => {
    renderViewer([
      textPart({ group: 'issue-5', label: 'before' }),
      textPart({ group: 'issue-5', label: 'after' }),
    ]);
    expect(screen.getByText(/issue-5/)).toBeInTheDocument();
  });

  it('shows each part label in a two-part labeled group', () => {
    renderViewer([
      imagePart({ group: 'g', label: 'broken' }),
      imagePart({ group: 'g', label: 'fixed' }),
    ]);
    expect(screen.getByText('broken')).toBeInTheDocument();
    expect(screen.getByText('fixed')).toBeInTheDocument();
  });

  it('does not invent a header for ungrouped parts', () => {
    renderViewer([textPart({ text: 'loose one' })]);
    expect(screen.getByText('loose one')).toBeInTheDocument();
    // No group chrome for a single ungrouped part.
    expect(screen.queryByText(/^group:/)).not.toBeInTheDocument();
  });
});

describe('ArtifactViewer — never drops a part', () => {
  it('renders every part of a mixed, multi-group artifact', () => {
    const parts = [
      textPart({ text: 'alpha', group: 'g1', label: 'a' }),
      textPart({ text: 'beta', group: 'g1', label: 'b' }),
      textPart({ text: 'gamma', group: 'g2', seq: 0 }),
      textPart({ text: 'delta', group: 'g2', seq: 1 }),
      textPart({ text: 'epsilon' }),
    ];
    renderViewer(parts);
    ['alpha', 'beta', 'gamma', 'delta', 'epsilon'].forEach(t => {
      expect(screen.getByText(t)).toBeInTheDocument();
    });
  });

  it('renders all parts of an oversized group (list fallback)', () => {
    const parts = Array.from({ length: 9 }, (_, i) =>
      textPart({ text: `part-${i}`, group: 'big' }),
    );
    renderViewer(parts);
    for (let i = 0; i < 9; i++) {
      expect(screen.getByText(`part-${i}`)).toBeInTheDocument();
    }
  });
});

describe('ArtifactViewer — status and render metadata', () => {
  it('surfaces an error-status part as an error', () => {
    renderViewer([
      textPart({
        text: 'Diagram render FAILED at emit time',
        status: 'error',
        group: 'issue-9',
        label: 'broken',
      }),
    ]);
    expect(screen.getByText(/FAILED at emit time/)).toBeInTheDocument();
  });

  it('exposes render warnings when the emit-time render logged some', () => {
    renderViewer([
      imagePart({
        rendered: true,
        render_warnings: ['[warning] ELK layout fallback'],
      }),
    ]);
    expect(screen.getByText(/ELK layout fallback/)).toBeInTheDocument();
  });

  it('does not show a warnings affordance when there are none', () => {
    renderViewer([imagePart({ rendered: true, render_warnings: [] })]);
    expect(screen.queryByText(/warning/i)).not.toBeInTheDocument();
  });

  it('offers the diagram spec when one was recorded', () => {
    renderViewer([
      imagePart({
        rendered: true,
        diagram_type: 'mermaid',
        diagram_definition: 'graph LR\n A-->B',
      }),
    ]);
    expect(screen.getByText(/spec/i)).toBeInTheDocument();
  });
});

describe('ArtifactViewer — hierarchy attribution', () => {
  // Match the chrome's own shape ("iter 4"), not a bare substring —
  // artifact BODY text can legitimately contain "iter" (as in
  // "iteration", "iterate", "no-iter"), which would make a loose
  // /iter/i assertion pass or fail for the wrong reason.
  const ITER_CHROME = /^iter \d+$/;

  it('shows the iteration a part came from', () => {
    renderViewer([imagePart({ iteration: 4, group: 'g', label: 'x' })]);
    expect(screen.getByText(ITER_CHROME)).toBeInTheDocument();
  });

  it('omits iteration chrome for parts with no iteration stamp', () => {
    renderViewer([textPart({ text: 'plain body copy' })]);
    expect(screen.queryByText(ITER_CHROME)).not.toBeInTheDocument();
  });

  it('shows iteration 0 — a valid index, not an absent stamp', () => {
    renderViewer([imagePart({ iteration: 0, group: 'g', label: 'x' })]);
    expect(screen.getByText(ITER_CHROME)).toBeInTheDocument();
  });
});

describe('ArtifactViewer — list fallback toggle', () => {
  it('provides a way to view a smart-laid-out group as a plain list', () => {
    renderViewer([
      imagePart({ group: 'g', label: 'broken' }),
      imagePart({ group: 'g', label: 'fixed' }),
    ]);
    const toggle = screen.getByTitle(/list/i);
    expect(toggle).toBeInTheDocument();
    // Toggling must not lose either part.
    fireEvent.click(toggle);
    expect(screen.getByText('broken')).toBeInTheDocument();
    expect(screen.getByText('fixed')).toBeInTheDocument();
  });
});

describe('ArtifactViewer — missing route context', () => {
  it('still renders parts when projectId/runId are unavailable', () => {
    // A tile can render before the project context resolves; the viewer
    // must degrade to non-image rendering rather than crash or blank.
    render(<ArtifactViewer parts={[imagePart()]} projectId="" runId="" />);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText(/chart\.png/)).toBeInTheDocument();
  });
});
