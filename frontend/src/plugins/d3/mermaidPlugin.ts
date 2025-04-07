import mermaid from 'mermaid';
import { D3RenderPlugin } from '../../types/d3';
import { PictureOutlined, DownloadOutlined } from '@ant-design/icons';
import { Spin } from 'antd'; // For loading indicator

// Define the specification for Mermaid diagrams
export interface MermaidSpec {
    type: 'mermaid';
    definition: string;
    theme?: 'default' | 'dark' | 'neutral' | 'forest'; // Optional theme override
}

// Type guard to check if a spec is for Mermaid
const isMermaidSpec = (spec: any): spec is MermaidSpec => {
    return (
        typeof spec === 'object' &&
        spec !== null &&
        spec.type === 'mermaid' &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

const SCALE_CONFIG = {
    TARGET_FONT_SIZE: 14,   // Target font size in pixels
    MIN_FONT_SIZE: 12,      // Minimum font size in pixels
    MAX_SCALE: 1.0         // Maximum scale (natural size)
};

export const mermaidPlugin: D3RenderPlugin = {
    name: 'mermaid-renderer',
    priority: 5,

    canHandle: (spec: any): boolean => {
        return isMermaidSpec(spec);
    },

    render: async (container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean): Promise<void> => {
        try {
            container.innerHTML = '';

            // Initialize mermaid with graph-specific settings
            mermaid.initialize({
                startOnLoad: false,
                theme: isDarkMode ? 'dark' : 'default',
                securityLevel: 'loose',
                fontFamily: '"Arial", sans-serif',
                fontSize: 14,
                flowchart: {
                    htmlLabels: true,
                    curve: 'basis',
                    padding: 15,
                    nodeSpacing: 50,
                    rankSpacing: 50,
                    diagramPadding: 8,
                },
                sequence: {
                    diagramMarginX: 50,
                    diagramMarginY: 30,
                    actorMargin: 50,
                    width: 150,
                    height: 65,
                    boxMargin: 10,
                    boxTextMargin: 5,
                    noteMargin: 10,
                    messageMargin: 35,
                    mirrorActors: true,
                    bottomMarginAdj: 1,
                    useMaxWidth: true,
                },
                gantt: {
                    titleTopMargin: 25,
                    barHeight: 20,
                    barGap: 4,
                    topPadding: 50,
                    leftPadding: 75,
                    gridLineStartPadding: 35,
                    fontSize: 11,
                    sectionFontSize: 11,
                    numberSectionStyles: 4,
                    axisFormat: '%Y-%m-%d',
                    topAxis: false,
                },
            });

            // Render the diagram
            const mermaidId = `mermaid-${Date.now()}-${Math.random().toString(16).substring(2)}`;
            const { svg } = await mermaid.render(mermaidId, spec.definition);

            // Create wrapper div
            const wrapper = document.createElement('div');
            wrapper.className = 'mermaid-wrapper';
            wrapper.style.cssText = `
                width: 100%;
                max-width: 100%;
                overflow: auto;
                padding: 1em;
                display: flex;
                justify-content: center;
            `;
            wrapper.innerHTML = svg;

            // Add wrapper to container
            container.appendChild(wrapper);

            // Get the SVG element after it's in the DOM
            const svgElement = wrapper.querySelector('svg');
            if (!svgElement) {
                throw new Error('Failed to get SVG element');
            }

            // Wait for next frame to ensure SVG is rendered
            requestAnimationFrame(() => {
                // Find all text elements
                const textElements = svgElement.querySelectorAll('text');
                if (textElements.length === 0) return;
                // Get the computed font size of the first text element
                const computedStyle = window.getComputedStyle(textElements[0]);
                const currentFontSize = parseFloat(computedStyle.fontSize);

                // Calculate scale based on target font size
                const scale = SCALE_CONFIG.TARGET_FONT_SIZE / currentFontSize;

                // Apply transform scale to the SVG
                svgElement.style.transform = `scale(${scale})`;
                svgElement.style.transformOrigin = 'center';
                svgElement.style.width = '100%';
                svgElement.style.height = 'auto';
            });

            // Add action buttons
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'diagram-actions';

            // Add Open button
            const openButton = document.createElement('button');
            openButton.innerHTML = '↗️ Open';
            openButton.className = 'diagram-action-button mermaid-open-button';
            openButton.onclick = () => {
                const dataUri = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;
                window.open(dataUri, '_blank');
            };
            actionsContainer.appendChild(openButton);

            // Add Save button
            const saveButton = document.createElement('button');
            saveButton.innerHTML = '💾 Save';
            saveButton.className = 'diagram-action-button mermaid-save-button';
            saveButton.onclick = () => {
                const dataUri = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;
                const link = document.createElement('a');
                link.href = dataUri;
                link.download = `mermaid-diagram-${Date.now()}.svg`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            };
            actionsContainer.appendChild(saveButton);

            // Add actions container
            container.insertBefore(actionsContainer, wrapper);

        } catch (error: any) {
            console.error('Mermaid rendering error:', error);
            container.innerHTML = `
                <div class="mermaid-error">
                    <strong>Mermaid Error:</strong>
                    <pre>${error.message || 'Unknown error'}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${spec.definition}</code></pre>
                    </details>
                </div>
            `;
        }
    }
};
