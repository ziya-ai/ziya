/**
 * @jest-environment jsdom
 *
 * ToolBatch wraps 3+ consecutive tool rows in one strip.  It is collapsed
 * once a turn is finished and open while streaming (so a running call is
 * never hidden), and a click always wins over that default.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { ToolBatch } from '../ToolBatch';

const NAMES = ['mcp_run_shell_command', 'mcp_memory_search', 'mcp_run_shell_command'];

const renderBatch = (isStreaming: boolean) => render(
    <ToolBatch toolNames={NAMES} isDarkMode={false} isStreaming={isStreaming}>
        <div data-testid="row-a">row a</div>
        <div data-testid="row-b">row b</div>
        <div data-testid="row-c">row c</div>
    </ToolBatch>
);

describe('ToolBatch', () => {
    it('shows the call count and family summary in the strip', () => {
        renderBatch(false);
        expect(screen.getByText('3 tool calls')).toBeTruthy();
        expect(screen.getByText('2 shell · 1 memory')).toBeTruthy();
    });

    it('is collapsed for a finished turn and opens on click', () => {
        renderBatch(false);
        expect(screen.queryByTestId('row-a')).toBeNull();
        fireEvent.click(screen.getByRole('button'));
        expect(screen.getByTestId('row-a')).toBeTruthy();
        expect(screen.getByTestId('row-c')).toBeTruthy();
    });

    it('is open while streaming, and a click collapses it', () => {
        renderBatch(true);
        expect(screen.getByTestId('row-b')).toBeTruthy();
        fireEvent.click(screen.getByRole('button'));
        expect(screen.queryByTestId('row-b')).toBeNull();
    });

    it('collapses when streaming ends unless the user toggled it', () => {
        const { rerender } = renderBatch(true);
        expect(screen.getByTestId('row-a')).toBeTruthy();
        rerender(
            <ToolBatch toolNames={NAMES} isDarkMode={false} isStreaming={false}>
                <div data-testid="row-a">row a</div>
            </ToolBatch>
        );
        expect(screen.queryByTestId('row-a')).toBeNull();
    });
});
