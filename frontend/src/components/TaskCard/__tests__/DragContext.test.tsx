/**
 * @jest-environment jsdom
 *
 * Regression test for the drag-abort bug: DragHandle.onDragStart must
 * NOT synchronously flip the provider's draggingId.  Doing so mounts
 * the sibling drop zones (null -> element) inside the dragstart event;
 * Chrome aborts a native drag the instant the dragged element's subtree
 * mutates, yielding dragstart -> dragend with zero drag events and no
 * usable drop targets.  Confirmed live via a MutationObserver showing
 * +7/-7 node churn during dragstart.  The fix defers beginDrag one tick.
 *
 * This test exercises the real provider + DragHandle (no BlockBody /
 * BlockEditor, so it avoids the uuid/ProjectContext import chain that
 * jest 27 can't parse).  A local consumer mimics DropZone: it renders a
 * marker element ONLY while draggingId != null, exactly the mount-on-
 * drag behaviour that caused the subtree mutation.
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { TaskCardDragProvider, DragHandle, useTaskCardDrag } from '../DragContext';
import type { Block } from '../../../types/task_card';

// Mounts a marker the moment a drag is in flight — the stand-in for the
// real DropZones whose synchronous mount aborted the drag.
const DragMarker: React.FC = () => {
  const ctx = useTaskCardDrag();
  if (ctx.draggingId == null) return null;
  return <div data-testid="drop-zone" />;
};

const root: Block = {
  block_type: 'parallel', id: 'root', name: 'root',
  body: [
    { block_type: 'task', id: 't1', name: 't1', body: [] },
    { block_type: 'task', id: 't2', name: 't2', body: [] },
  ],
};

function renderTree() {
  return render(
    <TaskCardDragProvider root={root} onRootChange={() => {}}>
      <DragHandle id="t1" />
      <DragMarker />
    </TaskCardDragProvider>,
  );
}

describe('DragHandle defers beginDrag (drag-abort regression)', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('does NOT mount drop zones synchronously during dragstart', () => {
    renderTree();
    // At rest: no zones.
    expect(screen.queryByTestId('drop-zone')).toBeNull();

    const handle = screen.getByRole('button', { name: /drag to move/i });
    // Fire dragStart WITHOUT advancing timers — this is the synchronous
    // window in which a real browser would abort if the subtree mutated.
    fireEvent.dragStart(handle, { dataTransfer: { setData: () => {}, effectAllowed: '' } });

    // The bug: zones appear here (synchronous beginDrag).  The fix:
    // beginDrag is deferred, so still none at this instant.
    expect(screen.queryByTestId('drop-zone')).toBeNull();
  });

  it('mounts drop zones after the deferred tick, and clears on dragend', () => {
    renderTree();
    const handle = screen.getByRole('button', { name: /drag to move/i });

    fireEvent.dragStart(handle, { dataTransfer: { setData: () => {}, effectAllowed: '' } });
    // Flush the deferred beginDrag.
    act(() => { jest.runAllTimers(); });
    expect(screen.queryByTestId('drop-zone')).not.toBeNull();

    // dragEnd clears the drag state synchronously.
    fireEvent.dragEnd(handle);
    expect(screen.queryByTestId('drop-zone')).toBeNull();
  });
});
