/**
 * Shared color utility functions for D3 plugins
 * Consolidates duplicate implementations from graphviz, mermaid, and vega plugins
 */

/**
 * Convert hex color to RGB components
 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
    } : null;
}

/**
 * Get luminance component for a color value (0-255)
 */
export function getLuminanceComponent(c: number): number {
    const normalized = c / 255;
    return normalized <= 0.03928
        ? normalized / 12.92
        : Math.pow((normalized + 0.055) / 1.055, 2.4);
}

/**
 * Calculate relative luminance of an RGB color
 * Returns value between 0 (darkest) and 1 (lightest)
 */
export function luminance(r: number, g: number, b: number): number {
    const rLum = getLuminanceComponent(r);
    const gLum = getLuminanceComponent(g);
    const bLum = getLuminanceComponent(b);
    return 0.2126 * rLum + 0.7152 * gLum + 0.0722 * bLum;
}

/**
 * Determine if a background color is light
 * Handles hex, rgb(), and named color formats
 */
export function isLightBackground(color: string): boolean {
    if (!color || color === 'transparent' || color === 'none') {
        return false;
    }

    // Parse color to RGB values
    let r = 0, g = 0, b = 0;

    // Handle hex format
    const hexMatch = color.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
    if (hexMatch) {
        r = parseInt(hexMatch[1], 16);
        g = parseInt(hexMatch[2], 16);
        b = parseInt(hexMatch[3], 16);
    }
    // Handle rgb() format
    else if (color.startsWith('rgb')) {
        const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        if (rgbMatch) {
            r = parseInt(rgbMatch[1]);
            g = parseInt(rgbMatch[2]);
            b = parseInt(rgbMatch[3]);
        } else {
            return false;
        }
    }
    // Handle named colors
    else {
        const lightNamedColors = [
            'white', 'lightblue', 'lightgreen', 'lightyellow', 'lightgrey', 'lightgray', 'pink',
            'yellow', '#aed6f1', '#d4e6f1', '#d5f5e3', '#f5f5f5', '#e6e6e6', '#f0f0f0',
            '#ffffff', '#f8f9fa', '#e9ecef', '#dee2e6', '#ced4da', '#adb5bd'
        ];
        return lightNamedColors.some(c => c.toLowerCase() === color.toLowerCase());
    }

    // Calculate proper sRGB luminance
    const lum = luminance(r, g, b);

    // Use threshold where anything above 0.4 luminance is considered light
    return lum > 0.4;
}

/**
 * Get optimal text color (black or white) for a given background
 * Includes special handling for yellow and yellow-ish colors
 */
export function getOptimalTextColor(backgroundColor: string): string {
    const rgb = hexToRgb(backgroundColor);
    if (!rgb) {
        // If we can't parse as hex, try rgb() format
        const rgbMatch = backgroundColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (!rgbMatch) return '#ffffff'; // Default to white for unparseable colors
        const r = parseInt(rgbMatch[1]), g = parseInt(rgbMatch[2]), b = parseInt(rgbMatch[3]);
        return getOptimalTextColor(`#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`);
    }

    // Calculate proper sRGB luminance
    const lum = luminance(rgb.r, rgb.g, rgb.b);
    
    // Special handling for yellow and light yellow (expanded to catch all variants)
    // Yellow has high R and G, with B significantly lower than both
    if (rgb.r > 180 && rgb.g > 180 && rgb.b < Math.min(rgb.r - 30, rgb.g - 30)) {
        return '#000000'; // Always use black on yellow/light yellow
    }
    
    // Special handling for light blue colors (including journey diagram blue #aed6f1)
    // These colors have high blue component and appear light, needing dark text
    if (rgb.b > 200 && rgb.r > 150 && rgb.g > 180) {
        return '#000000'; // Use black on light blue backgrounds
    }
    
    // Special handling for medium blue/cyan (high blue but appears light despite low luminance)
    // Blue coefficient in luminance is only 0.0722, so these colors appear darker than they look
    if (rgb.b > 180 && rgb.r > 100 && rgb.g > 100 && Math.abs(rgb.r - rgb.g) < 60) {
        return '#000000'; // Use black on light blue/cyan (baby blue)
    }
    
    // Special handling for grey colors (all channels similar, medium to high brightness)
    // Only use black on LIGHT greys (>160), otherwise use white
    if (Math.abs(rgb.r - rgb.g) < 30 && Math.abs(rgb.g - rgb.b) < 30) {
        return rgb.r > 160 ? '#000000' : '#ffffff';
    }
    
    // SIMPLE AGGRESSIVE RULE: If ANY channel is very bright, likely needs black text
    const maxChannel = Math.max(rgb.r, rgb.g, rgb.b);
    const minChannel = Math.min(rgb.r, rgb.g, rgb.b);
    if (minChannel > 100 && maxChannel > 180) {
        return '#000000'; // Light pastel colors need black text
    }
    
    // Use WCAG-based threshold as final fallback: luminance > 0.5 is considered light
    return lum > 0.5 ? '#000000' : '#ffffff';
}


/**
 * Calculate contrast ratio between two colors
 * Returns value >= 1 (1 = no contrast, 21 = maximum contrast)
 * WCAG AA requires 4.5:1 for normal text, 3:1 for large text
 * 
 * @param color1 - First color (hex or rgb)
 * @param color2 - Second color (hex or rgb)
 * @returns Contrast ratio between 1 and 21
 */
export function calculateContrastRatio(color1: string, color2: string): number {
    // Handle various color formats
    const parseColor = (color: string): { r: number; g: number; b: number } | null => {
        // Handle named colors
        const namedColors: Record<string, string> = {
            'white': '#ffffff',
            'black': '#000000',
            'red': '#ff0000',
            'green': '#008000',
            'blue': '#0000ff',
            'yellow': '#ffff00',
            'cyan': '#00ffff',
            'magenta': '#ff00ff',
            'gray': '#808080',
            'grey': '#808080',
            'transparent': '#ffffff',
            'none': '#ffffff'
        };
        
        // Convert named color to hex
        const normalizedColor = color.toLowerCase().trim();
        if (namedColors[normalizedColor]) {
            color = namedColors[normalizedColor];
        }
        
        // Try hex first
        const hexResult = hexToRgb(color);
        if (hexResult) return hexResult;

        // Try rgb() format
        const rgbMatch = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (rgbMatch) {
            return {
                r: parseInt(rgbMatch[1]),
                g: parseInt(rgbMatch[2]),
                b: parseInt(rgbMatch[3])
            };
        }

        return null;
    };

    const rgb1 = parseColor(color1);
    const rgb2 = parseColor(color2);

    if (!rgb1 || !rgb2) {
        console.warn('🔍 COLOR-PARSE-FAIL:', {
            color1, color2, rgb1, rgb2
        });
        return 1;
    }

    const lum1 = luminance(rgb1.r, rgb1.g, rgb1.b);
    const lum2 = luminance(rgb2.r, rgb2.g, rgb2.b);

    const lighter = Math.max(lum1, lum2);
    const darker = Math.min(lum1, lum2);

    return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Find background color for an SVG element by searching parents and siblings
 * Uses multiple strategies to detect the actual background color
 * 
 * @param element - The element to find background for
 * @param defaultBg - Fallback background color
 * @returns The detected background color or default
 */
export function findElementBackground(element: Element, defaultBg: string = '#ffffff'): string {
    let backgroundColor: string | null = null;

    // Strategy 1: Check for fill attribute on parent elements (Graphviz nodes)
    // In Graphviz, text elements are inside <g> elements that have the fill color
    const parentGroup = element.closest('g');
    if (parentGroup) {
        // First, check if the parent group itself has a fill
        const parentFill = parentGroup.getAttribute('fill');
        if (parentFill && parentFill !== 'none') {
            console.log('Found parent group fill:', parentFill);
            return parentFill;
        }
        
        // Next, check for background shapes BEFORE the text element
        // These are rendered first and provide the background
        const backgroundShape = parentGroup.querySelector('rect, ellipse, polygon, circle, path[fill]:not([fill="none"])');
        if (backgroundShape) {
            const fill = backgroundShape.getAttribute('fill');
            const computedFill = window.getComputedStyle(backgroundShape).fill;
            backgroundColor = (computedFill && computedFill !== 'none' && computedFill !== 'rgb(0, 0, 0)')
                ? computedFill
                : fill;
            
            if (backgroundColor && backgroundColor !== 'none') {
                console.log('Found background shape fill:', backgroundColor);
                return backgroundColor;
            }
        }
    }

    // Strategy 2: For Graphviz, check siblings for filled shapes at the same level
    if (parentGroup) {
        const siblings = parentGroup.querySelectorAll('ellipse[fill]:not([fill="none"]), polygon[fill]:not([fill="none"]), path[fill]:not([fill="none"])');
        if (siblings.length > 0) {
            const firstSiblingFill = siblings[0].getAttribute('fill');
            if (firstSiblingFill && firstSiblingFill !== 'none') {
                console.log('Found sibling shape fill:', firstSiblingFill);
                return firstSiblingFill;
            }
        }
    }

    // Strategy 3: Check computed background from CSS
    if (!backgroundColor) {
        const computedBg = window.getComputedStyle(element).backgroundColor;
        if (computedBg && computedBg !== 'rgba(0, 0, 0, 0)' && computedBg !== 'transparent') {
            backgroundColor = computedBg;
        }
    }

    // Strategy 4: For legend items, use page background (text should contrast with page, not color box)
    if (!backgroundColor && parentGroup) {
        if (parentGroup.classList.contains('legend') ||
            parentGroup.closest('.legend') ||
            element.closest('.legend')) {
            return defaultBg;
        }
    }

    return backgroundColor || defaultBg;
}

/**
 * Universal SVG visibility enhancer
 * Works with ANY SVG diagram (Mermaid, Graphviz, DrawIO, Vega, etc.)
 * Fixes text, shapes, and lines to ensure proper contrast
 * 
 * @param svgElement - The SVG element to enhance
 * @param isDarkMode - Whether dark mode is active
 * @param options - Optional configuration
 * @returns Statistics about elements fixed
 */
export interface VisibilityEnhancerOptions {
    /** Minimum contrast ratio (default: 3.0 for accessibility) */
    minContrast?: number;
    /** Skip elements with these classes */
    skipClasses?: string[];
    /** Skip elements matching these selectors */
    skipSelectors?: string[];
    /** Debug logging */
    debug?: boolean;
}

export function enhanceSVGVisibility(
    svgElement: SVGElement,
    isDarkMode: boolean,
    options: VisibilityEnhancerOptions = {}
): { textFixed: number; shapesFixed: number; linesFixed: number } {
    const {
        minContrast = 3.0,
        skipClasses = [],
        skipSelectors = [],
        debug = false
    } = options;

    const log = debug ? console.log : () => { };
    const pageBg = isDarkMode ? '#2e3440' : '#ffffff';
    const defaultTextColor = isDarkMode ? '#eceff4' : '#333333';
    const defaultStrokeColor = isDarkMode ? '#88c0d0' : '#333333';

    let textFixed = 0;
    let shapesFixed = 0;
    let linesFixed = 0;

    log('🔍 UNIVERSAL-SVG-FIX: Starting visibility enhancement');

    // FIX 1a: Check for foreignObject HTML text (Mermaid v10+)
    const foreignObjects = svgElement.querySelectorAll('foreignObject');
    foreignObjects.forEach((fo) => {
        const htmlElements = fo.querySelectorAll('div, span, p');
        htmlElements.forEach((htmlEl) => {
            const textContent = htmlEl.textContent?.trim();
            if (!textContent) return;

            const backgroundColor = findElementBackground(htmlEl, pageBg);
            const optimalColor = getOptimalTextColor(backgroundColor);
            const currentStyle = window.getComputedStyle(htmlEl);
            const currentColor = currentStyle.color || defaultTextColor;

            const contrast = calculateContrastRatio(currentColor, backgroundColor);
            const textInvisible = currentColor === backgroundColor ||
                                 (isDarkMode && currentColor === '#000000') ||
                                 (!isDarkMode && currentColor === '#ffffff');

            log(`🔍 HTML-TEXT: "${textContent.substring(0, 30)}" contrast=${contrast.toFixed(2)} current=${currentColor} bg=${backgroundColor}`);

            if (contrast < minContrast || textInvisible) {
                (htmlEl as HTMLElement).style.setProperty('color', optimalColor, 'important');
                textFixed++;
                log(`🔧 HTML text fix: "${textContent.substring(0, 30)}" -> ${optimalColor}`);
            }
        });
    });

    // FIX 1b: SVG text elements (older Mermaid, Graphviz, etc.)
    const textElements = svgElement.querySelectorAll('text');
    textElements.forEach((textEl) => {
        // Skip if in skip list
        if (skipClasses.some(cls => textEl.classList.contains(cls))) return;
        if (skipSelectors.some(sel => textEl.matches(sel))) return;

        const textContent = textEl.textContent?.trim();
        if (!textContent) return;

        const backgroundColor = findElementBackground(textEl, pageBg);
        const optimalColor = getOptimalTextColor(backgroundColor);
        
        // CRITICAL: Use computed style if no fill attribute (ER diagrams use CSS classes)
        const fillAttr = textEl.getAttribute('fill');
        const currentColor = fillAttr || window.getComputedStyle(textEl).fill || defaultTextColor;
        
        // CRITICAL DEBUG: Log every text element analysis
        log(`🔍 TEXT-ANALYSIS: "${textContent.substring(0, 30)}"`, {
            currentColor,
            backgroundColor,
            optimalColor,
            pageBg,
            defaultTextColor,
            element: textEl.tagName,
            parentClass: textEl.parentElement?.getAttribute('class'),
            hasComputedStyle: !!window.getComputedStyle(textEl).fill
        });
        
        // Check if current color has sufficient contrast
        const contrast = calculateContrastRatio(currentColor, backgroundColor);
        
        // Also check if text is actually visible (not matching background exactly)
        const textInvisible = currentColor === backgroundColor ||
                             (isDarkMode && currentColor === '#000000') ||
                             (!isDarkMode && currentColor === '#ffffff');
        
        // CRITICAL DEBUG: Log contrast analysis before fix decision
        log(`🔍 CONTRAST-CHECK: "${textContent.substring(0, 30)}"`, {
            contrast: contrast.toFixed(2),
            minContrast,
            textInvisible,
            willFix: contrast < minContrast || textInvisible
        });
        
        if (contrast < minContrast || textInvisible) {
            textEl.setAttribute('fill', optimalColor);
            (textEl as SVGElement).style.setProperty('fill', optimalColor, 'important');
            textFixed++;
            log(`🔧 Text fix: "${textContent.substring(0, 30)}" -> ${optimalColor} (was ${currentColor}, contrast: ${contrast.toFixed(2)})`);
        }
    });

    // FIX 2: ALL shapes - ensure visible strokes ONLY (preserve fill colors)
    const shapes = svgElement.querySelectorAll('rect, ellipse, polygon, circle, path[fill]');
    shapes.forEach((shape) => {
        const fill = shape.getAttribute('fill');
        const stroke = shape.getAttribute('stroke');

        // Only fix strokes for shapes that have an explicit stroke or should have one
        // Don't add strokes to pie slices (path with fill but no stroke)
        const isPieSlice = shape.tagName === 'path' && 
                          fill && fill !== 'none' && 
                          !stroke;
        
        if (!isPieSlice) {
            // Fix invisible strokes (but don't add strokes where none exist)
            if (stroke && (stroke === 'none' || 
                (isDarkMode && (stroke === '#000000' || stroke === pageBg)) ||
                (!isDarkMode && (stroke === '#ffffff' || stroke === 'white')))) {
                shape.setAttribute('stroke', defaultStrokeColor);
                shapesFixed++;
                log(`🔧 Shape stroke fix: ${stroke} -> ${defaultStrokeColor}`);
            }
        }
        // REMOVED: Fill color modification - preserve user's color choices
    });

    // FIX 3: ALL lines and connection paths - ensure visible strokes
    // CRITICAL: Be VERY aggressive - catch ALL path elements that might be lines
    // This includes journey diagrams, flowcharts, sequence diagrams, etc.
    const lines = svgElement.querySelectorAll(
        'line, ' +
        'path[d]:not([fill]), ' +
        'path[fill="none"], ' +
        'path.path, ' +
        'path[class*="journey"], ' +
        'path[class*="line"], ' +
        'path[stroke]');  // ANY path with a stroke attribute
    
    log(`🔍 LINE-SCAN: Found ${lines.length} line elements to check`);
    
    lines.forEach((line) => {
        const stroke = line.getAttribute('stroke');
        const currentStrokeWidth = parseFloat(line.getAttribute('stroke-width') || '0');

        // Fix invisible strokes or strokes that match background
        const strokeInvisible = !stroke || stroke === 'none' ||
            stroke === pageBg ||
            (isDarkMode && (stroke === '#000000' || stroke === 'black' || stroke === '#2e3440')) ||
            (!isDarkMode && (stroke === '#ffffff' || stroke === 'white'));

        // CRITICAL: Also check for low-contrast grey strokes in dark mode
        // Grey (#808080, #999999, etc.) is invisible on dark grey backgrounds
        const isLowContrastGrey = isDarkMode && stroke && (
            stroke.toLowerCase() === '#808080' ||
            stroke.toLowerCase() === '#999999' ||
            stroke.toLowerCase() === '#666666' ||
            stroke.toLowerCase() === 'grey' ||
            stroke.toLowerCase() === 'gray'
        );

        if (strokeInvisible) {
            line.setAttribute('stroke', defaultStrokeColor);
            line.setAttribute('stroke-width', '2');
            linesFixed++;
            log(`🔧 Line fix: invisible stroke -> ${defaultStrokeColor}`);
        } else if (isLowContrastGrey) {
            // Fix low-contrast grey lines in dark mode
            line.setAttribute('stroke', defaultStrokeColor);
            (line as SVGElement).style.setProperty('stroke', defaultStrokeColor, 'important');
            if (currentStrokeWidth < 1.5) {
                line.setAttribute('stroke-width', '2');
            }
            linesFixed++;
            log(`🔧 Line fix: low-contrast grey (${stroke}) -> ${defaultStrokeColor}`);
        } else if (currentStrokeWidth < 0.5) {
            // Stroke exists but is too thin to see
            line.setAttribute('stroke-width', '1.5');
            linesFixed++;
            log(`🔧 Line fix: stroke too thin -> 1.5px`);
        }
    });

    log(`🔍 UNIVERSAL-SVG-FIX: Enhanced ${textFixed} text, ${shapesFixed} shapes, ${linesFixed} lines`);

    return { textFixed, shapesFixed, linesFixed };
}
