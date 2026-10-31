/**
 * Structural guard: muting a message stamps a new _version on the record.
 *
 * WHY THIS EXISTS
 *
 * toggleMessageMute used to be the one ChatContext mutation that did not
 * bump _version (a stale comment blamed scroll resets).  Every place that
 * decides whose copy of a conversation wins is version-gated:
 *
 *   - app/api/chats.py bulk-sync: a push whose _version is below the
 *     record's takes the "older push" branch, where matched messages keep
 *     the on-disk copy.  The server's version routinely moves past the
 *     browser's after a streamed turn (the merge differs once the empty
 *     assistant placeholder is stripped), so a version-less mute pushed in
 *     that window was dropped on the server.
 *   - ChatContext's sync reconcile: an in-memory copy beats a fetched
 *     server/IDB copy only by message count or a STRICTLY newer _version,
 *     so a muted-but-same-version copy lost the tie and the unmuted copy
 *     came back.
 *
 * Observed: a 225k-token response muted in the parent re-entered the request
 * of a fork taken a few turns later (prompt rejected at >1M tokens) while the
 * bubble still rendered as muted.
 *
 * WHY STATIC: same reasoning as removeMessagePersistsWiring.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (...p: string[]) => fs.readFileSync(path.resolve(__dirname, '..', ...p), 'utf8');
const stripComments = (s: string) => s.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

const CTX = stripComments(read('ChatContext.tsx'));

function callbackBody(src: string, name: string): string {
  const start = src.indexOf(`const ${name} = useCallback(`);
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf('}, [', start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

describe('message mute is a versioned change', () => {
  it('ChatContext.toggleMessageMute flips muted, bumps _version and queueSaves it', () => {
    const body = callbackBody(CTX, 'toggleMessageMute');
    expect(body).toMatch(/muted:\s*!updatedMessages\[messageIndex\]\.muted/);
    // The record returned for the toggled conversation carries a fresh version.
    expect(body).toMatch(/messages:\s*updatedMessages,\s*_version:\s*Date\.now\(\)/);
    expect(body).toMatch(/queueSave\(updated,\s*\{\s*changedIds:\s*\[conversationId\]\s*\}\)/);
  });

  it('no mutation in ChatContext opts out of _version on purpose any more', () => {
    expect(CTX).not.toMatch(/Don't update _version/);
  });
});
