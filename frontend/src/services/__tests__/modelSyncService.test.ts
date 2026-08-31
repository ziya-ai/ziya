/**
 * @jest-environment jsdom
 *
 * Unit tests for modelSyncService — cross-session alignment of the
 * server-global model.  Covers both reconciliation layers:
 *   1. syncNow() revalidation against /api/current-model
 *   2. reportStreamModel() from the X-Ziya-Model response headers
 *
 * NOTE: requires the modelSyncService diff to be applied first; this
 * suite fails (module not found) against an unpatched tree, which is
 * the expected pre-fix state.
 */
import {
  syncNow, reportStreamModel, _resetForTest, _getLastKnownAlias,
  EXTERNAL_MODEL_SYNC_SOURCE,
} from '../modelSyncService';

const mockFetchModel = (alias: string, ok = true) => {
  (global.fetch as jest.Mock).mockResolvedValueOnce({
    ok,
    json: async () => ({ model_alias: alias, model_id: alias }),
  });
};

let listener: jest.Mock;

beforeEach(() => {
  _resetForTest();
  global.fetch = jest.fn();
  listener = jest.fn();
  window.addEventListener('modelChanged', listener);
});

afterEach(() => {
  window.removeEventListener('modelChanged', listener);
});

describe('syncNow revalidation', () => {
  it('first sync establishes a baseline without dispatching', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();
    expect(_getLastKnownAlias()).toBe('sonnet4.5');
    expect(listener).not.toHaveBeenCalled();
  });

  it('dispatches modelChanged with external-sync source on drift', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();
    mockFetchModel('gpt-5.2');
    await syncNow();

    expect(listener).toHaveBeenCalledTimes(1);
    const detail = (listener.mock.calls[0][0] as CustomEvent).detail;
    expect(detail.source).toBe(EXTERNAL_MODEL_SYNC_SOURCE);
    expect(detail.model).toBe('gpt-5.2');
    expect(detail.previous).toBe('sonnet4.5');
    // The detail deliberately lacks previousModel/newModel: ChatContext's
    // handler requires those to inject a conversation notice, and external
    // changes must be surfaced visually only.
    expect(detail.previousModel).toBeUndefined();
    expect(detail.newModel).toBeUndefined();
    expect(_getLastKnownAlias()).toBe('gpt-5.2');
  });

  it('does not dispatch when the model is unchanged', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();
    mockFetchModel('sonnet4.5');
    await syncNow();
    expect(listener).not.toHaveBeenCalled();
  });

  it('leaves the baseline untouched on fetch failure', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();
    (global.fetch as jest.Mock).mockRejectedValueOnce(new Error('network'));
    await syncNow();
    expect(_getLastKnownAlias()).toBe('sonnet4.5');
    expect(listener).not.toHaveBeenCalled();
  });

  it('leaves the baseline untouched on non-ok response', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();
    mockFetchModel('ignored', false);
    await syncNow();
    expect(_getLastKnownAlias()).toBe('sonnet4.5');
    expect(listener).not.toHaveBeenCalled();
  });
});

describe('reportStreamModel (X-Ziya-Model header)', () => {
  it('ignores pin-sourced reports entirely', () => {
    reportStreamModel('opus4.5', 'pin');
    expect(_getLastKnownAlias()).toBeNull();
    expect(listener).not.toHaveBeenCalled();
  });

  it('ignores null/missing values', () => {
    reportStreamModel(null, 'global');
    reportStreamModel('model', null);
    expect(_getLastKnownAlias()).toBeNull();
    expect(listener).not.toHaveBeenCalled();
  });

  it('adopts the baseline silently on the first global report', () => {
    reportStreamModel('sonnet4.5', 'global');
    expect(_getLastKnownAlias()).toBe('sonnet4.5');
    expect(listener).not.toHaveBeenCalled();
  });

  it('dispatches on drift from an established baseline', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();

    reportStreamModel('gpt-5.2', 'global');

    expect(listener).toHaveBeenCalledTimes(1);
    const detail = (listener.mock.calls[0][0] as CustomEvent).detail;
    expect(detail.source).toBe(EXTERNAL_MODEL_SYNC_SOURCE);
    expect(detail.model).toBe('gpt-5.2');
    expect(detail.previous).toBe('sonnet4.5');
    expect(_getLastKnownAlias()).toBe('gpt-5.2');
  });

  it('does not dispatch when the reported model matches the baseline', async () => {
    mockFetchModel('sonnet4.5');
    await syncNow();
    reportStreamModel('sonnet4.5', 'global');
    expect(listener).not.toHaveBeenCalled();
  });
});
