/**
 * Wiring guards for the Ask block ('ask' card type).
 *
 * The Ask block landed backend-complete but frontend-stub: the block
 * type, its fields, an editor, an add-menu entry, and — the load-bearing
 * gap — any way to ANSWER a holding run were all missing.  These tests
 * pin the seams that connect the (already-working) backend to the UI, so
 * a future edit that drops one of them fails loudly rather than silently
 * re-stranding a run at 'awaiting_input'.
 *
 * Static source assertions for the JOINs (following the convention of
 * proposalSigningNotice.test.ts / launchTitleSeedWiring.test.ts), plus
 * real behavioural checks on the pure helpers (makeBlock, the run-map
 * label) and the API client, which can execute without a DOM.
 */

import * as fs from 'fs';
import * as path from 'path';
import { makeBlock } from '../../../utils/taskCardBlocks';
import { blockLabel } from '../runMapModel';
import { answerTaskRunAsk } from '../../../services/taskRunApi';

const SRC = path.resolve(__dirname, '..', '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(SRC, rel), 'utf8');

// ── Types the backend already emits ────────────────────────────────

describe('the frontend type mirror includes the ask block', () => {
  const TYPES = () => read('types/task_card.ts');

  it("BlockType includes 'ask'", () => {
    // Without this every `block.block_type === 'ask'` comparison is a TS
    // error, so the whole feature is unbuildable.
    expect(TYPES()).toMatch(/BlockType\s*=\s*[^;]*\|\s*'ask'/);
  });

  it('the Block interface declares the ask_* fields', () => {
    const t = TYPES();
    expect(t).toMatch(/ask_question\?:/);
    expect(t).toMatch(/ask_variable\?:/);
    expect(t).toMatch(/ask_choices\?:/);
  });

  it('draft is declared exactly once on TaskCard (the merge dup is gone)', () => {
    // The botched merge tripled `draft?: boolean` on TaskCard and left
    // Create/Update without it.  TS silently dedups, so only a count
    // catches the regression.
    const iface = TYPES().match(/export interface TaskCard \{[\s\S]*?\n\}/)![0];
    const drafts = iface.match(/^\s*draft\?:/gm) ?? [];
    expect(drafts.length).toBe(1);
  });

  it('TaskCardCreate and TaskCardUpdate carry draft', () => {
    const t = TYPES();
    const create = t.match(/export interface TaskCardCreate \{[\s\S]*?\n\}/)![0];
    const update = t.match(/export interface TaskCardUpdate \{[\s\S]*?\n\}/)![0];
    expect(create).toMatch(/draft\?:/);
    expect(update).toMatch(/draft\?:/);
  });

  it('TaskRun declares pending_ask, and PendingAsk exists', () => {
    const t = read('types/task_run.ts');
    expect(t).toMatch(/export interface PendingAsk/);
    expect(t).toMatch(/pending_ask\?:/);
  });
});

// ── The pure helpers actually handle ask ────────────────────────────

describe('block construction and labelling handle ask', () => {
  it("makeBlock('ask') builds an ask leaf with a question field", () => {
    const b = makeBlock('ask');
    expect(b.block_type).toBe('ask');
    expect(b.body).toEqual([]);              // leaf, like state
    expect(b).toHaveProperty('ask_question');
    // Free-text by default: choices null, not [] (a different shape).
    expect(b.ask_choices ?? null).toBeNull();
  });

  it('the run-map labels an unnamed ask block by its question', () => {
    // blockLabel prefers a set name; clear it so the block_type switch
    // (the branch this feature adds) is exercised.
    const b = makeBlock('ask');
    b.name = '';
    b.ask_question = 'Proceed to prod?';
    expect(blockLabel(b)).toContain('Proceed to prod?');
  });

  it('an unnamed, question-less ask still labels legibly, not as raw type', () => {
    const b = makeBlock('ask');
    b.name = '';
    b.ask_question = '';
    expect(blockLabel(b)).not.toBe('ask');
    expect(blockLabel(b).toLowerCase()).toContain('ask');
  });
});

// ── The add-menu and editor dispatcher reach ask ────────────────────

describe('ask is authorable and editable from the UI', () => {
  it("BlockBody's add-menu offers 'ask'", () => {
    // Missing here, an ask block can be built by makeBlock but never
    // added by a user — authorable in theory, unreachable in practice.
    expect(read('components/TaskCard/BlockBody.tsx'))
      .toMatch(/ADD_KINDS[^=]*=\s*\[[^\]]*'ask'/);
  });

  it('the editor dispatcher routes ask to AskBlockEditor', () => {
    const d = read('components/TaskCard/BlockEditor.tsx');
    expect(d).toMatch(/block_type === 'ask'\)\s*return <AskBlockEditor/);
    expect(d).toMatch(/import \{ AskBlockEditor \} from '\.\/AskBlockEditor'/);
  });

  it('an AskBlockEditor module exists and edits the ask fields', () => {
    const e = read('components/TaskCard/AskBlockEditor.tsx');
    expect(e).toMatch(/ask_question/);
    expect(e).toMatch(/ask_variable/);
    expect(e).toMatch(/ask_choices/);
  });
});

// ── The answer seam: client + tile mount ────────────────────────────

describe('a holding run can be answered from the browser', () => {
  it('the tile mounts the answer panel for awaiting_input AND held, gated on an OPEN ask', () => {
    const tile = read('components/TaskCard/TaskCardInlineTile.tsx');
    // 'held' is included: a restart reconciles an unanswered Ask to held
    // with the question kept, and the answer endpoint accepts on held —
    // but before this nothing in the browser could reach it.
    expect(tile).toMatch(
      /\(run\.status === 'awaiting_input' \|\| run\.status === 'held'\)\s*&&\s*run\.pending_ask/,
    );
    // Open means unanswered: on a held run the answer does not clear
    // pending_ask (close_ask is the resumed executor's), so without this
    // the panel would re-ask a settled question.
    expect(tile).toMatch(/!run\.ask_answers\?\.\[run\.pending_ask\.block_id\]/);
    expect(tile).toMatch(/<AskAnswerPanel/);
    expect(tile).toMatch(/import \{ AskAnswerPanel \} from '\.\/AskAnswerPanel'/);
  });

  it('the tile answers the block from pending_ask, not a guessed id', () => {
    const tile = read('components/TaskCard/TaskCardInlineTile.tsx');
    expect(tile).toMatch(/answerTaskRunAsk\(\s*projectId,\s*run\.id,\s*run\.pending_ask\.block_id/);
  });

  it('the API client posts to the ask endpoint with method POST', async () => {
    // Real call against a fetch mock — proves the path and verb, the
    // part a static grep of the client could not certify.
    const calls: any[] = [];
    const orig = global.fetch;
    (global as any).fetch = jest.fn(async (url: string, init: any) => {
      calls.push({ url, init });
      return { ok: true, json: async () => ({ id: 'r1', status: 'running' }) } as any;
    });
    (window as any).__ZIYA_CURRENT_PROJECT_PATH__ = '/tmp/proj';
    try {
      await answerTaskRunAsk('p1', 'r1', 'b-ask-9', { decision: 'reject', answer: 'no' });
    } finally {
      global.fetch = orig;
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain('/task-runs/r1/ask/b-ask-9');
    expect(calls[0].init.method).toBe('POST');
    const body = JSON.parse(calls[0].init.body);
    expect(body.decision).toBe('reject');
    expect(body.answer).toBe('no');
  });
});
