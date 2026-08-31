/**
 * The AI-authored proposal panel must tell the user HOW to sign, in place.
 *
 * The defect this pins (observed 2026-08-22): the panel's "Requires signing"
 * notice said *Use **Save to deck** to get the ziya-approve command for each*
 * — but
 *   (a) once the card WAS saved the button relabels to "Update in deck", so
 *       the notice named a control that was no longer on screen;
 *   (b) the panel never rendered `signCommand` at all, so even after saving
 *       no command appeared — the instruction dead-ended into a second
 *       surface (the Task Cards deck) that the panel did not link to;
 *   (c) the panel only ever consulted the *preview* endpoint, which returns
 *       `signCommand: ""` by contract. That is not merely cosmetic: the
 *       by-id scope-status endpoint is what STAGES the decrypted scope the
 *       out-of-process signer needs to recompute the hash, so a flow that
 *       never calls it leaves the signer without its input.
 *
 * Static assertions rather than a mount: every part of this defect was
 * wiring — a field present in the API contract and rendered by one surface
 * (TaskCardEditor) but never read by another, and a fetch that was never
 * made. A render test would need a project context, a message-id context
 * and two fetch mocks and would still not pin "the by-id endpoint gets
 * called after save".
 */

import * as fs from 'fs';
import * as path from 'path';

const COMPONENTS = path.resolve(__dirname, '..');
const readComponent = (f: string) =>
  fs.readFileSync(path.join(COMPONENTS, f), 'utf8');

const PROPOSAL = readComponent('TaskCardLaunchButton.tsx');
const SHELL = readComponent('ShellConfigModal.tsx');
const API = fs.readFileSync(
  path.resolve(COMPONENTS, '..', 'services', 'taskCardApi.ts'), 'utf8');
const CARDS_API_PY = fs.readFileSync(
  path.resolve(COMPONENTS, '..', '..', '..', 'app', 'api', 'task_cards.py'), 'utf8');

describe('the proposal panel obtains real sign commands', () => {
  it('calls the by-id scope-status endpoint, not only the preview one', () => {
    // scopePreview returns signCommand: "" by contract, so the preview
    // reading alone can never produce a runnable command.
    expect(PROPOSAL).toContain('taskCardApi.scopeStatus(');
  });

  it('does so after a successful save, when block ids exist', () => {
    // Approvals key on persisted block ids; before create() assigns them
    // there is nothing to sign against.
    expect(PROPOSAL).toMatch(/handleSaveToDeck[\s\S]{0,1600}taskCardApi\.scopeStatus\(/);
  });

  it('prefers the saved reading over the preview reading', () => {
    expect(PROPOSAL).toMatch(/savedScope/);
    expect(PROPOSAL).toMatch(/savedScope\s*\?\?/);
  });
});

describe('the command is displayed in place, copyably', () => {
  it('renders the server-provided signCommand', () => {
    expect(PROPOSAL).toContain('signCommand');
  });

  it('uses the shared copyable CommandBlock, not bare inline <code>', () => {
    expect(PROPOSAL).toMatch(/CommandBlock/);
    expect(PROPOSAL).toMatch(/import\s+\{?\s*CommandBlock/);
  });

  it('guards the empty-string case so no blank box renders', () => {
    // The preview endpoint's "" would otherwise paint an empty monospace
    // box that reads as a command the UI failed to load.
    expect(PROPOSAL).toMatch(/signCommand\s*&&/);
  });

  it('surfaces the single sign-all command when several blocks need it', () => {
    // Three separate sudo lines is the wall-of-text the user objected to;
    // ziya-approve --all signs every unapproved block in one invocation.
    expect(PROPOSAL).toContain('signAllCommand');
    expect(API).toContain('signAllCommand');
  });

  it('the sign-all command syntax is minted server-side', () => {
    // The CLI's flag vocabulary must not be duplicated in the frontend:
    // a client-built string silently rots when the CLI changes.
    expect(CARDS_API_PY).toContain('signAllCommand');
    expect(PROPOSAL).not.toMatch(/['"`]sudo ziya-approve --task/);
    expect(PROPOSAL).not.toContain('--all --project');
  });
});

describe('the notice cannot name a control that is not on screen', () => {
  it('button label and notice text come from ONE binding', () => {
    // "Update in deck" vs "Save to deck" diverged because the label was
    // written twice — once as a conditional on the button, once as a
    // hardcoded literal in the prose.
    expect(PROPOSAL).toMatch(/const saveLabel\s*=/);
    expect(PROPOSAL).toMatch(/\{saveLabel\}/);
  });

  it('the notice no longer hardcodes the unsaved-state label', () => {
    const notice = PROPOSAL.slice(PROPOSAL.indexOf('Requires signing.'));
    expect(notice).not.toMatch(/<strong>Save to deck<\/strong>\s*to get/);
  });
});

describe('CommandBlock is defined once for the whole app', () => {
  it('lives in its own module', () => {
    expect(fs.existsSync(path.join(COMPONENTS, 'CommandBlock.tsx'))).toBe(true);
  });

  it('ShellConfigModal consumes it rather than keeping a twin', () => {
    // Two copies drift: the copy-button behaviour and the theme-safe
    // rgba palette would have to be fixed twice.
    expect(SHELL).not.toMatch(/const CmdBlock\s*:/);
    expect(SHELL).toMatch(/import\s+\{?\s*CommandBlock/);
  });
});
