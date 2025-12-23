/**
 * Dynamic formatter loader for plugin-provided formatters
 */
import { formatterRegistry } from './formatterRegistry';

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
            await new Promise<void>((resolve, reject) => {
                const script = document.createElement('script');
                script.src = path;
                script.async = true;
                script.onload = () => {
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
