/**
 * Seam test: the `task_card_stage` and `task_card_launch` builtins create a
 * binding server-side mid-turn (staged, and bound to a live run,
 * respectively).  `useTaskBindings` only re-fetches on the
 * `task-binding-created` window event, so unless chatApi.ts fires that event
 * on the tool's `tool_display` result the tile does not appear until reload —
 * which for task_card_launch meant a run executing with no monitor anywhere.
 *
 * chatApi.ts's stream handler is not exported (see chatApiConnectionError
 * .test.ts for the convention), so this asserts the wiring at the sink, and
 * checks the event name matches what the hook actually listens for.
 *
 * The block is scoped by brace-matching from the `if (` that names the tool,
 * not by a neighbouring comment: the previous version anchored on prose in
 * the following handler's comment and went red when that was reworded.
 */
import * as fs from 'fs';
import * as path from 'path';

const CHAT_API_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'chatApi.ts'), 'utf-8');
const HOOK_SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'hooks', 'useTaskBindings.ts'), 'utf-8');

/** The full `if (...) { ... }` statement whose condition names `toolName`. */
function toolDisplayBlock(toolName: string): string {
  const needle = `unwrappedData.tool_name === '${toolName}'`;
  const hit = CHAT_API_SRC.indexOf(needle);
  expect(hit).toBeGreaterThan(-1);
  const start = CHAT_API_SRC.lastIndexOf('if (', hit);
  expect(start).toBeGreaterThan(-1);
  // Walk to the opening brace of the if-body, then to its matching close.
  const open = CHAT_API_SRC.indexOf('{', hit);
  expect(open).toBeGreaterThan(hit);
  let depth = 0;
  for (let i = open; i < CHAT_API_SRC.length; i++) {
    const ch = CHAT_API_SRC[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return CHAT_API_SRC.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces after ${needle}`);
}

describe.each(['task_card_stage', 'task_card_launch'])(
  'chatApi.ts %s → task-binding-created sync', (toolName) => {
    it('handles the tool_display result and requires a binding_id', () => {
      const block = toolDisplayBlock(toolName);
      expect(block).toMatch(/unwrappedData\.type === 'tool_display'/);
      expect(block).toMatch(/result\.success && result\.binding_id/);
    });

    it('dispatches the exact event name useTaskBindings listens for', () => {
      const block = toolDisplayBlock(toolName);
      const m = block.match(/new CustomEvent\('([a-z-]+)'\)/);
      expect(m).not.toBeNull();
      const eventName = m![1];
      expect(HOOK_SRC).toContain(`addEventListener('${eventName}'`);
    });

    it('tolerates a stringified result (provider-dependent)', () => {
      expect(toolDisplayBlock(toolName)).toMatch(/typeof raw === 'string'/);
    });
  },
);
