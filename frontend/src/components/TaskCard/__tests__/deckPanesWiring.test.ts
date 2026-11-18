/**
 * Static wiring guards for the Task Cards dialog's panes and run pane.
 *
 * Source-text checks, like the other *Wiring suites here: the failures
 * this guards against are a component that exists and is never mounted,
 * a collapse with no way back, and a run pane that redraws the tile's
 * logic and then disagrees with it.
 */

import * as fs from 'fs';
import * as path from 'path';

const DIR = path.join(__dirname, '..');
const read = (f: string) => fs.readFileSync(path.join(DIR, f), 'utf8');

const library = () => read('TaskCardsLibrary.tsx');
const pane = () => read('DeckRunPane.tsx');
const chrome = () => read('DeckPaneChrome.tsx');
const entriesHook = () => read('useDeckRunEntries.ts');
const outline = () => read('BlockOutline.tsx');
const editor = () => read('TaskCardEditor.tsx');
const css = () => read('task-deck.css');

describe('the dialog mounts the run pane for a card that has runs', () => {
  it('derives the entries from own runs AND callee context', () => {
    expect(library()).toMatch(
      /useDeckRunEntries\(projectId, selectedId, draftRuns, runIndex\)/);
    expect(entriesHook()).toMatch(/getCalleeContext/);
    expect(entriesHook()).toMatch(/buildRunEntries/);
  });

  it('has no run pane, switch or strip for a card without runs', () => {
    const lib = library();
    expect(lib).toMatch(/const hasRunPane = !!draft && runEntries\.length > 0/);
    expect(lib).toMatch(/effectivePaneView\(layout\.view, hasRunPane\)/);
    expect(lib).toMatch(/view === 'cards' && hasRunPane && \(\s*<Segmented/);
  });

  it('mounts the pane with what it needs to act on a run', () => {
    const lib = library();
    const mount = lib.slice(lib.indexOf('<DeckRunPane'), lib.indexOf('<DeckRunPane') + 500);
    expect(mount).toMatch(/entries=\{runEntries\}/);
    expect(mount).toMatch(/cardRoot=\{draft\?\.root\}/);
    expect(mount).toMatch(/onRunsChanged=\{refreshRuns\}/);
    expect(mount).toMatch(/onOpenConversation=\{handleOpenConversation\}/);
  });

  it('keeps the Cards / Schedules switch', () => {
    expect(library()).toMatch(/data-testid="deck-view-switch"/);
  });
});

describe('every collapse has a way back', () => {
  it('offers the three-way view switch in the title', () => {
    const lib = library();
    expect(lib).toMatch(/data-testid="deck-pane-switch"/);
    for (const v of ['definition', 'split', 'run']) {
      expect(lib).toMatch(new RegExp(`value: '${v}'`));
    }
  });

  it('each pane has a collapse tab and a strip that reopens it', () => {
    const lib = library();
    // Card list.
    expect(lib).toMatch(/<DeckPaneTab label="CARDS"[\s\S]{0,120}setListCollapsed\(true\)/);
    expect(lib).toMatch(/<DeckPaneStrip label="CARDS"[\s\S]{0,120}setListCollapsed\(false\)/);
    // Definition.
    expect(lib).toMatch(/<DeckPaneTab label="DEFINITION"[\s\S]{0,120}setPaneView\('run'\)/);
    expect(lib).toMatch(/<DeckPaneStrip label="DEFINITION"[\s\S]{0,120}setPaneView\('split'\)/);
    // Run: the tab lives in the pane's own header.
    expect(lib).toMatch(/onCollapse=\{\(\) => setPaneView\('definition'\)\}/);
    expect(pane()).toMatch(/<DeckPaneTab label="RUN" side="right" onCollapse=\{onCollapse\}/);
    expect(lib).toMatch(/RUN · \$\{leadRunStatus\.toUpperCase\(\)\}[\s\S]{0,260}setPaneView\('split'\)/);
  });

  it('the tab and the strip carry the same label', () => {
    const src = chrome();
    expect(src).toMatch(/`« \$\{label\}`/);
    expect(src).toMatch(/`\$\{label\} »`/);
    expect(src).toMatch(/tc-deck__strip-label">\{label\}/);
  });

  it('a collapsed run strip still says the run is held', () => {
    expect(library()).toMatch(/tone=\{leadRunStatus === 'held' \? 'held' : 'plain'\}/);
  });

  it('the divider settles through the snap rule and resets on double-click', () => {
    const lib = library();
    expect(lib).toMatch(/settleDivider\(d\.raw, d\.available, prev\.runWidth\)/);
    expect(lib).toMatch(/onDoubleClick=\{\(\) => setLayout\(prev => \(\s*\{ \.\.\.prev, runWidth: Math\.round\(paneAvailable\(\) \/ 2\) \}\)\)\}/);
    // Pointer deltas go through the UI-zoom conversion like the other drags.
    expect(lib).toMatch(/toLayoutPx\(e\.clientX - d\.startX\)/);
  });

  it('remembers the layout per browser', () => {
    const lib = library();
    expect(lib).toMatch(/useState<DeckLayout>\(\(\) => loadDeckLayout\(\)\)/);
    expect(lib).toMatch(/saveDeckLayout\(layout\)/);
  });

  it('opens the run for a card that wants eyes, once per card', () => {
    const lib = library();
    expect(lib).toMatch(/autoOpenedFor\.current === selectedId\) return/);
    expect(lib).toMatch(/entriesWantEyes\(runEntries\)/);
    expect(lib).toMatch(/viewOnCardOpen\(prev\.view, true\)/);
  });
});

describe('the run pane shares the tile\'s machinery', () => {
  it('streams by run id through the same hook', () => {
    expect(pane()).toMatch(/useTaskRunStream\(projectId, runId \?\? undefined\)/);
  });

  it('derives controls and the recovery target from the shared modules', () => {
    const src = pane();
    expect(src).toMatch(/deriveRunControls\(run\)/);
    expect(src).toMatch(/recoveryTarget\(run\)/);
    expect(src).toMatch(/<RunRecoveryBanner/);
    expect(src).toMatch(/<IterationDotStrip/);
    expect(src).toMatch(/<SteerComposer/);
  });

  it('does not restate which statuses are terminal or live', () => {
    const src = pane();
    expect(src).not.toMatch(/\['done',\s*'partial'/);
    expect(src).not.toMatch(/'queued'\s*,\s*'running'\s*,\s*'paused'/);
  });

  it('can continue or retry a stopped run without leaving the dialog', () => {
    const src = pane();
    expect(src).toMatch(/resumeRunFromBlock\(projectId, run\.id, blockId, mode\)/);
    expect(src).toMatch(/resumeFrom\(id, 'retry'\)/);
    expect(src).toMatch(/resumeFrom\(id, 'continue'\)/);
    // The new attempt is shown at once and announced to the conversation.
    expect(src).toMatch(/selectRun\(res\.run\.id\)/);
    expect(src).toMatch(/TASK_BINDING_EVENT/);
  });

  it('confirms before a retry that discards a recorded result', () => {
    const src = pane();
    expect(src).toMatch(/mode === 'retry' && st\?\.artifact && st\.block_type !== 'parallel'/);
    expect(src).toMatch(/Modal\.confirm/);
  });

  it('drives a live run: pause, resume, step, cancel', () => {
    const src = pane();
    for (const fn of ['pauseTaskRun', 'resumeTaskRun', 'stepTaskRun', 'cancelTaskRun']) {
      expect(src).toMatch(new RegExp(`${fn}\\(projectId, run!\\.id`));
    }
  });

  it('offers the note composer only when a block can receive one', () => {
    // The steer endpoint 409s on a run that is not executing, so the
    // composer must not be offered there.
    const src = pane();
    expect(src).toMatch(/if \(!isLive \|\| !run\) return \[\]/);
    expect(src).toMatch(/steerTargets\.length > 0 \?/);
    expect(src).toMatch(/there is no block to send a note to/);
  });

  it('shows a callee run through this card\'s own recorded tree', () => {
    // The run's card_snapshot is the CALLER's card.
    expect(pane()).toMatch(
      /entry\?\.calleeRoot \?\? run\?\.card_snapshot\?\.root \?\? cardRoot/);
  });

  it('resets what is open when the card changes', () => {
    const src = pane();
    const effect = src.slice(src.indexOf('setRunId(defaultEntryId(entries));'));
    expect(effect.slice(0, 220)).toMatch(/setFocus\(null\)/);
    expect(effect.slice(0, 220)).toMatch(/setUserFocused\(false\)/);
  });
});

describe('the definition outline carries the run\'s markers', () => {
  it('the run pane reports markers and clears them on unmount', () => {
    const src = pane();
    expect(src).toMatch(/outlineMarkers\(sections, chain\)/);
    expect(src).toMatch(/deriveHoldChain\(run, root\)/);
    expect(src).toMatch(/useEffect\(\(\) => \(\) => onMarkersChange\?\.\(null\)/);
  });

  it('the dialog routes them into the editor\'s outline', () => {
    const lib = library();
    expect(lib).toMatch(/onMarkersChange=\{setOutlineMarkersById\}/);
    expect(lib).toMatch(/outlineMarkerOf=\{hasRunPane \? markerOf : undefined\}/);
    expect(editor()).toMatch(/markerOf=\{outlineMarkerOf\}/);
  });

  it('the outline draws the glyph and a badge in edit mode', () => {
    const src = outline();
    expect(src).toMatch(/mode === 'edit' && selectable\s*\? \(markerOf\?\.\(id\) \?\? null\)/);
    expect(src).toMatch(/: \(marker\?\.status \?\? null\)/);
    expect(src).toMatch(/tc-outline__mark tc-outline__mark--\$\{marker\.badge\}/);
    expect(src).toMatch(/held: 'HELD HERE'/);
    expect(css()).toMatch(/\.tc-outline__mark--held\s*\{/);
  });

  it('the run pane stays mounted while collapsed, so markers survive', () => {
    const lib = library();
    expect(lib).toMatch(/paneView === 'definition'\s*\? \{ display: 'none' \}/);
    expect(lib).not.toMatch(/hasRunPane && paneView !== 'definition' && \(/);
  });

  it('the second copy of the tree and the old run list are gone', () => {
    const lib = library();
    expect(lib).not.toMatch(/CalleeHoldPanel/);
    expect(lib).not.toMatch(/DeckRunList/);
  });
});

describe('selecting a block in one pane selects it in the other', () => {
  it('outline -> run: opens and scrolls to the section', () => {
    expect(library()).toMatch(/onOutlineSelect=\{hasRunPane \? handleOutlineSelect : undefined\}/);
    expect(library()).toMatch(/revealBlock=\{revealBlock\}/);
    const src = pane();
    expect(src).toMatch(/if \(!revealBlock \|\| !run\) return/);
    expect(src).toMatch(/scrollIntoView/);
    expect(src).toMatch(/data-block-id=\{s\.block\.id\}/);
  });

  it('run -> outline: a section click selects its block', () => {
    expect(library()).toMatch(/onSectionSelect=\{handleSectionSelect\}/);
    expect(library()).toMatch(/focusBlockRequest=\{editorFocus\}/);
    expect(pane()).toMatch(/onSectionSelect\?\.\(s\.block\.id\)/);
    expect(editor()).toMatch(/if \(focusBlockRequest\?\.blockId\) setSelectedId\(focusBlockRequest\.blockId\)/);
  });

  it('a revealed section is not reported back, so the panes do not ping-pong', () => {
    const src = pane();
    const reveal = src.slice(src.indexOf('if (!revealBlock || !run) return'));
    expect(reveal.slice(0, 500)).not.toMatch(/onSectionSelect/);
  });
});

describe('card actions live in the title bar', () => {
  it('Save, Launch and a more-menu sit beside the card name', () => {
    const lib = library();
    const title = lib.slice(lib.indexOf('className="tc-deck__title"'), lib.indexOf('open={visible}'));
    expect(title).toMatch(/data-testid="deck-card-actions"/);
    expect(title).toMatch(/onClick=\{handleSave\}/);
    expect(title).toMatch(/onClick=\{chatId \? handleLaunchCurrent : handleLaunchNew\}/);
    expect(title).toMatch(/<Dropdown menu=\{launchMenu\}/);
    expect(title).toMatch(/<Dropdown menu=\{moreMenu\}/);
    expect(title).toMatch(/\{statusTag\}/);
  });

  it('every target of the old button row is still reachable', () => {
    const lib = library();
    for (const k of ['launch-current', 'launch-new', 'stage-current', 'stage-new',
      'add-to-deck', 'duplicate', 'export', 'delete']) {
      expect(lib).toMatch(new RegExp(`key: '${k}'`));
    }
    expect(lib).toMatch(/key === 'delete'[\s\S]{0,120}Modal\.confirm/);
  });

  it('the dialog sits near the top of the viewport', () => {
    expect(library()).toMatch(/style=\{\{ top: DIALOG_TOP \}\}/);
  });

  it('navigating to a run\'s conversation closes the dialog', () => {
    // Navigating behind a modal that still covers the conversation is
    // indistinguishable from the click having done nothing.
    expect(library()).toMatch(/const handleOpenConversation[\s\S]{0,160}onClose\(\)/);
  });
});

describe('an Ask can be answered from the run pane', () => {
  it('mounts the answer panel under the tile\'s own gate', () => {
    const src = pane();
    expect(src).toMatch(/<AskAnswerPanel/);
    expect(src).toMatch(/run\.status === 'awaiting_input' \|\| run\.status === 'held'/);
    expect(src).toMatch(/!run\.ask_answers\?\.\[run\.pending_ask\.block_id\]/);
    expect(src).toMatch(/answerTaskRunAsk\(\s*projectId, run\.id, run\.pending_ask!\.block_id/);
  });
});
