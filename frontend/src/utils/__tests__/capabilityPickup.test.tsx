/**
 * @jest-environment jsdom
 */
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import {
    markPickedUp, clearPickups, getPickups, useCapabilityPickups, _resetPickups,
} from '../capabilityPickup';

beforeEach(() => _resetPickups());

describe('capabilityPickup store', () => {
    it('records per conversation and clears only that conversation', () => {
        markPickedUp('A', 'skill:x');
        markPickedUp('B', 'skill:y');
        expect([...getPickups('A')]).toEqual(['skill:x']);
        clearPickups('A');
        expect(getPickups('A').size).toBe(0);
        expect([...getPickups('B')]).toEqual(['skill:y']);
    });
    it('ignores empty ids and keys', () => {
        markPickedUp('', 'skill:x');
        markPickedUp('A', '');
        expect(getPickups('A').size).toBe(0);
        expect(getPickups(null).size).toBe(0);
    });
});

const Probe: React.FC<{ cid: string }> = ({ cid }) => {
    const keys = useCapabilityPickups(cid);
    return <span data-testid="keys">{[...keys].join(',')}</span>;
};

describe('useCapabilityPickups', () => {
    it('re-renders on pickup and on clear for its own conversation only', () => {
        render(<Probe cid="A" />);
        expect(screen.getByTestId('keys')).toHaveTextContent('');
        act(() => markPickedUp('A', 'skill:x'));
        expect(screen.getByTestId('keys')).toHaveTextContent('skill:x');
        act(() => markPickedUp('B', 'skill:y'));
        expect(screen.getByTestId('keys')).toHaveTextContent('skill:x');
        act(() => clearPickups('A'));
        expect(screen.getByTestId('keys')).toHaveTextContent('');
    });
});
