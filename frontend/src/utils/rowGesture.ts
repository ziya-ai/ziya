/**
 * Classify a press-and-move on a sidebar row as a move (drag) or a text
 * selection.
 *
 * The sidebar list is vertical, so moving a row into a folder is vertical
 * motion. Selecting a title's text is horizontal motion. Without this
 * split, any 12px of movement started a drag and set body user-select to
 * none, which made row titles impossible to select and copy.
 */
export type RowGesture = 'pending' | 'drag' | 'select';

export const ROW_GESTURE_THRESHOLD_PX = 12;

export function classifyRowGesture(
  dx: number,
  dy: number,
  threshold: number = ROW_GESTURE_THRESHOLD_PX,
): RowGesture {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax <= threshold && ay <= threshold) return 'pending';
  return ay >= ax ? 'drag' : 'select';
}

/**
 * True when the document has a non-empty text selection that starts or ends
 * inside \`el\`. A click that ends a selection must not also act as a click
 * (e.g. open the conversation), or the user cannot copy what they selected.
 */
export function hasSelectionWithin(el: Node | null): boolean {
  if (!el || typeof window === 'undefined' || !window.getSelection) return false;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.toString().length === 0) return false;
  return (!!sel.anchorNode && el.contains(sel.anchorNode))
    || (!!sel.focusNode && el.contains(sel.focusNode));
}
