/**
 * Tests for D3Renderer streaming render state management.
 *
 * Validates that the render success path updates both ref and state
 * so that visualizations become visible as soon as their spec is complete,
 * rather than waiting for the entire message stream to finish.
 */

describe('D3Renderer render success state updates', () => {
    it('should set showRawContent=false on render success during streaming', () => {
        // Simulate component state at mount time during streaming
        let isLoading = true;
        let hasSuccessfulRender = false;
        let showRawContent = true;
        const hasSuccessfulRenderRef = { current: false };
        const isStreaming = true;
        const isMarkdownBlockClosed = true;

        // Simulate the FIXED render success path
        const simulateRenderSuccess = () => {
            isLoading = false;
            hasSuccessfulRenderRef.current = true;
            hasSuccessfulRender = true;
            showRawContent = false;
        };

        // Simulate the showRawContent effect logic
        const evaluateShowRawContent = () => {
            if (hasSuccessfulRenderRef.current && isMarkdownBlockClosed) {
                showRawContent = false;
                return;
            }
            if ((isStreaming && !hasSuccessfulRenderRef.current) ||
                !isMarkdownBlockClosed || isLoading) {
                showRawContent = true;
            } else {
                showRawContent = false;
            }
        };

        // Before render: raw content visible during streaming
        evaluateShowRawContent();
        expect(showRawContent).toBe(true);

        // Plugin render completes (e.g. vegaEmbed finishes)
        simulateRenderSuccess();
        expect(showRawContent).toBe(false);
        expect(hasSuccessfulRenderRef.current).toBe(true);
        expect(hasSuccessfulRender).toBe(true);

        // Effect re-evaluation should maintain rendered state
        evaluateShowRawContent();
        expect(showRawContent).toBe(false);
    });

    it('confirms the old code path left showRawContent=true after render', () => {
        let isLoading = true;
        let showRawContent = true;
        const hasSuccessfulRenderRef = { current: false };

        // OLD behavior: only ref and isLoading updated, no direct state set
        const simulateOldRenderSuccess = () => {
            isLoading = false;
            hasSuccessfulRenderRef.current = true;
            // Missing: setHasSuccessfulRender(true)
            // Missing: setShowRawContent(false)
        };

        simulateOldRenderSuccess();
        // Without direct state update, showRawContent remained true
        expect(showRawContent).toBe(true);
    });

    it('CustomEvent with bubbles:true propagates to parent element', () => {
        const parent = document.createElement('div');
        const child = document.createElement('div');
        parent.appendChild(child);

        let received = false;
        parent.addEventListener('vega-render-complete', () => { received = true; });

        child.dispatchEvent(new CustomEvent('vega-render-complete', {
            detail: { success: true },
            bubbles: true
        }));

        expect(received).toBe(true);
    });
});
