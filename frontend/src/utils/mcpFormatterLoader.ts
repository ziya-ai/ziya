/**
 * Dynamic formatter loader for plugin-provided formatters
 */
import { formatterRegistry } from './formatterRegistry';

// Track which formatter URLs have already been injected this page lifetime
const loadedFormatterUrls = new Set<string>();

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
            // Skip if already injected (e.g. hot reload re-invoking this function)
            if (loadedFormatterUrls.has(path)) {
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
