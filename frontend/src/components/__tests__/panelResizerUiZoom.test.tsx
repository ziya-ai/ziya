/**
 * Behavioural seam: with CSS zoom 0.8 applied, a mouse at viewport x=400
 * sits over layout x=500, so dragging the folder-panel resizer there must
 * set a 500-px panel.  Against unpatched PanelResizer this reports 400 and
 * the panel lags the cursor by 20%.
 */
import React from 'react';
import { render, fireEvent } from '@testing-library/react';
import PanelResizer from '../PanelResizer';
import { applyUiZoom } from '../../utils/uiScale';

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false }),
}));

describe('PanelResizer under UI zoom', () => {
    let rafSpy: jest.SpyInstance;
    beforeEach(() => {
        rafSpy = jest.spyOn(window, 'requestAnimationFrame')
            .mockImplementation((cb: FrameRequestCallback) => { cb(performance.now() + 100); return 1; });
        jest.spyOn(performance, 'now').mockReturnValue(1000);
    });
    afterEach(() => {
        rafSpy.mockRestore();
        (performance.now as jest.Mock).mockRestore?.();
        applyUiZoom(1);
    });

    const drag = (clientX: number) => {
        const onResize = jest.fn();
        const { container } = render(<PanelResizer onResize={onResize} isPanelCollapsed={false} />);
        const handle = container.querySelector('.panel-resizer') as HTMLElement;
        fireEvent.mouseDown(handle);
        fireEvent.mouseMove(document, { clientX });
        fireEvent.mouseUp(document);
        return onResize;
    };

    it('reports the layout width (clientX / zoom) at zoom 0.8', () => {
        applyUiZoom(0.8);
        expect(drag(400)).toHaveBeenCalledWith(500);
    });

    it('is unchanged at zoom 1', () => {
        applyUiZoom(1);
        expect(drag(400)).toHaveBeenCalledWith(400);
    });
});
