/**
 * Steer wiring — asserts the connection between the pieces, not the
 * pieces: TaskRunInspector mounts SteerComposer under the Live output
 * tab when given a ``steer`` prop and a live run, hides it once the run
 * is over (nothing is listening), and the stream dispatcher refetches on
 * the three steer events so ``run.steer_notes`` — which the chip and the
 * composer's note history read — is never stale.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { TaskRunInspector } from '../TaskRunInspector';
import { dispatchTaskRunEvent } from '../../../hooks/useTaskRunStream';
import type { LiveTaskState } from '../../../hooks/useTaskRunStream';

const live: LiveTaskState = {
  text: { t1: 'working…' },
  toolCalls: [], events: [],
  iterations: [{ index: 0, blockId: 't1', streamText: 'working…',
                 toolCalls: [], events: [], status: 'running' }],
  variables: {}, blockStatuses: {},
};

const steer = {
  targets: [{ blockId: 't1', index: 0, label: 'audit' }],
  onSend: jest.fn(),
};

describe('steer wiring', () => {
  it('inspector mounts the composer on the Live tab of a live run', () => {
    render(
      <TaskRunInspector live={live} defaultOpen runStatus="running" steer={steer} />,
    );
    expect(screen.getByTestId('steer-composer')).toBeInTheDocument();
  });

  it('inspector withholds the composer once the run is over', () => {
    render(
      <TaskRunInspector live={live} defaultOpen runStatus="done" steer={steer} />,
    );
    expect(screen.queryByTestId('steer-composer')).toBeNull();
  });

  it('inspector renders no composer without a steer prop', () => {
    render(<TaskRunInspector live={live} defaultOpen runStatus="running" />);
    expect(screen.queryByTestId('steer-composer')).toBeNull();
  });

  it.each(['task_steer_queued', 'task_steer_delivered', 'task_steer_expired'])(
    '%s refetches the run record', (type) => {
      expect(dispatchTaskRunEvent({ type, block_id: 't1' })).toEqual({ kind: 'refetch' });
    },
  );
});
