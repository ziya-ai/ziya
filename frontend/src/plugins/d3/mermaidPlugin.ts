import mermaid from 'mermaid';
import { D3RenderPlugin } from '../../types/d3';
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

// Mermaid plugin implementation
export const mermaidPlugin: D3RenderPlugin = {
    name: 'mermaid-renderer',
    priority: 5, // Give it a reasonable priority

    canHandle: (spec: any): boolean => {
        return isMermaidSpec(spec);
    },

    render: async (container: HTMLElement, d3: any, spec: MermaidSpec, isDarkMode: boolean): Promise<void> => {
        console.debug('Mermaid plugin rendering:', { spec, isDarkMode });

        // Clear previous content and show loading indicator
        container.innerHTML = ''; // Clear container
        const loadingDiv = document.createElement('div');
        loadingDiv.style.display = 'flex';
        loadingDiv.style.justifyContent = 'center';
        loadingDiv.style.alignItems = 'center';
        loadingDiv.style.minHeight = '150px'; // Ensure spinner is visible
        loadingDiv.innerHTML = `<span style="margin-right: 8px;">Rendering diagram...</span>`; // Basic text indicator
        container.appendChild(loadingDiv);
        // Note: Using Ant Design Spin directly here is complex, using simple text

        try {
            // Determine the theme
            const mermaidTheme = spec.theme || (isDarkMode ? 'dark' : 'default');

            // Initialize Mermaid (safe to call multiple times)
            mermaid.initialize({
                startOnLoad: false, // We manually render
                theme: mermaidTheme,                // Security level can be adjusted if needed, e.g., 'loose' if using complex HTML
                securityLevel: 'strict',
                // Add font settings if needed
                fontFamily: '"Arial", sans-serif',
                // Log level for mermaid debugging
                logLevel: 'warn',
            });            // Generate a unique ID for Mermaid rendering
            // Mermaid needs a unique ID to render into, even if we discard the temporary element
            const mermaidId = `mermaid-${Date.now()}-${Math.random().toString(16).substring(2)}`;

            // Render the diagram using the async API
            const { svg } = await mermaid.render(mermaidId, spec.definition);            // Render successful, replace loading indicator with SVG
            container.innerHTML = svg; // Replace container content with the rendered SVG

            const svgElement = container.querySelector('svg');
            if (svgElement) {
                // Let the SVG use its natural dimensions determined by Mermaid
                // The container's CSS will handle overflow and max-width
                svgElement.style.display = 'block'; // Good practice for SVG layout
                svgElement.style.margin = '0 auto'; // Center if container is wider
            }

            // Add "Open in New Window" button
            const openButton = document.createElement('button');
            openButton.innerText = 'Open Diagram';
            openButton.className = 'mermaid-open-button'; // Add class for styling
            openButton.onclick = () => {
                const svgString = container.innerHTML; // Get the current SVG content
                const dataUri = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svgString)))}`;
                window.open(dataUri, '_blank');
            }
            container.appendChild(openButton);

            console.debug('Mermaid diagram rendered successfully.');

        } catch (error: any) {
            console.error('Mermaid rendering error:', error);
            // Display error message in the container
            container.innerHTML = `
                <div class="mermaid-error" style="padding: 16px; border: 1px solid red; color: red; background-color: #ffeeee; border-radius: 4px;">
                    <strong>Mermaid Error:</strong>
                    <pre style="white-space: pre-wrap; word-wrap: break-word; margin-top: 8px;">${error.message || 'Unknown error'}</pre>
                    <details style="margin-top: 12px;">
                        <summary style="cursor: pointer;">Show Definition</summary>
                        <pre style="background-color: #f0f0f0; padding: 8px; border-radius: 4px; margin-top: 8px; color: #333;"><code>${spec.definition}</code></pre>                    </details>
                </div>
            `;
        }
    }
};
