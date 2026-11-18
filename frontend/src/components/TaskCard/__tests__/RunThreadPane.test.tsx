/**
 * @jest-environment jsdom
 *
 * The inline tile's run view: the card on the left, the selected block's
 * transcript on the right, and a way to put the card away.
 *
 *  - RunTranscript renders prose and tool calls interleaved.
 *  - RunThreadPane follows the running block, shows an explicit selection,
 *    and collapses the card column to a rail (remembered per tile).
 *  - Seams: the tile mounts the pane with its run map as the card column,
 *    the dialog's thread uses the same transcript, the stream reducer and
 *    the executor produce the events it reads.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { fireEvent, render, screen } from '@testing-library/react';
// The component file is TranscriptView.tsx, not RunTranscript.tsx: on a
// case-insensitive filesystem './RunTranscript' resolves to the model
// file runTranscript.ts.
import { RunTranscript, RunTranscriptTail } from '../TranscriptView';
import { RunThreadPane } from '../RunThreadPane';
import type { TranscriptSegment } from '../runTranscript';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';
import type { Block } from '../../../types/task_card';

jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => (
    <div data-testid="md">{markdown}</div>
  ),
}));
jest.mock('../../../services/taskRunApi', () => ({
  getIterationArtifact: jest.fn(() => Promise.resolve(null)),
}));

const SEGS: TranscriptSegment[] = [
  { kind: 'text', text: 'before the call' },
  { kind: 'tool', toolId: 'a', toolName: 'rg', header: '-n subgraph', status: 'done',
    resultPreview: 'line one\nline two', durationMs: 200 },
  { kind: 'text', text: 'after the call' },
  { kind: 'tool', toolId: 'b', toolName: 'render_diagram', header: 'type=mermaid',
    status: 'running', startedTs: Date.now() - 4000 },
];

describe('RunTranscript', () => {
  it('renders prose and tool calls in order', () => {
    const { container } = render(<RunTranscript segments={SEGS} live />);
    const text = container.textContent ?? '';
    const at = (s: string) => text.indexOf(s);
    expect(at('before the call')).toBeGreaterThan(-1);
    expect(at('rg')).toBeGreaterThan(at('before the call'));
    expect(at('after the call')).toBeGreaterThan(at('-n subgraph'));
    expect(at('render_diagram')).toBeGreaterThan(at('after the call'));
  });

  it('marks a call in flight, and stops saying so once the run is over', () => {
    const { rerender } = render(<RunTranscript segments={SEGS} live />);
    expect(screen.getByText(/^running/)).toBeTruthy();
    expect(screen.getByText('0.2s')).toBeTruthy();
    rerender(<RunTranscript segments={SEGS} live={false} />);
    expect(screen.queryByText(/^running/)).toBeNull();
    expect(screen.getByText('did not finish')).toBeTruthy();
  });

  it('shows a result on demand', () => {
    render(<RunTranscript segments={SEGS} live />);
    expect(screen.queryByText(/line one/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /rg/ }));
    expect(screen.getByText(/line one/)).toBeTruthy();
  });

  it('falls back to plain text when there is no ordered list', () => {
    render(<RunTranscript fallbackText="recorded text" live={false} />);
    expect(screen.getByTestId('md').textContent).toBe('recorded text');
  });

  it('the tail shows the block and its newest lines', () => {
    render(<RunTranscriptTail label="Sweep · #3" lines={[
      { kind: 'text', text: 'newest sentence' },
      { kind: 'tool', text: 'render_diagram type=mermaid', status: 'running' },
    ]} />);
    expect(screen.getByText('Sweep · #3')).toBeTruthy();
    expect(screen.getByText('newest sentence')).toBeTruthy();
    expect(screen.getByText('render_diagram type=mermaid')).toBeTruthy();
  });
});

const task = (id: string, name: string): Block =>
  ({ block_type: 'task', id, name, instructions: 'x' } as unknown as Block);
const ROOT = { block_type: 'group', id: 'root', body: [task('t1', 'Plan'), task('t2', 'Build')] } as unknown as Block;
const RUN = { id: 'r1', status: 'running', block_states: {} } as any;
const LIVE: LiveTaskState = {
  text: {}, toolCalls: [], events: [], variables: {},
  blockStatuses: { t1: 'done', t2: 'running' },
  iterations: [{
    index: 0, blockId: 't2', streamText: 'hello from build', toolCalls: [], events: [],
    status: 'running', segments: [{ kind: 'text', text: 'hello from build' }],
  }],
};

const pane = (over: Partial<React.ComponentProps<typeof RunThreadPane>> = {}) => (
  <RunThreadPane
    projectId="p" root={ROOT} run={RUN} live={LIVE} isLive
    focus={null} onFocus={jest.fn()} onFollow={jest.fn()}
    storageKey="test.cardAway" hasCard
    card={<div data-testid="card-slot">MAP</div>}
    {...over}
  />
);

describe('RunThreadPane', () => {
  beforeEach(() => window.localStorage.clear());

  it('follows the running block when nothing is selected', () => {
    render(pane());
    expect(screen.getByTestId('card-slot')).toBeTruthy();
    expect(screen.getByTestId('run-pane-head').textContent).toContain('Build');
    expect(screen.getByTestId('md').textContent).toBe('hello from build');
    expect(screen.queryByText('follow run')).toBeNull();
  });

  it('shows an explicit selection and offers the way back', () => {
    const onFollow = jest.fn();
    render(pane({ focus: { blockId: 't1', index: null }, onFollow }));
    expect(screen.getByTestId('run-pane-head').textContent).toContain('Plan');
    expect(screen.queryByTestId('md')).toBeNull();
    expect(screen.getByText('No output was kept for this block.')).toBeTruthy();
    fireEvent.click(screen.getByText('follow run'));
    expect(onFollow).toHaveBeenCalledTimes(1);
  });

  it('puts the card away to a rail, remembers it, and brings it back', () => {
    const onFocus = jest.fn();
    const { unmount } = render(pane({ onFocus }));
    fireEvent.click(screen.getByTitle('Put the card away'));
    expect(screen.queryByTestId('card-slot')).toBeNull();
    expect(window.localStorage.getItem('test.cardAway')).toBe('1');
    // The transcript stays.
    expect(screen.getByTestId('md').textContent).toBe('hello from build');
    // One glyph per top-level block; clicking one selects it.
    const glyphs = screen.getAllByTestId('run-rail-glyph');
    expect(glyphs).toHaveLength(2);
    fireEvent.click(glyphs[0]);
    expect(onFocus).toHaveBeenCalledWith('t1', null);
    unmount();

    render(pane());
    expect(screen.queryByTestId('card-slot')).toBeNull();   // remembered
    fireEvent.click(screen.getByTitle('Show the card'));
    expect(screen.getByTestId('card-slot')).toBeTruthy();
    expect(window.localStorage.getItem('test.cardAway')).toBe('0');
  });

  it('draws neither column nor rail for a single-block card', () => {
    render(pane({ hasCard: false }));
    expect(screen.queryByTestId('card-slot')).toBeNull();
    expect(screen.queryByTestId('run-rail-glyph')).toBeNull();
    expect(screen.getByTestId('md').textContent).toBe('hello from build');
  });
});

describe('wiring', () => {
  const read = (...p: string[]) =>
    fs.readFileSync(path.resolve(__dirname, '..', ...p), 'utf8');

  it('the tile mounts the pane with its run map as the card column', () => {
    const tile = read('TaskCardInlineTile.tsx');
    const mount = tile.indexOf('<RunThreadPane');
    expect(mount).toBeGreaterThan(-1);
    const slot = tile.indexOf('card={', mount);
    expect(slot).toBeGreaterThan(mount);
    const map = tile.indexOf('<TaskRunMap', slot);
    expect(map).toBeGreaterThan(slot);
    expect(tile.slice(mount, map)).toContain('onFollow={clearFocus}');
    // The collapsed receipt keeps a live tail.
    expect(tile).toContain('<RunTranscriptTail');
    expect(tile).toContain('liveTail(live.iterations');
  });

  it('no file differs from another only by case', () => {
    const names = fs.readdirSync(path.resolve(__dirname, '..'))
      .map(n => n.replace(/\.(tsx?|css)$/, '').toLowerCase());
    const dupes = names.filter((n, i) => names.indexOf(n) !== i);
    expect(dupes).toEqual([]);
    for (const f of ['RunThreadPane.tsx', 'TaskCardInlineTile.tsx', 'DeckRunPane.tsx']) {
      expect(read(f)).not.toMatch(/from '\.\/RunTranscript'/);
    }
  });

  it('the dialog thread renders the same transcript', () => {
    const deck = read('DeckRunPane.tsx');
    expect(deck).toContain('<RunTranscript');
    expect(deck).toContain('segments={bucket?.segments}');
  });

  it('the stream reducer and the executor produce what it reads', () => {
    const hook = read('..', '..', 'hooks', 'useTaskRunStream.ts');
    expect(hook).toContain("'task_tool_start'");
    expect(hook).toContain('appendTranscriptEvent(');
    const exec = fs.readFileSync(path.resolve(
      __dirname, '..', '..', '..', '..', '..', 'app', 'agents', 'task_executor.py'), 'utf8');
    expect(exec).toContain('"type": "task_tool_start"');
  });
});
