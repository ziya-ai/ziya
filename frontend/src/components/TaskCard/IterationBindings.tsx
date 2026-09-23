/**
 * IterationBindings — what an iteration's ``{{placeholders}}`` expanded to.
 *
 * Two renderings of the same ``TemplateBinding[]`` (carried on a live
 * iteration bucket, or collected from an Events-tab bucket):
 *
 *   - ``BindingChip``: one headline value inlined into the iteration's
 *     collapsed summary row, so a forty-iteration for_each can be scanned
 *     without opening each section.  Only ``{{item}}`` / ``{{item.KEY}}``
 *     qualify — ``index`` is already in the label, and the rest are too
 *     long to be a headline.
 *   - ``IterationBindingsBox``: the full table at the top of the expanded
 *     body, one row per placeholder present in the instructions.  Not a
 *     dump of every binding the engine could have supplied: only what
 *     the author wrote is listed, so the count in the header is the
 *     number of placeholders in the block.
 *
 * Unresolved placeholders (a typo, or a loop-scoped name outside a
 * loop) are shown rather than omitted, mirroring the templating
 * engine's choice to leave them literal so the author sees the mistake.
 */
import React, { useState } from 'react';
import type { TemplateBinding } from './eventLog';

/** Values longer than this collapse behind a "show all" toggle. */
export const BINDING_PREVIEW_CHARS = 200;

/** The binding worth promoting into a collapsed summary row, if any. */
export function headlineBinding(
  bindings: ReadonlyArray<TemplateBinding> | undefined,
): TemplateBinding | null {
  if (!bindings) return null;
  return bindings.find(b =>
    b.resolved && !!b.value
    && (b.placeholder === 'item' || b.placeholder.startsWith('item.')),
  ) ?? null;
}

export const BindingChip: React.FC<{
  bindings?: ReadonlyArray<TemplateBinding>;
}> = ({ bindings }) => {
  const head = headlineBinding(bindings);
  if (!head) return null;
  return (
    <span
      className="tc-tile__iter-binding-chip"
      title={`{{${head.placeholder}}} = ${head.value}`}
      data-testid="iter-binding-chip"
    >
      <span className="tc-tile__iter-binding-chip-key">{head.placeholder}</span>
      {' = '}
      {head.value}
    </span>
  );
};

const BindingValue: React.FC<{
  binding: TemplateBinding;
  expanded: boolean;
  onToggle: () => void;
}> = ({ binding, expanded, onToggle }) => {
  if (!binding.resolved) {
    return (
      <span className="tc-tile__iter-bindings-value tc-tile__iter-bindings-value--unresolved">
        unresolved — left literal in the instructions
      </span>
    );
  }
  const value = binding.value ?? '';
  if (value === '') {
    return <span className="tc-tile__iter-bindings-value tc-tile__iter-bindings-value--empty">(empty)</span>;
  }
  const fullLength = binding.truncated && typeof binding.length === 'number'
    ? binding.length : value.length;
  const needsToggle = value.length > BINDING_PREVIEW_CHARS || !!binding.truncated;
  const shown = expanded || !needsToggle ? value : value.slice(0, BINDING_PREVIEW_CHARS);
  return (
    <span className="tc-tile__iter-bindings-value">
      {shown}
      {needsToggle && !expanded && '…'}
      {needsToggle && (
        <button
          type="button"
          className="tc-tile__iter-bindings-more"
          onClick={onToggle}
        >
          {expanded ? 'show less' : `show all (${fullLength.toLocaleString()} chars)`}
        </button>
      )}
      {expanded && binding.truncated && (
        <span className="tc-tile__iter-bindings-value--empty">
          {' '}— server kept the first {value.length.toLocaleString()} of {fullLength.toLocaleString()} chars
        </span>
      )}
    </span>
  );
};

export const IterationBindingsBox: React.FC<{
  bindings?: ReadonlyArray<TemplateBinding>;
}> = ({ bindings }) => {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  if (!bindings || bindings.length === 0) return null;
  const toggle = (key: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  const unresolved = bindings.filter(b => !b.resolved).length;
  return (
    <div className="tc-tile__iter-bindings" data-testid="iter-bindings">
      <div className="tc-tile__iter-bindings-head">
        <span>⟨⟩ template bindings</span>
        <span className="tc-tile__iter-bindings-count">
          — {bindings.length} placeholder{bindings.length === 1 ? '' : 's'} in instructions
          {unresolved > 0 && `, ${unresolved} unresolved`}
        </span>
      </div>
      <table className="tc-tile__iter-bindings-table">
        <tbody>
          {bindings.map(b => (
            <tr key={b.placeholder} data-placeholder={b.placeholder}>
              <td className="tc-tile__iter-bindings-key">
                {'{{'}{b.placeholder}{'}}'}
              </td>
              <td>
                <BindingValue
                  binding={b}
                  expanded={expanded.has(b.placeholder)}
                  onToggle={() => toggle(b.placeholder)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default IterationBindingsBox;
