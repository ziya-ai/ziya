/**
 * Tests for the template-manager sub-view in ProjectManagerModal.
 *
 * The view itself is presentation, but three pieces of it carry real
 * behaviour that is easy to get wrong and invisible when wrong:
 *
 *   1. **Toggling the default.** Clicking the CURRENT default must clear it
 *      (send an explicit null), not no-op. The API's clear is a null body
 *      the UI would otherwise never send, so without the toggle there is no
 *      way back to "detection only" once a default is set.
 *
 *   2. **Two separate copies of the template list.** The create form's copy
 *      and the manager's copy are deliberately distinct — the manager
 *      mutates and re-reads, the create form holds a render snapshot. That
 *      separation is correct but creates a staleness obligation: leaving the
 *      manager must sync the create form's copy, or the list-view hint and
 *      the create picker keep offering a template that was just deleted.
 *
 *   3. **Built-in vs user partition.** Built-ins must not offer a delete
 *      button; the server returns 403, so offering it would produce an
 *      error the user cannot act on.
 *
 * Mirrors are asserted against the real component source (the
 * `extraction fidelity` block), so a drift between what these tests model
 * and what the component does fails here rather than silently passing.
 */

import * as fs from 'fs';
import * as path from 'path';

import { api } from '../index';
import { setDefaultTemplate, deleteTemplate } from '../projectTemplateApi';
import type { ProjectTemplate } from '../../types/projectTemplate';

jest.mock('../index', () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;

function modalSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', '..', 'components', 'ProjectManagerModal.tsx'),
    'utf-8',
  );
}

function tpl(over: Partial<ProjectTemplate> = {}): ProjectTemplate {
  return {
    id: 'general',
    name: 'General',
    description: '',
    detectMarkers: [],
    settings: {},
    isBuiltIn: true,
    ...over,
  };
}

/**
 * The component's default-toggle rule, mirrored.
 *
 * Returns what should be SENT to the server: null clears, a string sets.
 */
function nextDefault(
  currentDefault: string | null, clicked: string,
): string | null {
  return currentDefault === clicked ? null : clicked;
}

/** The component's built-in/user partition, mirrored. */
function partition(templates: ProjectTemplate[]) {
  return {
    builtIns: templates.filter(t => t.isBuiltIn),
    userTemplates: templates.filter(t => !t.isBuiltIn),
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('default-template toggle', () => {
  it('sets the default when a non-default template is clicked', () => {
    expect(nextDefault(null, 'software_development')).toBe('software_development');
  });

  it('clears the default when the current default is clicked again', () => {
    // The load-bearing case: without it a default can be set but never
    // unset, because the clear path is an explicit null.
    expect(nextDefault('software_development', 'software_development')).toBeNull();
  });

  it('switches directly between two templates', () => {
    expect(nextDefault('general', 'software_development'))
      .toBe('software_development');
  });

  it('sends the resolved value straight through to the API', async () => {
    mockApi.put.mockResolvedValue({ templates: [], defaultTemplateId: null });
    await setDefaultTemplate(nextDefault('deno', 'deno'));
    expect(mockApi.put).toHaveBeenCalledWith(
      '/projects/templates/default', { templateId: null },
    );
  });

  it('sends the id when setting rather than clearing', async () => {
    mockApi.put.mockResolvedValue({
      templates: [], defaultTemplateId: 'deno',
    });
    await setDefaultTemplate(nextDefault(null, 'deno'));
    expect(mockApi.put).toHaveBeenCalledWith(
      '/projects/templates/default', { templateId: 'deno' },
    );
  });
});

describe('built-in vs user partition', () => {
  const list = [
    tpl({ id: 'general', name: 'General' }),
    tpl({ id: 'software_development', name: 'Software Development' }),
    tpl({ id: 'deno', name: 'Deno', isBuiltIn: false }),
  ];

  it('separates shipped templates from user-authored ones', () => {
    const { builtIns, userTemplates } = partition(list);
    expect(builtIns.map(t => t.id))
      .toEqual(['general', 'software_development']);
    expect(userTemplates.map(t => t.id)).toEqual(['deno']);
  });

  it('treats a missing isBuiltIn as user-authored', () => {
    // The server always sends the flag, but a hand-edited templates.json
    // entry may omit it. Defaulting to "user" is the safe direction: it
    // offers a delete the server will honour, rather than hiding one.
    const noFlag = { ...tpl({ id: 'x', name: 'X' }) } as any;
    delete noFlag.isBuiltIn;
    expect(partition([noFlag]).userTemplates).toHaveLength(1);
  });

  it('deletes by id through the API', async () => {
    mockApi.delete.mockResolvedValue(undefined);
    await deleteTemplate('deno');
    expect(mockApi.delete).toHaveBeenCalledWith('/projects/templates/deno');
  });
});

describe('leaving the manager syncs the create form', () => {
  /**
   * Mirror of `closeTemplateManager`: copy the managed list over the create
   * form's copy, and drop an override that names a template which no longer
   * exists.
   */
  function closeManager(
    managed: ProjectTemplate[], managedDefault: string | null,
    override: string | null,
  ) {
    return {
      templates: managed,
      defaultTemplateId: managedDefault,
      templateOverride: override && !managed.some(t => t.id === override)
        ? null
        : override,
    };
  }

  it('copies the managed list and default into the create form state', () => {
    const managed = [tpl({ id: 'deno', name: 'Deno', isBuiltIn: false })];
    const out = closeManager(managed, 'deno', null);
    expect(out.templates).toBe(managed);
    expect(out.defaultTemplateId).toBe('deno');
  });

  it('drops an override naming a template deleted while in the manager', () => {
    // Otherwise create would POST a templateId the server cannot resolve.
    const out = closeManager([tpl({ id: 'general' })], null, 'deno');
    expect(out.templateOverride).toBeNull();
  });

  it('keeps an override that still exists', () => {
    const managed = [tpl({ id: 'deno', isBuiltIn: false })];
    expect(closeManager(managed, null, 'deno').templateOverride).toBe('deno');
  });

  it('leaves a null override alone', () => {
    expect(closeManager([], null, null).templateOverride).toBeNull();
  });
});

describe('extraction fidelity', () => {
  // These read the real component so the mirrors above cannot drift into
  // testing a fiction.

  it('the component toggles rather than re-sets the current default', () => {
    const src = modalSource();
    expect(src).toMatch(
      /managedDefaultId === templateId \? null : templateId/,
    );
  });

  it('the component offers delete only for non-built-in templates', () => {
    const src = modalSource();
    // The delete affordance must be inside a !tpl.isBuiltIn guard.
    expect(src).toMatch(/!tpl\.isBuiltIn && \(/);
  });

  it('the component syncs create-form state when leaving the manager', () => {
    const src = modalSource();
    const fn = src.slice(src.indexOf('closeTemplateManager = ()'));
    expect(fn).toMatch(/setTemplates\(managedTemplates\)/);
    expect(fn).toMatch(/setDefaultTemplateId\(managedDefaultId\)/);
  });

  it('the component clears a dangling override when leaving the manager', () => {
    const src = modalSource();
    const fn = src.slice(src.indexOf('closeTemplateManager = ()'));
    expect(fn).toMatch(/setTemplateOverride\(null\)/);
  });

  it('the manager loads the list on open, not on every render', () => {
    const src = modalSource();
    // Guarded by the view flag so entering the view triggers exactly one load.
    expect(src).toMatch(/if \(!showTemplateManager\) return;/);
  });

  it('the create form list loads on modal visibility so names resolve', () => {
    const src = modalSource();
    // The list-view default hint resolves an id to a name, which needs the
    // list loaded before the create form is ever opened.
    //
    // Deliberately scoped to the effect that calls listTemplates rather
    // than matching `}, [visible]);` anywhere in the file: an unrelated
    // effect (`if (visible) refreshProjects()`) already ends that way, so
    // the loose pattern passed against UNPATCHED source — a fidelity check
    // that cannot fail is worse than no check, because it reports
    // agreement it never verified.
    const idx = src.indexOf('templateApi.listTemplates()');
    expect(idx).toBeGreaterThan(-1);
    const effect = src.slice(idx, idx + 600);
    expect(effect).toMatch(/\}, \[visible\]\);/);
    expect(effect).not.toMatch(/\}, \[showCreateForm\]\);/);
  });

  it('the manager distinguishes loading from empty', () => {
    const src = modalSource();
    expect(src).toMatch(/templatesLoaded/);
    expect(src).toMatch(/Loading templates/);
  });

  it('the delete confirmation states that projects keep their settings', () => {
    // Apply-once is the reason deletion is safe; saying so is what stops a
    // user assuming it will strip their projects.
    expect(modalSource()).toMatch(/keep their settings/);
  });
});
