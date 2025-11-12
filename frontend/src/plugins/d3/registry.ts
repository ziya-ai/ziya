import { D3RenderPlugin } from '../../types/d3';

// Plugin metadata for registration (lightweight)
interface PluginMetadata {
  name: string;
  priority: number;
  loader: () => Promise<D3RenderPlugin>;
}

// Lazy-loadable plugin registry
const pluginMetadata: PluginMetadata[] = [
  {
    name: 'network-diagram',
    priority: 1,
    loader: async () => (await import('./networkDiagram')).networkDiagramPlugin
  },
  {
    name: 'basic-chart',
    priority: 10,
    loader: async () => (await import('./basicChart')).basicChartPlugin
  },
  {
    name: 'mermaid-renderer',
    priority: 5,
    loader: async () => (await import('./mermaidPlugin')).mermaidPlugin
  },
  {
    name: 'graphviz-renderer',
    priority: 5,
    loader: async () => (await import('./graphvizPlugin')).graphvizPlugin
  },
  {
    name: 'vega-lite-renderer',
    priority: 8,
    loader: async () => (await import('./vegaLitePlugin')).vegaLitePlugin
  },
  {
    name: 'joint-renderer',
    priority: 6,
    loader: async () => (await import('./jointPlugin')).jointPlugin
  },
  {
    name: 'd2-renderer',
    priority: 6,
    loader: async () => (await import('./d2Plugin')).d2Plugin
  }
];

// Cache for loaded plugins
const loadedPlugins = new Map<string, D3RenderPlugin>();

/**
 * Dynamically load a plugin by name
 */
export async function loadPlugin(name: string): Promise<D3RenderPlugin | undefined> {
  // Return cached plugin if already loaded
  if (loadedPlugins.has(name)) {
    return loadedPlugins.get(name);
  }

  // Find and load the plugin
  const metadata = pluginMetadata.find(p => p.name === name);
  if (!metadata) {
    console.warn(`Plugin ${name} not found in registry`);
    return undefined;
  }

  try {
    const plugin = await metadata.loader();
    loadedPlugins.set(name, plugin);
    return plugin;
  } catch (error) {
    console.error(`Failed to load plugin ${name}:`, error);
    return undefined;
  }
}

/**
 * Get all available plugin metadata (without loading them)
 */
export function getAvailablePlugins(): Array<{ name: string; priority: number }> {
  return pluginMetadata.map(({ name, priority }) => ({ name, priority }));
}

/**
 * Load all plugins (for compatibility with old code)
 * WARNING: This defeats lazy loading - use sparingly
 */
export async function loadAllPlugins(): Promise<D3RenderPlugin[]> {
  const plugins = await Promise.all(
    pluginMetadata.map(async ({ name }) => await loadPlugin(name))
  );
  return plugins.filter((p): p is D3RenderPlugin => p !== undefined);
}

// For backward compatibility - but this defeats lazy loading!
// TODO: Remove this and update all callers to use loadPlugin() instead
const pluginRegistry: D3RenderPlugin[] = [];

pluginRegistry.forEach(plugin => {
    if (!plugin.name) {
        throw new Error('Plugin missing required name property');
    }
    if (typeof plugin.priority !== 'number') {
        throw new Error(`Plugin ${plugin.name} missing required priority number`);
    }
    if (typeof plugin.canHandle !== 'function') {
        throw new Error(`Plugin ${plugin.name} missing required canHandle function`);
    }
    if (typeof plugin.render !== 'function') {
        throw new Error(`Plugin ${plugin.name} missing required render function`);
    }
});

// Export empty array - plugins should be loaded dynamically now
export const d3RenderPlugins = Object.freeze(pluginRegistry);

/**
 * Get plugin by name (lazy loads if needed)
 */
export const getPluginByName = async (name: string): Promise<D3RenderPlugin | undefined> => {
  return await loadPlugin(name);
};

/**
 * Find and load the best plugin for a given spec
 */
export async function findPluginForSpec(spec: any): Promise<D3RenderPlugin | undefined> {
  // Sort metadata by priority
  const sortedMetadata = [...pluginMetadata].sort((a, b) => b.priority - a.priority);
  
  // Try each plugin in priority order
  for (const metadata of sortedMetadata) {
    const plugin = await loadPlugin(metadata.name);
    if (plugin && plugin.canHandle(spec)) {
      console.debug(`Selected plugin ${plugin.name} for spec`);
      return plugin;
    }
  }
  
  console.warn('No plugin found for spec:', spec);
  return undefined;
}

/**
 * Preload specific plugins (useful for anticipated renders)
 */
export async function preloadPlugins(names: string[]): Promise<void> {
  await Promise.all(names.map(name => loadPlugin(name)));
}

/**
 * Clear plugin cache (useful for development/testing)
 */
export function clearPluginCache(): void {
  loadedPlugins.clear();
}
