/**
 * @jest-environment jsdom
 *
 * Sidebar rows: horizontal motion selects text, vertical motion moves the row.
 *
 * Before this split, any 12px of movement on a conversation title started a
 * custom drag and set body user-select to none, so a title could not be
 * selected and copied.
 */
import { classifyRowGesture, hasSelectionWithin, ROW_GESTURE_THRESHOLD_PX } from '../rowGesture';

describe('classifyRowGesture', () => {
  it('stays pending inside the threshold in every direction', () => {
    expect(classifyRowGesture(0, 0)).toBe('pending');
    expect(classifyRowGesture(ROW_GESTURE_THRESHOLD_PX, 0)).toBe('pending');
    expect(classifyRowGesture(-ROW_GESTURE_THRESHOLD_PX, ROW_GESTURE_THRESHOLD_PX)).toBe('pending');
  });

  it('horizontal motion past the threshold is a text selection (the reported bug)', () => {
    expect(classifyRowGesture(40, 0)).toBe('select');
    expect(classifyRowGesture(-40, 3)).toBe('select');
    expect(classifyRowGesture(30, -10)).toBe('select');
  });

  it('vertical motion past the threshold is still a drag (moving rows keeps working)', () => {
    expect(classifyRowGesture(0, 20)).toBe('drag');
    expect(classifyRowGesture(5, -30)).toBe('drag');
  });

  it('an exact diagonal resolves to drag', () => {
    expect(classifyRowGesture(20, 20)).toBe('drag');
  });
});

describe('hasSelectionWithin', () => {
  afterEach(() => {
    window.getSelection()?.removeAllRanges();
    document.body.innerHTML = '';
  });

  function select(node: Node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
  }

  it('is true when the selection is inside the row', () => {
    document.body.innerHTML = '<div id="row"><span id="t">My conversation title</span></div>';
    select(document.getElementById('t')!);
    expect(hasSelectionWithin(document.getElementById('row'))).toBe(true);
  });

  it('is false for a selection elsewhere, an empty selection, or a null element', () => {
    document.body.innerHTML = '<div id="row">Row</div><p id="o">Other text</p>';
    expect(hasSelectionWithin(document.getElementById('row'))).toBe(false);
    select(document.getElementById('o')!);
    expect(hasSelectionWithin(document.getElementById('row'))).toBe(false);
    expect(hasSelectionWithin(null)).toBe(false);
  });
});
