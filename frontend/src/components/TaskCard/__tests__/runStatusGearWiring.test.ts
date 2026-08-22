/**
 * Wiring for the per-status gear cluster in the conversation list.
 *
 * Static source assertions, for the reason every defect in this area has
 * had: the logic was correct and unreachable.  A recorder defined and
 * never called, a glyph map missing one key, a derivation with no
 * consumer — each had green unit tests while the feature could not work.
 * The chain here has six joins (hook -> Conversation -> ChatContext ->
 * ActiveChatContext -> MUIChatHistory -> row -> component), and any one
 * of them failing is silent.
 */

import * as fs from 'fs';
import * as path from 'path';

const root = path.resolve(__dirname, '../../..');
const read = (p: string) => fs.readFileSync(path.join(root, p), 'utf8');

/**
 * Text of the first construct that starts at ``open`` and runs to the next
 * ``close``.  Membership is then asserted INSIDE it.
 *
 * Every brittle assertion this file has carried was positional: one pinned
 * a field as the LAST entry of a dep array, another required two names to
 * be ADJACENT lines, a third anchored on a fixed-size tail window.  All
 * three passed when written and then failed on a benign edit -- adding a
 * sibling field, or inserting a line between two others -- so a correct
 * change looked like a regression and the real signal was lost in the
 * noise of fixing the test.
 *
 * Scope-then-membership is the shape that does not do that: it fails when
 * the thing is absent and passes wherever it sits.  Verified in both
 * directions for each use below.
 */
function within(src: string, open: RegExp, close = ']'): string {
  const m = src.match(open);
  if (m == null || m.index == null) return '';
  const end = src.indexOf(close, m.index);
  return end === -1 ? '' : src.slice(m.index, end + 1);
}

const HOOK = () => read('hooks/useTaskBindings.ts');
const CONV = () => read('components/Conversation.tsx');
const CHAT_CTX = () => read('context/ChatContext.tsx');
const ACTIVE_CTX = () => read('context/ActiveChatContext.tsx');
const SIDEBAR = () => read('components/MUIChatHistory.tsx');
const GEARS = () => read('components/TaskCard/RunStatusGears.tsx');
const TILE = () => read('components/TaskCard/TaskCardInlineTile.tsx');

describe('the hook exposes what counting needs', () => {
  it('returns the flat binding list, not only the anchor map', () => {
    // The anchor map drops staged bindings and groups for render; a caller
    // that flattens it back out has already lost data.
    expect(HOOK()).toMatch(/return \{ bindings, bindingsByAnchor/);
  });
});

describe('Conversation publishes bindings instead of a boolean', () => {
  it('destructures the flat list', () => {
    expect(CONV()).toMatch(/const \{ bindings, bindingsByAnchor \} = useTaskBindings/);
  });

  it('pushes them into context', () => {
    expect(CONV()).toMatch(/setConversationTaskBindings\(currentConversationId, bindings\)/);
  });

  it('keys that effect on the bindings, not just the conversation', () => {
    const fn = CONV().match(
      /setConversationTaskBindings\(currentConversationId, bindings\);[\s\S]{0,200}?\}, \[[^\]]*\]/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/bindings/);
  });
});

describe('ChatContext carries bindings rather than more boolean sets', () => {
  it('declares the map on the context type', () => {
    expect(CHAT_CTX()).toMatch(/conversationTaskBindings: Map<string, TaskBinding\[\]>/);
  });

  it('imports the binding type', () => {
    expect(CHAT_CTX()).toMatch(/import type \{ TaskBinding \} from '\.\.\/types\/task_binding'/);
  });

  it('holds it in state', () => {
    expect(CHAT_CTX()).toMatch(/setConversationTaskBindingsState/);
  });

  it('exports the setter through the provider value', () => {
    // A declared-but-unexported value is the same defect shape as a
    // recorder that is defined and never called.
    // Asserted as an adjacent PAIR over the whole file rather than by
    // scanning a fixed-size tail window.  The window was measured from a
    // file length that grows: ChatContext is ~276k chars and the value
    // object sits ~11.5k from the end, so an 8k window silently stopped
    // covering the thing under test and the assertion failed for a reason
    // unrelated to the code it guards.
    // Both names present in the value object, NOT adjacent to each other.
    // Requiring adjacency meant any field inserted between them broke a
    // test that only cares that both are exported.
    const value = within(
      CHAT_CTX(), /const value(?::\s*\w+)? = \{|\n    return \{\n\s*addStreamingConversation,/, '};',
    );
    const scope = value === '' ? CHAT_CTX() : value;
    expect(scope).toMatch(/\bconversationTaskBindings,/);
    expect(scope).toMatch(/\bsetConversationTaskBindings,/);
  });

  it('does not also carry the superseded held-Set design', () => {
    // The two-Set design (heldTaskConversations / isHeldTask) was an
    // EARLIER attempt at this surface, abandoned because a Set cannot
    // carry a count and eight statuses would mean eight mutually
    // consistent Sets.  It was later applied anyway, on top of the gear
    // cluster, and one of its hunks REPLACED the isRunningTaskConv
    // declaration the cluster's optimistic fallback still needs -- so the
    // row referenced an undeclared name and the whole sidebar stopped
    // compiling.  Another of its hunks matched inside the project-switcher
    // Menu, ~500 lines from the row it was written for, because its anchor
    // no longer existed.
    //
    // Asserted as an ABSENCE across every file the design touched: two
    // competing implementations of one indicator is worse than either,
    // and a half-applied one does not compile at all.
    for (const src of [CHAT_CTX(), ACTIVE_CTX(), SIDEBAR()]) {
      expect(src).not.toMatch(/heldTaskConversations/);
      expect(src).not.toMatch(/isHeldTask/);
    }
  });

  it('keeps the optimistic running declaration the fallback depends on', () => {
    // The specific line the superseded change overwrote.  Its absence is
    // a TS2304 at the row, several thousand lines away from the edit that
    // caused it.
    expect(SIDEBAR()).toMatch(
      /const isRunningTaskConv = !!\(convId && runningTaskConversations\.has\(convId\)\)/,
    );
  });

  it('passes it to the ActiveChatProvider JSX mount too', () => {
    // The seam this suite originally missed.  ActiveChatProvider receives
    // its value BOTH as explicit JSX props at the mount site and as a
    // memoized object inside itself, so a new field has to be added in two
    // places.  Adding it to the interface and the value object but not the
    // mount type-errors at the mount, not at the definition -- a build
    // failure whose message points nowhere near the field that caused it.
    // Asserted next to the running-task prop so a future field cannot be
    // added beside it and skip this list.
    expect(CHAT_CTX()).toMatch(
      /runningTaskConversations=\{runningTaskConversations\}\s*\n\s*conversationTaskBindings=\{conversationTaskBindings\}/,
    );
  });

  it('bails out when no run status actually changed', () => {
    // Polling refreshes bindings continuously; replacing the Map identity
    // on every refresh re-renders the whole list for no visible change.
    const fn = CHAT_CTX().match(
      /const setConversationTaskBindings = useCallback\([\s\S]*?\n {4}\}, \[\]\);/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/return prev/);
    expect(fn![0]).toMatch(/run_status/);
  });

  it('keeps the optimistic running set, which answers a different question', () => {
    // It is set at launch, before any run record exists, so the
    // server-derived map cannot cover that window.
    expect(CHAT_CTX()).toMatch(/addRunningTaskConversation/);
  });
});

describe('ActiveChatContext re-exports it, or the sidebar never sees it', () => {
  it('declares it on the value type', () => {
    expect(ACTIVE_CTX()).toMatch(/conversationTaskBindings: Map<string, TaskBinding\[\]>/);
  });

  it('includes it in the memoized value', () => {
    expect(ACTIVE_CTX()).toMatch(/conversationTaskBindings: value\.conversationTaskBindings/);
  });

  it('includes it in the memo dep array', () => {
    // Present in the value but absent from the deps means it updates
    // invisibly — the worst of the three outcomes, because it looks wired.
    const deps = ACTIVE_CTX().match(/\[\s*\n\s*value\.currentConversationId[\s\S]*?\]\s*\)/);
    expect(deps).not.toBeNull();
    expect(deps![0]).toMatch(/value\.conversationTaskBindings/);
  });
});

describe('the sidebar renders the cluster', () => {
  it('imports the component', () => {
    expect(SIDEBAR()).toMatch(/import RunStatusGears from '\.\/TaskCard\/RunStatusGears'/);
  });

  it('reads the map from context', () => {
    // Scoped to the destructure, then membership.  The previous form
    // required conversationTaskBindings to be the LAST name before the
    // closing brace, so destructuring one more field from useActiveChat --
    // which this very feature went on to do -- would have broken it.
    const block = within(
      SIDEBAR(), /const \{\n(?:.*\n)*?\s*\} = useActiveChat\(\);/, '} = useActiveChat();',
    );
    expect(block).not.toBe('');
    expect(block).toMatch(/conversationTaskBindings,/);
  });

  it('resolves the row\'s own bindings', () => {
    expect(SIDEBAR()).toMatch(/rowTaskBindings = convId \? conversationTaskBindings\.get\(convId\)/);
  });

  it('passes them to the row', () => {
    expect(SIDEBAR()).toMatch(/taskBindings=\{rowTaskBindings\}/);
  });

  it('declares the prop and destructures it', () => {
    expect(SIDEBAR()).toMatch(/taskBindings\?: ReadonlyArray<TaskBinding>/);
    expect(SIDEBAR()).toMatch(/^\s*taskBindings,$/m);
  });

  it('REPLACES the single running line rather than adding beside it', () => {
    // Both would draw a gear for a running task, and two gears for one run
    // is worse than the problem being fixed.
    expect(SIDEBAR()).toMatch(/taskBindings && taskBindings\.length > 0 \?/);
    // Scoped to the RENDERED element, not the whole file: the comment
    // explaining this replacement necessarily quotes the old string, so a
    // file-wide absence check fails against its own documentation -- the
    // assertion would be measuring the prose rather than the JSX.
    expect(SIDEBAR()).not.toMatch(
      />\s*\n?\s*Task running…\s*\n?\s*<\/Typography>/,
    );
  });

  it('keeps an optimistic fallback for the pre-binding window', () => {
    expect(SIDEBAR()).toMatch(/Task starting…/);
  });

  it('suppresses live gears while the chat itself is streaming', () => {
    expect(SIDEBAR()).toMatch(/suppressLive=\{isStreaming\}/);
  });

  it('treats run statuses as an ordering input, not just membership', () => {
    // A run going running -> held changes no timestamp and no membership,
    // so the sort key must include the statuses themselves.
    const hash = SIDEBAR().match(
      /conversationTaskBindings\.forEach\([\s\S]{0,300}?\}\);/,
    );
    expect(hash).not.toBeNull();
    expect(hash![0]).toMatch(/run_status/);
  });

  it('includes the map in the tree-build deps', () => {
    // Membership, not POSITION.  The original form required
    // conversationTaskBindings to be the LAST entry, so correctly adding
    // runStatusIndex to the same array broke a test that has no opinion
    // about ordering -- an assertion anchored on incidental formatting
    // rather than on the property it guards.
    // Six dep arrays in this file begin '}, [conversations,', so some
    // disambiguation is unavoidable -- but it should be the array's own
    // unique marker, not a prefix of its contents.  The eslint-disable
    // comment is that marker; the previous form keyed on the first three
    // dependency NAMES, which reordering them would have broken.
    const line = SIDEBAR().split('\n').find(
      l => l.includes('eslint-disable-line react-hooks/exhaustive-deps')
        && l.includes('pinnedFolders'),
    );
    expect(line).toBeDefined();
    expect(line!).toMatch(/conversationTaskBindings/);
    // The project-wide index must be here too: the sort hash reads it, and
    // a memo that never re-runs cannot notice its own hash changing.
    expect(line!).toMatch(/runStatusIndex/);
  });
});

describe('the gear component honours the vocabulary', () => {
  it('derives clusters rather than re-deriving statuses', () => {
    expect(GEARS()).toMatch(/statusClusters\(bindings\)/);
  });

  it('animates only what the vocabulary says animates', () => {
    expect(GEARS()).toMatch(/c\.animate\s*\n?\s*\?\s*<SpinningGear/);
  });

  it('renders a static gear otherwise', () => {
    expect(GEARS()).toMatch(/:\s*<SettingsIcon/);
  });

  it('gates the count on showCount, not on a bare number', () => {
    expect(GEARS()).toMatch(/showCount\(c\) &&/);
  });

  it('takes colour from the cluster, never a literal', () => {
    expect(GEARS()).toMatch(/color: c\.color/);
    // A hex literal here would mean a third copy of the palette.
    expect(GEARS()).not.toMatch(/#[0-9a-f]{6}/i);
  });

  it('puts the status word in the accessible name', () => {
    // Colour alone is unreadable to a colour-blind user and invisible to
    // a screen reader, and this row's entire job is signalling state.
    expect(GEARS()).toMatch(/aria-label=/);
    expect(GEARS()).toMatch(/tasks \$\{c\.label\}/);
  });

  it('renders nothing when there is nothing to say', () => {
    expect(GEARS()).toMatch(/if \(visible\.length === 0\) return null/);
  });
});

describe('the tile stops carrying its own palette', () => {
  it('imports the shared maps', () => {
    expect(TILE()).toMatch(
      /import \{ RUN_STATUS_FILL, RUN_STATUS_FG \} from '\.\/runStatusVocabulary'/,
    );
  });

  it('aliases rather than redefining', () => {
    expect(TILE()).toMatch(/const STATUS_COLORS = RUN_STATUS_FILL/);
    expect(TILE()).toMatch(/const STATUS_ICON_COLORS = RUN_STATUS_FG/);
  });

  it('no longer holds a literal palette', () => {
    // The whole point of extracting it: two copies is one drift away from
    // the sidebar and the tile disagreeing about what held looks like.
    expect(TILE()).not.toMatch(/queued: '#7d8590'/);
  });
});
