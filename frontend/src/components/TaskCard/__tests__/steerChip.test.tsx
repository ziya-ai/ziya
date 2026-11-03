/**
 * The "💬 say something" chip on the tile's progress line must DO
 * something.  It was a <span> whose tooltip told the user to open
 * Inspect → Live output themselves; the inspector's open/tab state is
 * private, so the tile could not do it for them.  Now the inspector
 * exposes openSteer() through a ref and the chip is a button that calls
 * it: drawer opens, Live output tab is selected, composer input focused.
 *
 * Two layers: the handle's behaviour (rendered), and the tile's wiring
 * to it (static — the tile needs the full run/binding/WS stack to mount).
 */

import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, render, screen } from '@testing-library/react';
import { TaskRunInspector, type TaskRunInspectorHandle } from '../TaskRunInspector';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';

jest.mock('../TaskMarkdown', () => ({
  TaskMarkdown: ({ markdown }: { markdown: string }) => (
    <div data-testid="md">{markdown}</div>
  ),
}));

const live: LiveTaskState = {
  text: { t1: 'working…' },
  toolCalls: [], events: [],
  iterations: [{ index: 0, blockId: 't1', streamText: 'working…',
    status: 'running' } as any],
} as any;

const steer = {
  targets: [{ blockId: 't1', index: 0, label: 'Task' }],
  onSend: jest.fn(),
};

describe('TaskRunInspector.openSteer', () => {
  beforeEach(() => {
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { cb(0); return 0; });
    Element.prototype.scrollIntoView = jest.fn();
  });
  afterEach(() => jest.restoreAllMocks());

  it('opens a closed drawer on Live output and focuses the composer', () => {
    const ref = React.createRef<TaskRunInspectorHandle>();
    const { container } = render(
      <TaskRunInspector ref={ref} live={live} runStatus="running" steer={steer} />,
    );
    const details = container.querySelector('details.tc-tile__inspector') as HTMLDetailsElement;
    expect(details.open).toBe(false);

    act(() => { ref.current!.openSteer(); });

    expect(details.open).toBe(true);
    expect(screen.getByText('Live output').className).toContain('--active');
    const input = container.querySelector('.tc-steer__input') as HTMLTextAreaElement;
    expect(input).not.toBeNull();
    expect(document.activeElement).toBe(input);
  });

  it('switches back to Live output from another tab', () => {
    const ref = React.createRef<TaskRunInspectorHandle>();
    render(
      <TaskRunInspector ref={ref} live={live} defaultOpen runStatus="running" steer={steer} />,
    );
    act(() => { screen.getByText('Events').click(); });
    expect(screen.getByText('Events').className).toContain('--active');
    act(() => { ref.current!.openSteer(); });
    expect(screen.getByText('Live output').className).toContain('--active');
  });
});

describe('tile wiring', () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, '..', 'TaskCardInlineTile.tsx'), 'utf8');

  it('the chip is a button that calls openSteer on the inspector ref', () => {
    const i = src.indexOf('data-testid="steer-chip"');
    expect(i).toBeGreaterThan(-1);
    const chip = src.slice(src.lastIndexOf('<button', i), src.indexOf('</button>', i));
    expect(chip).toContain('onClick={() => inspectorRef.current?.openSteer()}');
    expect(src).not.toMatch(/Open Inspect → Live output to send a note/);
  });

  it('the same ref is mounted on the inspector', () => {
    expect(src).toContain('const inspectorRef = useRef<TaskRunInspectorHandle>(null);');
    const mount = src.slice(src.indexOf('<TaskRunInspector'), src.indexOf('/>', src.indexOf('<TaskRunInspector')));
    expect(mount).toContain('ref={inspectorRef}');
  });
});
