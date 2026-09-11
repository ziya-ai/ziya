/**
 * Seam test: the `task_card_stage` builtin creates a card + a staged binding
 * server-side mid-turn.  `useTaskBindings` only re-fetches on the
 * `task-binding-created` window event, so unless chatApi.ts fires that event
 * on the tool's `tool_display` result the tile does not appear until reload.
 *
 * chatApi.ts's stream handler is not exported (see chatApiConnectionError
 * .test.ts for the convention), so this asserts the wiring at the sink, and
 * checks the event name matches what the hook actually listens for.
 */
import * as fs from 'fs';
import * as path from 'path';

const CHAT_API_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'chatApi.ts'), 'utf-8');
const HOOK_SRC = fs.readFileSync(
  path.join(__dirname, '..', '..', 'hooks', 'useTaskBindings.ts'), 'utf-8');

function stageBlock(): string {
  const start = CHAT_API_SRC.indexOf("unwrappedData.tool_name === 'task_card_stage'");
  expect(start).toBeGreaterThan(-1);
  // Scope to the enclosing if-block: from the match to the next
  // top-level comment that begins the diff-validation handler.
  const end = CHAT_API_SRC.indexOf('Handle diff validation status', start);
  expect(end).toBeGreaterThan(start);
  return CHAT_API_SRC.slice(start, end);
}

describe('chatApi.ts task_card_stage → task-binding-created sync', () => {
  it('handles the tool_display result for task_card_stage', () => {
    const block = stageBlock();
    expect(block).toMatch(/result\.success && result\.binding_id/);
  });

  it('dispatches the exact event name useTaskBindings listens for', () => {
    const block = stageBlock();
    const m = block.match(/new CustomEvent\('([a-z-]+)'\)/);
    expect(m).not.toBeNull();
    const eventName = m![1];
    expect(HOOK_SRC).toContain(`addEventListener('${eventName}'`);
  });

  it('tolerates a stringified result (provider-dependent)', () => {
    expect(stageBlock()).toMatch(/typeof raw === 'string'/);
  });
});
