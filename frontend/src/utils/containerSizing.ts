import { PluginSizingConfig } from '../types/d3';

export class ContainerSizingManager {
  private resizeObservers: Map<HTMLElement, ResizeObserver> = new Map();
  private cleanupFunctions: Set<() => void> = new Set();

  /**
   * Apply sizing configuration to a container based on plugin requirements
   */
  applySizingConfig(
    container: HTMLElement,
    config: PluginSizingConfig,
    isDarkMode: boolean
  ): void {
    // Apply container styles
    if (config.containerStyles) {
      Object.assign(container.style, config.containerStyles);
    }

    // Apply sizing strategy
    switch (config.sizingStrategy) {
      case 'responsive':
        container.style.width = '100%';
        container.style.maxWidth = '100%';
        if (config.needsDynamicHeight) {
          container.style.height = 'auto';
          container.style.minHeight = config.minHeight ? `${config.minHeight}px` : 'auto';
        }
        break;
      
      case 'content-driven':
        container.style.width = '100%';
        container.style.height = 'auto';
        container.style.minHeight = 'auto';
        break;
      
      case 'auto-expand':
        container.style.width = '100%';
        container.style.height = 'auto';
        container.style.minHeight = config.minHeight ? `${config.minHeight}px` : '200px';
        break;
      
      case 'fixed':
      default:
        if (config.minWidth) container.style.minWidth = `${config.minWidth}px`;
        if (config.minHeight) container.style.minHeight = `${config.minHeight}px`;
        break;
    }

    // Handle overflow
    if (config.needsOverflowVisible) {
      container.style.overflow = 'visible';
      this.updateParentOverflow(container);
    }

    // Set up resize observation if needed
    if (config.observeResize) {
      this.setupResizeObserver(container, config);
    }
  }

  private updateParentOverflow(container: HTMLElement): void {
    let parent = container.parentElement;
    while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-container'))) {
      parent.style.overflow = 'visible';
      parent = parent.parentElement;
    }
  }

  private setupResizeObserver(container: HTMLElement, config: PluginSizingConfig): void {
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const actualHeight = entry.contentRect.height;
        const actualWidth = entry.contentRect.width;
        
        this.adjustParentContainers(container, actualWidth, actualHeight, config);
      }
    });

    // Observe the main content element (could be svg, .vega-embed, etc.)
    setTimeout(() => {
      const contentElement = container.querySelector('svg, .vega-embed, .mermaid, .graphviz') as HTMLElement;
      if (contentElement) {
        observer.observe(contentElement);
        this.resizeObservers.set(container, observer);
      }
    }, 100);
  }

  private adjustParentContainers(
    container: HTMLElement,
    actualWidth: number,
    actualHeight: number,
    config: PluginSizingConfig
  ): void {
    if (!config.needsDynamicHeight) return;

    let parent = container.parentElement;
    while (parent && (parent.classList.contains('d3-container') || parent.classList.contains('vega-lite-container'))) {
      const parentElement = parent as HTMLElement;
      const currentHeight = parentElement.getBoundingClientRect().height;
      
      if (currentHeight < actualHeight + 40) { // Add padding
        parentElement.style.height = `${actualHeight + 40}px`;
        parentElement.style.minHeight = `${actualHeight + 40}px`;
      }
      
      if (!parentElement.style.width || parentElement.style.width === 'auto') {
        parentElement.style.width = '100%';
        parentElement.style.maxWidth = '100%';
      }
      
      parent = parent.parentElement;
    }
  }

  /**
   * Clean up all observers and resources
   */
  cleanup(): void {
    this.resizeObservers.forEach(observer => observer.disconnect());
    this.resizeObservers.clear();
    this.cleanupFunctions.forEach(cleanup => cleanup());
    this.cleanupFunctions.clear();
  }

  /**
   * Add a cleanup function to be called when the manager is destroyed
   */
  addCleanupFunction(cleanup: () => void): void {
    this.cleanupFunctions.add(cleanup);
  }
}
