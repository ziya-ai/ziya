/**
 * Editor for a Group block — a neutral run-once sequential container.
 *
 * Rendered WITHOUT chrome: no tc-block box, no header, no type label.
 * It is the invisible card-root wrapper, so the canvas simply presents
 * an ordered list you drop blocks into. This is what lets a State be
 * added first and have operators follow it, and lets a State precede a
 * loop without entering the loop's scope.
 *
 * Semantics: body runs top-to-bottom exactly once.
 * See app/agents/block_executor.py (group -> _execute_sequence).
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import { BlockBody } from './BlockBody';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

export const GroupBlockEditor: React.FC<Props> = ({ block, onChange }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  return (
    <BlockBody
      parentId={block.id}
      body={block.body}
      bodyClassName="tc-block-body-group"
      sequenceLabel=""
      onChange={body => update({ body })}
    />
  );
};
