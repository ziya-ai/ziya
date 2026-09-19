/**
 * Static wiring guards for how MCPRegistryModal reports a failed or
 * warning-laden registry install (follow-up to the 2026-09-17
 * kuiper-minerva-mcp install).
 *
 * The registry CLI printed a clear, multi-line diagnosis; the modal showed
 * `message.error(\`Installation failed: ${...}\`)` — a toast that truncates
 * and disappears. Warnings on a successful install were not rendered at all.
 *
 * Source-text assertions, deliberately: each property is about WHICH
 * surface the text goes to, which a mount test would not distinguish.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'MCPRegistryModal.tsx'),
  'utf8'
);

const handlerBody = (name: string): string => {
  const start = SRC.indexOf(`const ${name}`);
  expect(start).toBeGreaterThan(-1);
  const rest = SRC.slice(start + 1);
  const next = rest.search(/\n    const [a-zA-Z]+ = /);
  return next === -1 ? rest : rest.slice(0, next);
};

describe('install failure is a durable dialog, not a toast', () => {
  it('the toast forms are gone from installService', () => {
    const body = handlerBody('installService');
    expect(body).not.toMatch(/message\.error\(`Installation failed/);
    expect(body).not.toMatch(/message\.error\('Installation failed'\)/);
  });

  it('every failure branch routes through showInstallFailure', () => {
    const body = handlerBody('installService');
    // non-success 200 body, non-2xx response, and thrown fetch error
    expect(body.match(/showInstallFailure\(/g)?.length).toBe(3);
  });

  it('the dialog renders the CLI text as a block and keeps hint + log tail', () => {
    const body = handlerBody('showInstallFailure');
    expect(body).toMatch(/Modal\.error\(/);
    expect(body).toMatch(/<pre style=\{installTextStyle\}>\{body\}<\/pre>/);
    expect(body).toMatch(/rec\.hint/);
    expect(body).toMatch(/Server log tail/);
    // states the outcome: nothing persisted
    expect(body).toMatch(/Nothing was added to your MCP config/);
  });

  it('accepts both the structured 400 detail and a flat string', () => {
    const body = handlerBody('showInstallFailure');
    expect(body).toMatch(/typeof payload === 'object'/);
    expect(body).toMatch(/String\(payload/);
  });
});

describe('a tolerated install-step failure is surfaced on success', () => {
  it('warnings[] on a success response opens a warning dialog', () => {
    const body = handlerBody('installService');
    expect(body).toMatch(/result\.warnings/);
    expect(body).toMatch(/showInstallWarnings\(/);
  });

  it('the warning dialog says the server started and shows each warning verbatim', () => {
    const body = handlerBody('showInstallWarnings');
    expect(body).toMatch(/Modal\.warning\(/);
    expect(body).toMatch(/The server started/);
    expect(body).toMatch(/warnings\.map\(/);
  });
});
