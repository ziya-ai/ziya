/**
 * Structural guard: the sidebar row's mousedown handler classifies the
 * gesture with classifyRowGesture (horizontal = text selection, vertical =
 * drag), and the row click handler does not navigate when it ends a text
 * selection.  Previously any 12px of motion started a drag, which made
 * conversation titles impossible to select and copy.
 *
 * Static because driving the virtualized tree through a real mouse gesture
 * in jsdom needs the full ChatContext/FolderContext stack; the behaviour of
 * the helpers themselves is covered in utils/__tests__/rowGesture.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.resolve(__dirname, '..', 'MUIChatHistory.tsx'), 'utf8');
const code = SRC.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

function slice(start: string, end: string): string {
  const s = code.indexOf(start);
  expect(s).toBeGreaterThan(-1);
  const e = code.indexOf(end, s);
  expect(e).toBeGreaterThan(s);
  return code.slice(s, e);
}

describe('sidebar row: select text vs drag', () => {
  it('imports the gesture helpers', () => {
    expect(code).toMatch(/import \{[^}]*classifyRowGesture[^}]*hasSelectionWithin[^}]*\} from '..\/utils\/rowGesture'/);
  });

  it('mousedown starts a drag only for a drag gesture, and abandons detection on select', () => {
    const body = slice('const handleCustomMouseDown = (e: React.MouseEvent) => {', 'const cleanup = () =>');
    expect(body).toContain('classifyRowGesture(me.clientX - startX, me.clientY - startY)');
    expect(body).not.toMatch(/Math\.abs\(me\.clientX - startX\) > 12 \|\|/);
    const dragBranch = body.indexOf("if (gesture === 'drag') {");
    const start = body.indexOf('startCustomDrag(', dragBranch);
    const selectBranch = body.indexOf("} else if (gesture === 'select') {");
    expect(dragBranch).toBeGreaterThan(-1);
    expect(start).toBeGreaterThan(dragBranch);
    expect(selectBranch).toBeGreaterThan(start);
    expect(body.slice(selectBranch)).toContain('cleanup();');
    expect(body.slice(selectBranch)).not.toContain('startCustomDrag(');
  });

  it('row click returns before navigating when it ends a selection', () => {
    const body = slice('if (e.detail > 1) return;', 'handleConversationClick(');
    expect(body).toContain('if (hasSelectionWithin(e.currentTarget)) return;');
  });
});
