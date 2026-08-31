/**
 * Wiring for "copy a task card into a conversation without running it".
 *
 * The staged binding shape (run_id null → TaskCardInlineTile renders
 * StagedCardTile with Run/Discard) already existed, but only /goal could
 * produce it — it creates the binding directly through storage.  The deck
 * had no route to it: every path went through createBinding, which always
 * launched.
 *
 * Static source assertions, following launchTitleSeedWiring.test.ts.  The
 * endpoint behaviour is covered by tests/test_api_task_bindings_staged_create.py;
 * what can silently break here is the JOIN across four hops —
 *
 *   button → handleStage* → launchToChat(..., staged) → createBinding body
 *
 * — any one of which can be correct in isolation while the feature does
 * nothing.  The historically likeliest break is the last: the flag being
 * accepted by the callback signature but never reaching the request body,
 * which launches the run the user asked NOT to start.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');

const LIBRARY = () => read('components/TaskCard/TaskCardsLibrary.tsx');
const TYPES = () => read('types/task_binding.ts');

/** The launchToChat callback body (up to its dependency array). */
const launchToChat = (): string => {
  const m = LIBRARY().match(
    /const launchToChat = useCallback\([\s\S]*?\n  \}, \[[^\]]*\]\);/,
  );
  expect(m).not.toBeNull();
  return m![0];
};

const namedCallback = (name: string): string => {
  const m = LIBRARY().match(
    new RegExp(`const ${name} = useCallback\\([\\s\\S]*?\\n  \\}, \\[[^\\]]*\\]\\);`),
  );
  expect(m).not.toBeNull();
  return m![0];
};

describe('the request type carries the staged flag', () => {
  it('TaskBindingCreateRequest declares staged', () => {
    // Adding it to the callback but not the type is a compile error in
    // strict mode — but the reverse (type only) compiles and does nothing.
    expect(TYPES()).toMatch(/interface TaskBindingCreateRequest[\s\S]*?staged\?: boolean/);
  });

  it('TaskBindingCreateResponse.run is nullable', () => {
    // A staged create has no run.  Left as `run: TaskRun` the compiler
    // blesses `resp.run.id`, which throws at runtime on the staged path.
    expect(TYPES()).toMatch(/interface TaskBindingCreateResponse[\s\S]*?run: TaskRun \| null/);
  });
});

describe('launchToChat threads staged through to the request', () => {
  it('accepts a staged parameter', () => {
    expect(launchToChat()).toMatch(/staged\s*=\s*false/);
  });

  it('puts staged in the createBinding body', () => {
    // The load-bearing hop.  Without it the deck reports "copied, not
    // started" while the run is already executing.
    const body = launchToChat().match(/createBinding\([\s\S]*?\}\);/);
    expect(body).not.toBeNull();
    expect(body![0]).toMatch(/\bstaged\b/);
  });

  it('narrows run before reading its id', () => {
    // resp.run is null when staged; an unguarded resp.run.id throws
    // before the tile is ever notified.
    //
    // Asserted as "every read of resp.run.id is guarded", not as "the
    // string resp.run.id is absent": the correct form
    // `if (resp.run) setActiveRunId(resp.run.id)` CONTAINS the
    // unguarded-looking substring, so an absence assertion fails on
    // working code.  Anchor on the guard.
    const src = launchToChat();
    for (const m of src.matchAll(/^.*resp\.run\.id.*$/gm)) {
      // Either an explicit if-guard on the same line, or optional
      // chaining.  A bare `setActiveRunId(resp.run.id);` matches neither.
      expect(m[0]).toMatch(/if \(resp\.run\)|resp\.run\?\./);
    }
    // Positive control: the id is read at all.  Without this the loop
    // above passes vacuously if the line is deleted outright, which
    // would leave the deck never tracking the run it just launched.
    expect(src).toMatch(/setActiveRunId\(/);
    expect(src).toMatch(/resp\.run\?\.id|if \(resp\.run\)/);
  });

  it('does not claim a running task for a staged copy', () => {
    // The conversation-list gear is cleared by the run reaching a
    // terminal state.  With no run there is nothing to clear it, so the
    // gear would persist indefinitely.
    expect(launchToChat()).toMatch(/if \(!staged\) addRunningTaskConversation/);
  });

  it('still notifies the binding hook so the staged tile appears', () => {
    // Positive control: the tile is the only surface the staged card has.
    expect(launchToChat()).toMatch(/task-binding-created/);
  });
});

describe('the deck exposes the copy action', () => {
  it('has a current-conversation copy handler that stages', () => {
    expect(namedCallback('handleStageCurrent')).toMatch(/launchToChat\([^)]*true\)/);
  });

  it('has a new-conversation copy handler that stages', () => {
    expect(namedCallback('handleStageNew')).toMatch(/launchToChat\([^)]*true\)/);
  });

  it('does not gate copying behind the unsigned-run confirmation', () => {
    // Nothing executes, so there is no clamp to warn about — and staging
    // is the recommended route for an unsigned card precisely because the
    // staged tile carries the ziya-approve command and its own Run gate.
    // A confirm here would train users to dismiss the one that matters.
    expect(namedCallback('handleStageCurrent')).not.toMatch(/confirmIfUnsigned/);
    expect(namedCallback('handleStageNew')).not.toMatch(/confirmIfUnsigned/);
    // Positive control: the LAUNCH handlers must still be gated.
    expect(namedCallback('handleLaunchCurrent')).toMatch(/confirmIfUnsigned/);
    expect(namedCallback('handleLaunchNew')).toMatch(/confirmIfUnsigned/);
  });

  it('both handlers are bound to rendered buttons', () => {
    // A handler no control calls is the quietest way for this to ship
    // broken: every assertion above passes and nothing is clickable.
    const src = LIBRARY();
    expect(src).toMatch(/onClick=\{handleStageCurrent\}/);
    expect(src).toMatch(/onClick=\{handleStageNew\}/);
  });
});
