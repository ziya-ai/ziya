/**
 * /api/accurate-token-count must be anchored to the open project, and an
 * error result must not be stored as a real count.
 *
 * Observed 2026-10-03: the request carried no X-Project-Root, so the server
 * resolved the relative paths against its launch directory and answered
 * {accurate_count: 0, error: 'File not found'} for every file.  FolderContext
 * stored those as count 0.  The file row then fell back to the estimate (and
 * still showed a ✓), while the folder rollup trusted the 0, so every folder
 * read "0/N" while its checked files showed real numbers.
 *
 * Source-level because the seam is the request shape and the result filter;
 * both are inline in a large provider.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '..', 'FolderContext.tsx'), 'utf8');

const block = (() => {
  // From the log line just above the request: the project path is read
  // into a const before the fetch call, so the slice must include it.
  const start = SRC.indexOf('Making API request for accurate token counts');
  expect(start).toBeGreaterThan(0);
  return SRC.slice(start, SRC.indexOf('accurateTokenCountsUpdated', start));
})();

describe('every accurate-token-count call is project-scoped', () => {
  // measureForAutoAdd (the auto-add limit check) had its own fetch with the
  // same missing header.  Every result came back "File not found", which it
  // treats as unmeasured, so outside the launch project every auto-add was
  // held back.
  it('each fetch of the endpoint sends X-Project-Root', () => {
    const calls = SRC.split("fetch('/api/accurate-token-count'").slice(1);
    expect(calls.length).toBe(2);
    for (const c of calls) {
      const body = c.slice(0, c.indexOf('});'));
      expect(body).toMatch(/'X-Project-Root'/);
    }
  });
});

describe('accurate token count request', () => {
  it('sends the current project root', () => {
    expect(block).toMatch(/'X-Project-Root'/);
    expect(block).toMatch(/__ZIYA_CURRENT_PROJECT_PATH__/);
  });

  it('does not store an error result as a count', () => {
    expect(block).toMatch(/result\.error/);
    // Positive: a successful result is still stored.
    expect(block).toMatch(/updated\[path\] = \{\s*count: result\.accurate_count/);
  });
});
