/**
 * Seam coverage for the cross-session model-sync chain.  The sync only
 * works when every hop is connected:
 *
 *   server header → chatApi reads it → service reconciles →
 *   FolderTree starts the service and renders the pulse.
 *
 * Each assertion here guards a hop that could silently disconnect
 * (defined-but-never-called being the classic failure).
 *
 * NOTE: requires the model-sync diffs to be applied; these fail against
 * an unpatched tree, which is the expected pre-fix state.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.resolve(__dirname, rel), 'utf-8');

describe('model sync seams', () => {
  it('server reports the streaming model in /api/chat response headers', () => {
    const src = read('../../../../app/server.py');
    expect(src).toContain('"X-Ziya-Model"');
    expect(src).toContain('"X-Ziya-Model-Source"');
    // pin-vs-global attribution is what keeps pinned requests from being
    // misread as global drift
    expect(src).toContain('"pin" if pinned_model else "global"');
  });

  it('chatApi feeds the header report into the sync service', () => {
    const src = read('../../apis/chatApi.ts');
    expect(src).toContain("headers.get('X-Ziya-Model')");
    expect(src).toContain("headers.get('X-Ziya-Model-Source')");
    expect(src).toContain('reportStreamModel(');
    expect(src).toContain("from '../services/modelSyncService'");
  });

  it('FolderTree starts the sync service', () => {
    const src = read('../../components/FolderTree.tsx');
    expect(src).toContain('startModelSync()');
    expect(src).toContain("from '../services/modelSyncService'");
  });

  it('FolderTree pulses the label on model change', () => {
    const src = read('../../components/FolderTree.tsx');
    expect(src).toContain('model-label-pulse');
  });

  it('the pulse style exists in the stylesheet', () => {
    const css = read('../../index.css');
    expect(css).toContain('.model-label-pulse');
    expect(css).toContain('@keyframes ziya-model-pulse');
  });
});
