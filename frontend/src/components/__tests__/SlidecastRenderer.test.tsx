/**
 * @jest-environment jsdom
 *
 * Behavioral tests for the Slidecast renderer — the presentation primitive
 * that turns a chain of rendered blocks into a narrated, navigable walkthrough
 * with captions synced to the active frame.
 *
 * SlidecastRenderer is renderer-agnostic: it receives injected renderFrame /
 * renderCaption functions, so we can test navigation, index clamping,
 * wraparound, caption sync, and the empty-frames guard in isolation without
 * pulling in the whole D3/markdown pipeline. useTheme is mocked so the
 * component doesn't require a ThemeProvider wrapper.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

// Mock useTheme — the component only reads isDarkMode for styling.
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false }),
}));

import { SlidecastRenderer, SlidecastSpec, SlidecastFrame } from '../SlidecastRenderer';

// renderFrame stub: emits the frame's spec text so we can assert which frame
// is currently mounted.
const renderFrame = (frame: SlidecastFrame, key: string) => (
    <div data-testid="frame-body" key={key}>{frame.spec}</div>
);
// renderCaption stub: emits the raw markdown so we can assert caption sync.
const renderCaption = (md: string) => <div data-testid="frame-caption">{md}</div>;

const threeFrameSpec: SlidecastSpec = {
    title: 'Test Cast',
    sync: 'caption',
    frames: [
        { type: 'drawio', spec: 'FRAME_A', caption: 'caption A' },
        { type: 'drawio', spec: 'FRAME_B', caption: 'caption B' },
        { type: 'drawio', spec: 'FRAME_C', caption: 'caption C' },
    ],
};

const renderCast = (spec: SlidecastSpec) =>
    render(
        <SlidecastRenderer spec={spec} renderFrame={renderFrame} renderCaption={renderCaption} />
    );

describe('SlidecastRenderer', () => {
    it('renders the first frame and its caption initially', () => {
        renderCast(threeFrameSpec);
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_A');
        expect(screen.getByTestId('frame-caption')).toHaveTextContent('caption A');
        expect(screen.getByText('1 / 3')).toBeInTheDocument();
    });

    it('advances to the next frame and syncs the caption', () => {
        renderCast(threeFrameSpec);
        fireEvent.click(screen.getByLabelText('Next frame'));
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_B');
        expect(screen.getByTestId('frame-caption')).toHaveTextContent('caption B');
        expect(screen.getByText('2 / 3')).toBeInTheDocument();
    });

    it('goes to the previous frame', () => {
        renderCast(threeFrameSpec);
        fireEvent.click(screen.getByLabelText('Next frame'));
        fireEvent.click(screen.getByLabelText('Previous frame'));
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_A');
        expect(screen.getByText('1 / 3')).toBeInTheDocument();
    });

    it('wraps around from last to first when clicking Next on the last frame', () => {
        renderCast(threeFrameSpec);
        const next = screen.getByLabelText('Next frame');
        fireEvent.click(next); // B
        fireEvent.click(next); // C
        fireEvent.click(next); // wrap -> A
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_A');
        expect(screen.getByText('1 / 3')).toBeInTheDocument();
    });

    it('wraps around from first to last when clicking Previous on the first frame', () => {
        renderCast(threeFrameSpec);
        fireEvent.click(screen.getByLabelText('Previous frame'));
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_C');
        expect(screen.getByText('3 / 3')).toBeInTheDocument();
    });

    it('navigates directly via frame dots', () => {
        renderCast(threeFrameSpec);
        fireEvent.click(screen.getByLabelText('Go to frame 3'));
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_C');
        expect(screen.getByText('3 / 3')).toBeInTheDocument();
    });

    it('responds to ArrowRight / ArrowLeft keyboard navigation', () => {
        const { container } = renderCast(threeFrameSpec);
        const root = container.querySelector('.ziya-slidecast') as HTMLElement;
        fireEvent.keyDown(root, { key: 'ArrowRight' });
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_B');
        fireEvent.keyDown(root, { key: 'ArrowLeft' });
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_A');
    });

    it('renders the title in the control bar', () => {
        renderCast(threeFrameSpec);
        expect(screen.getByText('Test Cast')).toBeInTheDocument();
    });

    it('shows an empty-state message when there are no frames', () => {
        renderCast({ title: 'Empty', frames: [] });
        expect(screen.getByText(/Empty slidecast/i)).toBeInTheDocument();
    });

    it('suppresses captions when sync is "none"', () => {
        renderCast({ ...threeFrameSpec, sync: 'none' });
        expect(screen.queryByTestId('frame-caption')).not.toBeInTheDocument();
        // Frame body still renders.
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_A');
    });

    it('renders frames without captions gracefully', () => {
        renderCast({
            frames: [
                { type: 'mermaid', spec: 'graph TD; A-->B' },
                { type: 'mermaid', spec: 'graph TD; B-->C' },
            ],
        });
        expect(screen.getByTestId('frame-body')).toHaveTextContent('graph TD; A-->B');
        expect(screen.queryByTestId('frame-caption')).not.toBeInTheDocument();
        fireEvent.click(screen.getByLabelText('Next frame'));
        expect(screen.getByTestId('frame-body')).toHaveTextContent('graph TD; B-->C');
    });

    it('does not render navigation dots for a single-frame cast', () => {
        renderCast({ frames: [{ type: 'drawio', spec: 'ONLY' }] });
        expect(screen.queryByLabelText('Go to frame 1')).not.toBeInTheDocument();
        expect(screen.getByText('1 / 1')).toBeInTheDocument();
    });

    it('supports the sidebar sync layout', () => {
        renderCast({ ...threeFrameSpec, sync: 'sidebar' });
        // Both frame and caption present, caption is the synced narration.
        expect(screen.getByTestId('frame-body')).toHaveTextContent('FRAME_A');
        expect(screen.getByTestId('frame-caption')).toHaveTextContent('caption A');
    });
});
