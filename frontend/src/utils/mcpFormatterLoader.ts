/**
 * Dynamic formatter loader for plugin-provided formatters
 */
import { formatterRegistry } from './formatterRegistry';

// Track which formatter URLs have already been injected this page lifetime
const loadedFormatterUrls = new Set<string>();

// templates/index.html already emits a <script src> for every formatter the
// server lists (formatter_scripts), and /api/config returns the same list.
// Injecting it again ran each plugin twice and registered it twice.
function scriptAlreadyInPage(path: string): boolean {
    const href = new URL(path, window.location.href).href;
    return Array.from(document.scripts).some(s => s.src === href);
}

export async function loadFormatters(): Promise<void> {
    try {
        // Get config from backend
        const response = await fetch('/api/config');
        if (!response.ok) {
            console.warn('Could not load config for formatters');
            return;
        }
        
        const config = await response.json();
        const formatterPaths = config?.frontend?.formatters || [];
        
        if (formatterPaths.length === 0) {
            console.log('No external formatters configured');
            return;
        }
        
        console.log(`Loading ${formatterPaths.length} external formatter(s)...`);
        
        // Load each formatter script
        for (const path of formatterPaths) {
            // Skip if already injected (page template, or hot reload re-invoking this function)
            if (loadedFormatterUrls.has(path) || scriptAlreadyInPage(path)) {
                loadedFormatterUrls.add(path);
                console.log(`⏭️ Formatter already loaded, skipping: ${path}`);
                continue;
            }

            await new Promise<void>((resolve, reject) => {
                const script = document.createElement('script');
                script.src = path;
                script.async = true;
                script.onload = () => {
                    loadedFormatterUrls.add(path);
                    console.log(`✅ Loaded formatter: ${path}`);
                    resolve();
                };
                script.onerror = (error) => {
                    console.error(`❌ Failed to load formatter: ${path}`, error);
                    reject(new Error(`Failed to load ${path}`));
                };
                document.head.appendChild(script);
            });
        }
    } catch (error) {
        console.debug('Error loading formatters:', error);
    }
}
