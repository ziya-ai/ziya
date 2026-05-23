/**
 * Template-hint chips for Task instruction fields.
 *
 * When a Task is inside a Repeat or Until, click a chip to insert the
 * corresponding Mustache placeholder ({{previous.summary}}, {{index}},
 * etc.) at the caret position in the instructions textarea.
 *
 * The auto-injection in app/agents/block_executor.py means most users
 * can skip these entirely -- the model already sees prior results in
 * its prompt.  These chips are for power users who want explicit
 * control over how prior context is referenced (e.g. inline within a
 * sentence: "Building on {{previous.summary}}, now do X").
 */

import React from 'react';
import './task-card-editor.css';

type Hint = { label: string; placeholder: string; tip: string };

interface Props {
  /** What kind of loop the parent block is, if any.  null when the
   *  Task is at the top level or inside a Parallel block (where prior
   *  context is undefined). */
  parentLoopType: 'repeat' | 'until' | null;
  /** True when the parent Repeat is in for_each mode and \`item\` bindings
   *  are available. */
  forEachActive?: boolean;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  onInsert: (next: string) => void;
  currentValue: string;
}

const HINTS_BASE: Hint[] = [
  { label: 'iteration #', placeholder: '{{index}}',
    tip: '0-indexed iteration count' },
];
const HINTS_PREVIOUS: Hint[] = [
  { label: 'last result', placeholder: '{{previous.summary}}',
    tip: 'Summary of the previous iteration' },
  { label: 'last decisions', placeholder: '{{previous.decisions}}',
    tip: 'Bullet list of the previous iteration\'s decisions' },
];
const HINTS_ALL: Hint[] = [
  { label: 'all prior results', placeholder: '{{all.summaries}}',
    tip: 'All prior iteration summaries, joined' },
];
const HINTS_FOR_EACH: Hint[] = [
  { label: 'current item', placeholder: '{{item}}',
    tip: 'The current for_each item' },
];

export const TemplateHintChips: React.FC<Props> = ({
  parentLoopType, forEachActive, textareaRef, onInsert, currentValue,
}) => {
  if (!parentLoopType) return null;
  const hints: Hint[] = [...HINTS_BASE, ...HINTS_PREVIOUS, ...HINTS_ALL];
  if (forEachActive) hints.push(...HINTS_FOR_EACH);

  const insert = (placeholder: string) => {
    const ta = textareaRef.current;
    const start = ta?.selectionStart ?? currentValue.length;
    const end = ta?.selectionEnd ?? currentValue.length;
    const next = currentValue.slice(0, start) + placeholder + currentValue.slice(end);
    onInsert(next);
    requestAnimationFrame(() => {
      if (!ta) return;
      ta.focus();
      const cursor = start + placeholder.length;
      ta.setSelectionRange(cursor, cursor);
    });
  };

  return (
    <div className="tc-template-hints">
      <span className="tc-template-hints-label">Insert:</span>
      {hints.map(h => (
        <button key={h.placeholder} type="button"
                className="tc-template-hint-chip"
                onClick={() => insert(h.placeholder)}
                title={h.tip}>{h.label}</button>
      ))}
    </div>
  );
};
