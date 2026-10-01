/**
 * Cost-display resolution (Docs/CostAccounting.md, "Where to see it").
 *
 * Mirrors the model pin: conversation → folder → project, most specific
 * wins, with one difference — there is no server-wide layer, so an
 * unbroken chain of nulls resolves to HIDDEN.  Display-only: the ledger
 * records tokens regardless of this value.
 *
 * The wiring tests read component source so that the resolver being
 * correct cannot mask the two surfaces ignoring it.
 */
import * as fs from 'fs';
import * as path from 'path';

import { normalizeCostDisplay, resolveCostDisplay } from '../costDisplay';

describe('resolveCostDisplay', () => {
  it('is hidden with nothing set anywhere, and names no scope', () => {
    expect(resolveCostDisplay({})).toEqual({ visible: false, scope: null });
    expect(resolveCostDisplay({ conversation: null, folder: null, project: null }))
      .toEqual({ visible: false, scope: null });
  });

  it('project show is inherited by folder and conversation', () => {
    expect(resolveCostDisplay({ project: 'show' })).toEqual({ visible: true, scope: 'project' });
  });

  it('a narrower hide overrides a wider show (and says which scope decided)', () => {
    expect(resolveCostDisplay({ project: 'show', conversation: 'hide' }))
      .toEqual({ visible: false, scope: 'conversation' });
    expect(resolveCostDisplay({ project: 'show', folder: 'hide' }))
      .toEqual({ visible: false, scope: 'folder' });
    expect(resolveCostDisplay({ project: 'hide', folder: 'show' }))
      .toEqual({ visible: true, scope: 'folder' });
  });

  it('null at a scope means inherit, not hide', () => {
    expect(resolveCostDisplay({ project: 'show', folder: null, conversation: null }))
      .toEqual({ visible: true, scope: 'project' });
  });

  it('ignores values that are not show/hide (a hand-edited record)', () => {
    expect(resolveCostDisplay({ project: 'show', conversation: 'yes' as any }))
      .toEqual({ visible: true, scope: 'project' });
  });
});

describe('normalizeCostDisplay', () => {
  it('accepts only the two legal values', () => {
    expect(normalizeCostDisplay('show')).toBe('show');
    expect(normalizeCostDisplay('hide')).toBe('hide');
    expect(normalizeCostDisplay(null)).toBeNull();
    expect(normalizeCostDisplay(undefined)).toBeNull();
    expect(normalizeCostDisplay(true)).toBeNull();
    expect(normalizeCostDisplay('SHOW')).toBeNull();
  });
});

describe('wiring', () => {
  const read = (rel: string) => fs.readFileSync(path.join(__dirname, '..', '..', rel), 'utf8');

  it('the model config modal is the only place UsageCostModal is mounted', () => {
    const modal = read('components/ModelConfigModal.tsx');
    expect(modal).toMatch(/import UsageCostModal from '\.\/UsageCostModal'/);
    expect(modal).toMatch(/<UsageCostModal[\s\S]*visible=\{showUsageCost\}/);
    // The discovery link in Manage Projects was deliberately removed: the
    // feature stays out of sight until a user asks (then: model config).
    const pm = read('components/ProjectManagerModal.tsx');
    expect(pm).not.toMatch(/UsageCostModal/);
    expect(pm).not.toMatch(/Usage &amp; cost/);
  });

  it('the checkbox is disabled at server scope and writes to the selected scope', () => {
    const modal = read('components/ModelConfigModal.tsx');
    expect(modal).toMatch(/disabled=\{pinScope === 'server'\}/);
    expect(modal).toMatch(/costDisplay\.onChange\(pinScope,/);
  });

  it('ModelConfigButton resolves and writes cost display through the hook, per scope', () => {
    const btn = read('components/ModelConfigButton.tsx');
    expect(btn).toMatch(/useResolvedCostDisplay\(\)/);
    expect(btn).toMatch(/setConversationCostDisplay\(currentConversationId, value\)/);
    expect(btn).toMatch(/costDisplay: value/); // folder + project record writes
  });

  it('ConversationInfoModal hides the Cost row (and skips the fetch) when not visible', () => {
    const info = read('components/ConversationInfoModal.tsx');
    expect(info).toMatch(/useResolvedCostDisplay\(\)/);
    expect(info).toMatch(/if \(!visible \|\| !conversationId \|\| !costVisible\) return;/);
    expect(info).toMatch(/\{costVisible && \(\s*<Descriptions\.Item label="Cost">/);
  });

  it('the setting round-trips on every record that carries modelPreference', () => {
    for (const rel of ['utils/types.ts', 'types/project.ts', 'api/folderSyncApi.ts']) {
      const src = read(rel);
      const prefs = (src.match(/modelPreference/g) || []).length;
      const costs = (src.match(/costDisplay/g) || []).length;
      expect({ file: rel, costs }).toEqual({ file: rel, costs: prefs });
    }
  });
});
