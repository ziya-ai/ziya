/**
 * Static wiring guards for the shell-config escalation UX
 * (follow-up to the 2026-08-22 ffmpeg session-grant incident).
 *
 * The reported failure was not logic but COMMUNICATION: (1) the footer's
 * "Apply (this session)" read as a plain "Apply", so the user ran the sudo
 * ceremony without registering the grant was temporary; (2) Save closed the
 * modal unconditionally — even on failure — destroying the only surface that
 * carries the "now sign it" instructions, so a saved-but-unsigned escalation
 * showed no invitation to sign until the modal was reopened; (3) the sudo
 * commands were inline <code> fragments in prose rather than distinct
 * copyable blocks.
 *
 * These read source text rather than mounting, deliberately: each defect is
 * a wiring/copy property a mount test would not exercise (the component
 * renders fine either way — it just never says the right thing).  This file
 * is the acceptance gate for the ShellConfigModal.tsx patch; it fails
 * against the pre-patch source by design.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'ShellConfigModal.tsx'),
  'utf8'
);
const CMD_BLOCK_SRC = fs.readFileSync(
  path.join(__dirname, '..', 'CommandBlock.tsx'),
  'utf8'
);

// Source between `const <name>` and the next top-level handler, so an
// assertion about one dialog's content cannot be satisfied by text that
// lives in a different banner or handler.
const handlerBody = (name: string): string => {
  const start = SRC.indexOf(`const ${name}`);
  expect(start).toBeGreaterThan(-1);
  const rest = SRC.slice(start + 1);
  const next = rest.search(/\n    const [a-zA-Z]+ = /);
  return next === -1 ? rest : rest.slice(0, next);
};

describe('session-only path is unmistakably temporary', () => {
  it('footer routes through an interstitial, not straight to staging', () => {
    expect(SRC).toMatch(/const confirmSessionStage/);
    expect(SRC).toMatch(/onClick=\{confirmSessionStage\}/);
    // The raw staging handler must no longer be a direct onClick target —
    // it is reachable only via the interstitial's onOk.
    expect(SRC).not.toMatch(/onClick=\{requestSessionGrant\}/);
  });

  it('interstitial names the temporary/persistent fork', () => {
    expect(SRC).toMatch(/Temporary grant — this session only/);
    expect(SRC).toMatch(/survive restarts/i);
  });

  it('staged banner is warning-styled and says temporary, not "ephemeral"', () => {
    expect(SRC).toMatch(/Temporary grant staged — this session only/);
    // jargon that proved skimmable is gone from the banner heading
    expect(SRC).not.toMatch(/Ephemeral escalation staged for this session/);
  });

  it('staged banner states that Save discards the staged request', () => {
    expect(SRC).toMatch(/discards this staged request/);
  });
});

describe('Save is labeled persistent and explains signing', () => {
  it('the primary button says what tier it writes to', () => {
    expect(SRC).toMatch(/Save \(persistent\)/);
  });

  it('save keeps the modal open when a signature is still needed', () => {
    expect(SRC).toMatch(/needsSignature/);
    expect(SRC).toMatch(/NOT active/);
  });

  it('the unconditional close after save — including on FAILURE — is gone', () => {
    expect(SRC).not.toMatch(
      /Failed to update shell configuration'\);\s*\}\s*onClose\(\);/
    );
  });

  it('fetchShellConfig returns the fresh config so save can branch on it', () => {
    expect(SRC).toMatch(/Promise<ShellConfig \| null>/);
  });
});

describe('terminal commands are copyable blocks, not inline prose', () => {
  // CmdBlock was extracted to ./CommandBlock.tsx so the task-card proposal
  // panel could share it; the copy affordance now lives there.
  it('CommandBlock is imported and carries the copy affordance', () => {
    expect(SRC).toMatch(/import \{ CommandBlock \} from '\.\/CommandBlock'/);
    expect(CMD_BLOCK_SRC).toMatch(/CopyOutlined/);
  });

  it('both signing ceremonies use it', () => {
    expect(SRC).toMatch(/CommandBlock cmd="sudo ziya-approve"/);
    expect(SRC).toMatch(/CommandBlock cmd="sudo ziya-approve --session"/);
  });
});

describe('a live temporary grant is visibly indicated', () => {
  // The residual gap from the first UX pass: after "Apply now" succeeded,
  // the staged banner disappeared and the modal looked identical to an
  // unescalated one — nothing said a temporary grant was live or that a
  // server restart would silently void it.

  it('the config interface carries the applied-grant state', () => {
    expect(SRC).toMatch(/sessionGrant\?:/);
  });

  it('an active-grant banner is rendered from it', () => {
    expect(SRC).toMatch(/config\.sessionGrant\?\.active/);
    expect(SRC).toMatch(/Temporary grant active/);
  });

  it('the banner shows WHICH privileges are temporarily granted', () => {
    expect(SRC).toMatch(/config\.sessionGrant\.delta/);
  });

  it('the banner states the void-on-restart lifetime', () => {
    // must mention that a server restart voids it, near the active banner
    // Anchor on the JSX prop, not the first occurrence: the phrase also
    // appears in an interface comment ~500 lines earlier.
    const bannerIdx = SRC.indexOf('message="Temporary grant active');
    expect(bannerIdx).toBeGreaterThan(-1);
    const vicinity = SRC.slice(bannerIdx, bannerIdx + 1200);
    expect(vicinity).toMatch(/void/i);
    expect(vicinity).toMatch(/restart/i);
  });
});

describe('staging hands off to the terminal with an explicit acknowledgement', () => {
  // The staged banner persisted, but the immediate feedback after staging
  // was a transient toast carrying the only statement of the next steps
  // ("Run `sudo ziya-approve --session`, then click Apply") — gone in
  // seconds, and the user is by then looking at a terminal, not at Ziya.
  // Staging is inert until an out-of-process sudo command runs, so the
  // handoff has to be a dialog the user must click through.

  it('the transient staged toast is gone', () => {
    expect(SRC).not.toMatch(/Staged for this session\. Run/);
    // requestSessionGrant no longer reports success via message.success
    expect(handlerBody('requestSessionGrant')).not.toMatch(/message\.success/);
  });

  it('staging success opens the acknowledgement dialog', () => {
    expect(SRC).toMatch(/const acknowledgeSessionStaged/);
    const body = handlerBody('requestSessionGrant');
    // The delta setter may sit between the two; what matters is that the
    // acknowledgement follows staging inside the success branch.
    expect(body).toMatch(/setSessionStaged\(true\);[\s\S]{0,120}?acknowledgeSessionStaged\(\);/);
  });

  it('the dialog is a blocking Modal with the command as a copyable block', () => {
    const body = handlerBody('acknowledgeSessionStaged');
    expect(body).toMatch(/Modal\.confirm\(/);
    expect(body).toMatch(/CommandBlock cmd="sudo ziya-approve --session"/);
    // numbered steps, and it names the button the user must come back to
    expect(body).toMatch(/<ol/);
    expect(body).toMatch(/Apply now/);
    // says nothing is active yet
    expect(body).toMatch(/Nothing has changed yet/);
  });

  it('the dialog OK completes the round trip via applySessionGrant', () => {
    const body = handlerBody('acknowledgeSessionStaged');
    expect(body).toMatch(/onOk: \(\) => applySessionGrant\(\)/);
  });

  it('an apply refusal is durable and repeats the command, not a toast', () => {
    // Direct consequence of offering Apply now from the dialog: clicking it
    // before signing must not fail into a toast that vanishes the same way.
    const body = handlerBody('applySessionGrant');
    expect(body).toMatch(/Modal\.error\(/);
    expect(body).toMatch(/CommandBlock cmd="sudo ziya-approve --session"/);
    expect(body).not.toMatch(/message\.error\(result\.message/);
  });
});

// 2026-09-19: the persistent "Unsigned privilege escalation" banner itemises
// its pendingDelta; the staged "Temporary grant staged" banner listed nothing,
// so when both were on screen there was no way to tell the two requests apart.
describe('staged banner lists its delta like the persistent banner does', () => {
  // The staged Alert's JSX, scoped by its heading so assertions cannot be
  // satisfied by the persistent banner's identical-looking field list.
  const stagedBanner = (): string => {
    const start = SRC.indexOf('message="Temporary grant staged — this session only"');
    expect(start).toBeGreaterThan(-1);
    const rest = SRC.slice(start);
    const end = rest.indexOf('message="Temporary grant active');
    expect(end).toBeGreaterThan(-1);
    return rest.slice(0, end);
  };

  it('the ShellConfig contract carries sessionPendingDelta', () => {
    expect(SRC).toMatch(/sessionPendingDelta\?: Record<string, string\[\]>/);
  });

  it('the delta is hydrated from the GET and from the stage response', () => {
    // GET: survives modal close/reopen like sessionStaged itself
    const fetchBody = handlerBody('fetchShellConfig');
    expect(fetchBody).toMatch(/setSessionStaged\(!!data\.sessionPending\);\s*setSessionStagedDelta\(data\.sessionPendingDelta \?\? \{\}\)/);
    // stage: from the POST response, NOT a re-fetch (which would reset the
    // modal's edited fields to the persisted config)
    const stageBody = handlerBody('requestSessionGrant');
    expect(stageBody).toMatch(/setSessionStagedDelta\(result\.pendingDelta \?\? \{\}\)/);
    expect(stageBody).not.toMatch(/fetchShellConfig\(\)/);
    // discard clears it
    expect(handlerBody('discardSessionGrant')).toMatch(/setSessionStagedDelta\(\{\}\)/);
  });

  it('the staged banner renders the delta entries, one line per field', () => {
    const banner = stagedBanner();
    expect(banner).toMatch(/Object\.entries\(sessionStagedDelta\)\.map\(\(\[field, vals\]\)/);
    expect(banner).toMatch(/\{field\}: \{vals\.join\(', '\)\}/);
  });

  it('an empty delta says so instead of listing nothing', () => {
    expect(stagedBanner()).toMatch(/nothing beyond the default\s+floor/);
  });
});