/**
 * Dispatcher — picks the right editor for a block's type.
 * Keeps recursion out of the individual editors' imports.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import { TaskBlockEditor } from './TaskBlockEditor';
import { RepeatBlockEditor } from './RepeatBlockEditor';
import { ParallelBlockEditor } from './ParallelBlockEditor';
import { UntilBlockEditor } from './UntilBlockEditor';
import { ScheduleBlockEditor } from './ScheduleBlockEditor';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
}

export const BlockEditor: React.FC<Props> = (props) => {
  if (props.block.block_type === 'repeat') return <RepeatBlockEditor {...props} />;
  if (props.block.block_type === 'parallel') return <ParallelBlockEditor {...props} />;
  if (props.block.block_type === 'until') return <UntilBlockEditor {...props} />;
  if (props.block.block_type === 'schedule') return <ScheduleBlockEditor {...props} />;
  if (props.block.block_type === 'task') return <TaskBlockEditor {...props} />;
  return <div className="tc-block tc-block-unknown">Unsupported block type: {props.block.block_type}</div>;
};
