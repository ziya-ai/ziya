import { D3RenderPlugin } from '../../types/d3';
import { networkDiagramPlugin } from './networkDiagram';
import { basicChartPlugin } from './basicChart';
import { mermaidPlugin } from './mermaidPlugin';
import { graphvizPlugin } from './graphvizPlugin';
import { vegaLitePlugin } from './vegaLitePlugin';
import { jointPlugin } from './jointPlugin';
import { d2Plugin } from './d2Plugin';

// Registry for D3 visualization plugins
const pluginRegistry: D3RenderPlugin[] = [
    networkDiagramPlugin,
    basicChartPlugin,
    mermaidPlugin,
    graphvizPlugin,
    vegaLitePlugin,
    jointPlugin,
    d2Plugin
    // Add more plugins here as needed
];
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
export const d3RenderPlugins = Object.freeze(pluginRegistry);
export const getPluginByName = (name: string) => d3RenderPlugins.find(p => p.name === name);
