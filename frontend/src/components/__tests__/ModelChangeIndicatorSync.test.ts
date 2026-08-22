/**
 * Regression coverage for model-change indicator synchronization.
 *
 * `modelChanged` is the committed-change event used to append the conversation
 * notice. The lower-left indicator must consume the same event rather than wait
 * for the later settings-save event, which may never arrive if Apply is aborted.
 */
import * as fs from 'fs';
import * as path from 'path';

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../FolderTree.tsx'),
  'utf-8',
);

describe('FolderTree model indicator synchronization', () => {
  it('refreshes and cleans up on the committed model-change event', () => {
    expect(SOURCE).toContain(
      "window.addEventListener('modelChanged', handleModelChange)",
    );
    expect(SOURCE).toContain(
      "window.removeEventListener('modelChanged', handleModelChange)",
    );
  });

  it('still refreshes for model settings-only changes', () => {
    expect(SOURCE).toContain(
      "window.addEventListener('modelSettingsChanged', handleModelChange)",
    );
  });
});
