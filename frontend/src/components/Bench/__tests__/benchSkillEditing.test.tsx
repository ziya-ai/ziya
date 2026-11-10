/**
 * @jest-environment jsdom
 *
 * Skill editing in the Bench (design/capabilities-hub.md, slice 6 "edit
 * mode"; stage 2 of the Skills-tab fold-in).  The ~400 editor lines the
 * sidebar tab carried move into the drawer and the panel header:
 *
 *   - CapabilityDetail offers "edit" and "delete" ONLY when the detail says
 *     the skill is editable (custom; built-in and file-backed are refused by
 *     the server), and routes them through the model's callbacks with the
 *     skill id the detail names -- never Skill.id guessed from the key;
 *   - BenchPanel shows "＋ New skill" only when the model can create, hosts
 *     the editor in the drawer slot, and after a create selects the new card;
 *   - SKILL.md import fills the form through the shared parser;
 *   - renderers still fetch nothing.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { BenchPanel, BenchPanelModel } from '../BenchPanel';
import { CapabilityDetail } from '../CapabilityDetail';
import { SkillEditor } from '../SkillEditor';
import { mk } from '../benchTestUtils';

const custom = () => mk('skill:fleet', { project: 'always', def: 'off', provenance: 'custom' });
const editableDetail = async () => ({
    key: 'skill:fleet', kind: 'skill' as const, name: 'fleet', description: 'Fleet telemetry',
    prompt: 'Query Atlas.', source: 'custom', skill_id: 'c-1', editable: true,
});
const fileBacked = async () => ({
    key: 'skill:fleet', kind: 'skill' as const, name: 'fleet', description: 'Fleet telemetry',
    prompt: 'Query Atlas.', source: 'project', path: '/p/.agents/skills/fleet/SKILL.md', skill_id: 'f-1', editable: false,
});

const type = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe('CapabilityDetail edit mode', () => {
    it('edits a custom skill through onUpdateSkill with the detail\'s skill id', async () => {
        const onUpdateSkill = jest.fn(async () => {});
        render(<CapabilityDetail item={custom()} detail={editableDetail} onUpdateSkill={onUpdateSkill} onClose={() => {}} />);
        fireEvent.click(await screen.findByTestId('detail-edit'));
        expect(screen.getByLabelText('Skill name')).toHaveValue('fleet');
        type('Prompt', 'Query Atlas, then ClickHouse.');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
        expect(onUpdateSkill).toHaveBeenCalledWith('c-1', {
            name: 'fleet', description: 'Fleet telemetry', prompt: 'Query Atlas, then ClickHouse.',
        });
        // Back to the read view, showing what was saved.
        await waitFor(() => expect(screen.getByTestId('skill-prompt')).toHaveTextContent('Query Atlas, then ClickHouse.'));
    });

    it('deletes after a second click, then closes the drawer', async () => {
        const onDeleteSkill = jest.fn(async () => {});
        const onClose = jest.fn();
        render(<CapabilityDetail item={custom()} detail={editableDetail} onDeleteSkill={onDeleteSkill} onClose={onClose} />);
        fireEvent.click(await screen.findByTestId('detail-delete'));
        expect(onDeleteSkill).not.toHaveBeenCalled();                 // first click only arms
        await act(async () => { fireEvent.click(screen.getByTestId('detail-delete-confirm')); });
        expect(onDeleteSkill).toHaveBeenCalledWith('c-1', 'skill:fleet');
        expect(onClose).toHaveBeenCalled();
    });

    it('a file-backed skill gets no edit or delete even when the callbacks exist', async () => {
        render(<CapabilityDetail item={custom()} detail={fileBacked} onUpdateSkill={jest.fn()} onDeleteSkill={jest.fn()} onClose={() => {}} />);
        await screen.findByTestId('skill-prompt');
        expect(screen.queryByTestId('detail-edit')).toBeNull();
        expect(screen.queryByTestId('detail-delete')).toBeNull();
        expect(screen.getByText(/READ-ONLY/)).toBeInTheDocument();
        expect(screen.getByTestId('skill-path')).toHaveTextContent('SKILL.md');
    });

    it('without the callbacks an editable detail is still read-only', async () => {
        render(<CapabilityDetail item={custom()} detail={editableDetail} onClose={() => {}} />);
        await screen.findByTestId('skill-prompt');
        expect(screen.queryByTestId('detail-edit')).toBeNull();
    });
});

function model(over: Partial<BenchPanelModel> = {}): BenchPanelModel {
    return {
        ready: true, error: null, items: [custom()], dropped: [],
        weight: { always: 10, ondemand: 0, environment: 0 },
        deltas: { conversation: 0, project: 1, user: 0 },
        lenses: ['project', 'user'], lens: 'project', setLens: jest.fn(),
        place: jest.fn(async () => {}), ...over,
    };
}

describe('BenchPanel new skill', () => {
    const realFetch = (global as any).fetch;
    afterEach(() => { (global as any).fetch = realFetch; });

    it('offers New skill only when the model can create', () => {
        render(<BenchPanel bench={model()} />);
        expect(screen.queryByTestId('bench-new-skill')).toBeNull();
    });

    it('creates through the model, then selects the new card; no network', async () => {
        const f = jest.fn();
        (global as any).fetch = f;
        const created = { id: 'c-2', name: 'notes', description: 'Take notes', prompt: 'Keep notes.' };
        const createSkill = jest.fn(async () => created as any);
        const m = model({ createSkill, items: [custom(), mk('skill:notes', { provenance: 'custom', def: 'off' })] });
        render(<BenchPanel bench={m} />);
        fireEvent.click(screen.getByTestId('bench-new-skill'));
        expect(screen.getByTestId('skill-editor')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();   // name + prompt required
        type('Skill name', 'notes');
        type('Description', 'Take notes');
        type('Prompt', 'Keep notes.');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create' })); });
        expect(createSkill).toHaveBeenCalledWith({ name: 'notes', description: 'Take notes', prompt: 'Keep notes.' });
        // The editor closes and the drawer opens on the new card.
        expect(screen.queryByTestId('skill-editor')).toBeNull();
        expect(screen.getByTestId('bench-detail')).toHaveAttribute('aria-label', 'Details for notes');
        expect(f).not.toHaveBeenCalled();
    });

    it('cancel closes the editor without creating', () => {
        const createSkill = jest.fn();
        render(<BenchPanel bench={model({ createSkill })} />);
        fireEvent.click(screen.getByTestId('bench-new-skill'));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByTestId('skill-editor')).toBeNull();
        expect(createSkill).not.toHaveBeenCalled();
    });
});

describe('SkillEditor import', () => {
    it('a SKILL.md fills the form; a file without frontmatter is reported', async () => {
        const onSave = jest.fn(async () => {});
        render(<SkillEditor title="New skill" saveLabel="Create" onSave={onSave} onCancel={() => {}} allowImport />);
        const input = screen.getByTestId('skill-import-input') as HTMLInputElement;
        const good = new File(['---\nname: imported\ndescription: From a file\nkeywords: a, b\n---\nBody here.\n'], 'SKILL.md');
        await act(async () => { fireEvent.change(input, { target: { files: [good] } }); });
        await waitFor(() => expect(screen.getByLabelText('Skill name')).toHaveValue('imported'));
        expect(screen.getByLabelText('Prompt')).toHaveValue('Body here.');
        const bad = new File(['no frontmatter'], 'x.md');
        await act(async () => { fireEvent.change(input, { target: { files: [bad] } }); });
        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/frontmatter/));
        expect(screen.getByLabelText('Skill name')).toHaveValue('imported');   // bad file does not wipe the form
    });
});
