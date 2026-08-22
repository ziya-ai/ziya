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
  it('CmdBlock exists with copy affordance', () => {
    expect(SRC).toMatch(/const CmdBlock/);
    expect(SRC).toMatch(/CopyOutlined/);
  });

  it('both signing ceremonies use it', () => {
    expect(SRC).toMatch(/CmdBlock cmd="sudo ziya-approve"/);
    expect(SRC).toMatch(/CmdBlock cmd="sudo ziya-approve --session"/);
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
    const bannerIdx = SRC.indexOf('Temporary grant active');
    expect(bannerIdx).toBeGreaterThan(-1);
    const vicinity = SRC.slice(bannerIdx, bannerIdx + 1200);
    expect(vicinity).toMatch(/void/i);
    expect(vicinity).toMatch(/restart/i);
  });
});
