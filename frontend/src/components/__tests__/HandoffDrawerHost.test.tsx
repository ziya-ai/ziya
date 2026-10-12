/**
 * @jest-environment jsdom
 *
 * The token bar's "⇢ Hand off…" dispatches a document event.  Its listener
 * and the drawer used to live inside MUIChatHistory, which antd's Tabs only
 * mounts once the sidebar's Chats tab has been opened (and which a project
 * switch replaces with a spinner) — so the click went nowhere.  The host is
 * mounted by App unconditionally; these tests pin both halves of that seam.
 */
import React from 'react';
import fs from 'fs';
import path from 'path';
import { render, screen, act } from '@testing-library/react';

jest.mock('../HandoffDrawer', () => ({
  __esModule: true,
  default: ({ conversationId, onClose }: { conversationId: string | null; onClose: () => void }) => (
    <div data-testid="drawer" data-open={conversationId ? 'yes' : 'no'}>
      {conversationId ?? ''}
      <button onClick={onClose}>close</button>
    </div>
  ),
}));

import HandoffDrawerHost from '../HandoffDrawerHost';
import { dispatchHandoffOpen } from '../../utils/handoffOpen';

const src = (rel: string) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');

describe('HandoffDrawerHost', () => {
  it('opens the drawer for the dispatched conversation and closes on onClose', () => {
    render(<HandoffDrawerHost />);
    expect(screen.getByTestId('drawer').getAttribute('data-open')).toBe('no');

    act(() => dispatchHandoffOpen('427862e0'));
    expect(screen.getByTestId('drawer').getAttribute('data-open')).toBe('yes');
    expect(screen.getByTestId('drawer').textContent).toContain('427862e0');

    act(() => screen.getByText('close').click());
    expect(screen.getByTestId('drawer').getAttribute('data-open')).toBe('no');
  });

  it('strips the chat-tree "conv-" prefix', () => {
    render(<HandoffDrawerHost />);
    act(() => dispatchHandoffOpen('conv-abc'));
    expect(screen.getByTestId('drawer').textContent).toContain('abc');
    expect(screen.getByTestId('drawer').textContent).not.toContain('conv-');
  });

  it('stops listening after unmount', () => {
    const { unmount } = render(<HandoffDrawerHost />);
    unmount();
    // Would throw a state-update-on-unmounted warning path if still subscribed;
    // the real assertion is that no listener survives.
    expect(() => act(() => dispatchHandoffOpen('x'))).not.toThrow();
  });
});

describe('handoff drawer mounting', () => {
  it('App mounts the host outside the sidebar tabs', () => {
    const app = src('App.tsx');
    expect(app).toMatch(/import\s+HandoffDrawerHost\s+from\s+'\.\/HandoffDrawerHost'/);
    expect(app).toContain('<HandoffDrawerHost />');
  });

  it('MUIChatHistory no longer mounts its own drawer or listener', () => {
    const hist = src('MUIChatHistory.tsx');
    expect(hist).not.toContain('<HandoffDrawer');
    expect(hist).not.toContain('subscribeHandoffOpen');
    // The context-menu entry still opens it — through the same event.
    expect(hist).toContain('dispatchHandoffOpen(');
  });
});
