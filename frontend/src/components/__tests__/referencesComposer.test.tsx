/**
 * @jest-environment jsdom
 *
 * Slice 3 of design/conversation-references.md: adding references from the
 * composer, and the per-message 📎.  Pinned:
 *   - AddReferenceModal pins through the API and announces, so every mounted
 *     📎 line re-fetches; a 400 is reported, not swallowed;
 *   - MessageReferencesIcon fetches nothing until hovered, then names what
 *     the message's turn read / cited / picked up;
 *   - the seams in files this test cannot mount: SendChatContainer routes the
 *     paperclip to a two-item menu and records a bare pasted link;
 *     MessageActions renders the icon for assistant messages.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import * as fs from 'fs';
import * as path from 'path';

import { AddReferenceModal } from '../AddReferenceModal';
import { MessageReferencesIcon, describeTurnReferences } from '../MessageReferencesIcon';
import { REFERENCES_CHANGED_EVENT } from '../../apis/referencesApi';

const BASE = '/api/v1/projects/P/chats/c1/references';
const calls: Array<{ url: string; method: string; body?: any }> = [];
let status = 200;
let served: any = { turn: 3, summary: { pinned: 0, picked_up: 0, useful: 0, faded: 0 }, references: [] };

beforeEach(() => {
    calls.length = 0;
    status = 200;
    (global as any).fetch = jest.fn((url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method || 'GET', body: init?.body ? JSON.parse(init.body as string) : undefined });
        const ok = status < 400;
        const body = init?.method === 'POST'
            ? { id: 'ref_new', kind: 'url', locator: 'https://example.com/spec', provenance: 'pinned', uses: [] }
            : served;
        return Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
    });
});

describe('AddReferenceModal', () => {
    it('pins the locator and title, then announces the change', async () => {
        const announced: string[] = [];
        document.addEventListener(REFERENCES_CHANGED_EVENT, e => announced.push((e as CustomEvent).detail.conversationId));
        const onClose = jest.fn();
        render(<AddReferenceModal open onClose={onClose} projectId="P" conversationId="c1" />);
        fireEvent.change(screen.getByTestId('add-reference-locator'), { target: { value: ' https://example.com/spec ' } });
        fireEvent.change(screen.getByTestId('add-reference-title'), { target: { value: 'The spec' } });
        fireEvent.click(screen.getByRole('button', { name: 'Add' }));
        await waitFor(() => expect(calls.some(c => c.url === BASE && c.method === 'POST')).toBe(true));
        expect(calls.find(c => c.method === 'POST')!.body).toEqual({ locator: 'https://example.com/spec', title: 'The spec' });
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(announced).toEqual(['c1']);
    });

    it('a refused locator is reported and the modal stays open', async () => {
        status = 400;
        const onClose = jest.fn();
        render(<AddReferenceModal open onClose={onClose} projectId="P" conversationId="c1" />);
        fireEvent.change(screen.getByTestId('add-reference-locator'), { target: { value: 'ziya://folder/x' } });
        fireEvent.click(screen.getByRole('button', { name: 'Add' }));
        await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true));
        await waitFor(() => expect(document.body.textContent).toMatch(/Not a trackable reference/));
        expect(onClose).not.toHaveBeenCalled();
    });

    it('Add is disabled with an empty locator', () => {
        render(<AddReferenceModal open onClose={jest.fn()} projectId="P" conversationId="c1" />);
        expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
    });
});

describe('MessageReferencesIcon', () => {
    const refs = [
        { id: 'a', kind: 'file', locator: 'src/main.py', title: null, provenance: 'picked_up', added_turn: 3,
          uses: [{ kind: 'read', turn: 3 }], useful: false, faded: false },
        { id: 'b', kind: 'url', locator: 'https://example.com/spec', title: 'The spec', provenance: 'pinned', added_turn: 1,
          uses: [{ kind: 'cited', turn: 3 }], useful: true, faded: false },
    ];

    it('fetches nothing until hovered, then describes the turn', async () => {
        served = { ...served, references: refs };
        render(<MessageReferencesIcon locate={() => ({ projectId: 'P', conversationId: 'c1', turn: 3 })} />);
        expect(calls).toHaveLength(0);
        fireEvent.mouseEnter(screen.getByTestId('message-references'));
        await waitFor(() => expect(calls.some(c => c.url === BASE)).toBe(true));
        await waitFor(() => expect(document.body.textContent).toMatch(/read src\/main\.py · cited The spec · new: src\/main\.py/));
    });

    it('describes an untouched turn plainly', () => {
        expect(describeTurnReferences({ read: [], cited: [], added: [] })).toBe('No references touched in this turn');
    });
});

describe('seams', () => {
    const src = (p: string) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

    it('SendChatContainer: paperclip is a two-item menu and a bare pasted link is recorded', () => {
        const s = src('SendChatContainer.tsx');
        expect(s).toMatch(/AddReferenceModal/);
        expect(s).toMatch(/bareReferenceFromPaste\(plain\)/);
        expect(s).toMatch(/addReference\(/);
        // Both menu items exist and the attach path is still the file picker.
        expect(s).toMatch(/key: 'attach'/);
        expect(s).toMatch(/key: 'reference'/);
        expect(s).toMatch(/fileInputRef\.current\?\.click\(\)/);
    });

    it('MessageActions renders the icon for assistant messages only', () => {
        const s = src('Conversation.tsx');
        const i = s.indexOf('<MessageReferencesIcon');
        expect(i).toBeGreaterThan(0);
        expect(s.slice(i - 200, i)).toMatch(/message\.role === 'assistant'/);
        expect(s.slice(i, i + 600)).toMatch(/turnOfMessage\(/);
    });
});
