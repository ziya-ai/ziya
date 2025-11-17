/**
 * Packet/Bytefield diagram renderer for protocol specifications.
 * Renders beautiful packet layout diagrams from simple JSON specifications.
 */

interface PacketField {
    name: string;
    bits: number;
    color?: string;
    value?: string;
    style?: 'solid' | 'dotted' | 'hatched';
    tooltip?: string;
}

interface PacketRow {
    fields: PacketField[];
    group?: string;
    brace?: 'left' | 'right' | 'both';
}

interface PacketSpec {
    type: 'packet';
    name?: string;
    bitWidth?: number;
    rows: PacketRow[];
    showBitNumbers?: boolean;
    cellHeight?: number;
    cellWidth?: number;
}

const DEFAULT_COLORS = {
    lightcyan: '#84ffff',
    lightgreen: '#a3ffa3',
    lightred: '#ffb3b3',
    lightblue: '#83b5c9',
    darkblue: '#2e5d6b',
    pink: '#de9999',
    gray: '#cccccc'
};

export class PacketDiagramRenderer {
    private spec: PacketSpec;
    private bitWidth: number;
    private cellWidth: number;
    private cellHeight: number;
    private fontSize: number;
    private padding: number;
    
    constructor(spec: PacketSpec) {
        this.spec = spec;
        this.bitWidth = spec.bitWidth || 8;
        this.cellWidth = spec.cellWidth || 60;
        this.cellHeight = spec.cellHeight || 40;
        this.fontSize = 12;
        this.padding = 40;
    }
    
    private getColor(color?: string): string {
        if (!color) return '#ffffff';
        
        // Check if it's a named color
        if (color in DEFAULT_COLORS) {
            return DEFAULT_COLORS[color as keyof typeof DEFAULT_COLORS];
        }
        
        // Otherwise use as-is (should be hex)
        return color;
    }
    
    private createPattern(svg: SVGSVGElement, id: string, color: string): void {
        const defs = svg.querySelector('defs') || svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'defs'));
        
        const pattern = document.createElementNS('http://www.w3.org/2000/svg', 'pattern');
        pattern.setAttribute('id', id);
        pattern.setAttribute('patternUnits', 'userSpaceOnUse');
        pattern.setAttribute('width', '4');
        pattern.setAttribute('height', '4');
        
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', '0');
        line.setAttribute('y1', '0');
        line.setAttribute('x2', '4');
        line.setAttribute('y2', '4');
        line.setAttribute('stroke', color);
        line.setAttribute('stroke-width', '1');
        
        pattern.appendChild(line);
        defs.appendChild(pattern);
    }
    
    private renderBitHeader(svg: SVGSVGElement, y: number): void {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', 'bit-header');
        
        for (let i = 0; i < this.bitWidth; i++) {
            const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', (this.padding + i * this.cellWidth + this.cellWidth / 2).toString());
            text.setAttribute('y', y.toString());
            text.setAttribute('text-anchor', 'middle');
            text.setAttribute('font-size', (this.fontSize - 2).toString());
            text.setAttribute('font-family', 'monospace');
            text.setAttribute('fill', '#666');
            text.textContent = i.toString();
            g.appendChild(text);
        }
        
        svg.appendChild(g);
    }
    
    private renderRow(svg: SVGSVGElement, row: PacketRow, y: number, rowIndex: number): number {
        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', `packet-row-${rowIndex}`);
        
        let currentBit = 0;
        
        row.fields.forEach((field, fieldIndex) => {
            const fieldWidth = (field.bits / this.bitWidth) * (this.cellWidth * this.bitWidth);
            const x = this.padding + currentBit * this.cellWidth;
            
            // Create field rectangle
            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('x', x.toString());
            rect.setAttribute('y', y.toString());
            rect.setAttribute('width', fieldWidth.toString());
            rect.setAttribute('height', this.cellHeight.toString());
            rect.setAttribute('stroke', '#000');
            rect.setAttribute('stroke-width', '1.5');
            
            const color = this.getColor(field.color);
            
            // Handle different styles
            if (field.style === 'hatched') {
                const patternId = `hatch-${rowIndex}-${fieldIndex}`;
                this.createPattern(svg, patternId, color);
                rect.setAttribute('fill', `url(#${patternId})`);
            } else {
                rect.setAttribute('fill', color);
            }
            
            // Add tooltip if provided
            if (field.tooltip) {
                const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                title.textContent = field.tooltip;
                rect.appendChild(title);
            }
            
            g.appendChild(rect);
            
            // Add field name text
            const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', (x + fieldWidth / 2).toString());
            text.setAttribute('y', (y + this.cellHeight / 2 + 4).toString());
            text.setAttribute('text-anchor', 'middle');
            text.setAttribute('font-size', this.fontSize.toString());
            text.setAttribute('font-weight', field.value ? 'bold' : 'normal');
            text.setAttribute('font-family', 'Arial, sans-serif');
            
            // Choose text color based on background
            const isDark = this.isColorDark(color);
            text.setAttribute('fill', isDark ? '#ffffff' : '#000000');
            
            // Add field name (with value if present)
            const displayText = field.value ? `${field.name}: ${field.value}` : field.name;
            
            // Handle dotted style for field names
            if (field.style === 'dotted' && field.name) {
                // Create dotted line effect with text
                const textNode = document.createElementNS('http://www.w3.org/2000/svg', 'tspan');
                textNode.textContent = `· · · ${displayText} · · ·`;
                text.appendChild(textNode);
            } else {
                text.textContent = displayText;
            }
            
            g.appendChild(text);
            
            currentBit += field.bits;
        });
        
        // Add group brace if specified
        if (row.group) {
            this.renderGroupBrace(svg, row, y, rowIndex);
        }
        
        svg.appendChild(g);
        return y + this.cellHeight;
    }
    
    private renderGroupBrace(svg: SVGSVGElement, row: PacketRow, y: number, rowIndex: number): void {
        if (!row.brace || !row.group) return;
        
        const totalWidth = this.cellWidth * this.bitWidth;
        const braceWidth = 15;
        
        if (row.brace === 'left' || row.brace === 'both') {
            const x = this.padding - braceWidth - 5;
            this.drawBrace(svg, x, y, this.cellHeight, 'left');
            
            // Add group label
            const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', (x - 5).toString());
            text.setAttribute('y', (y + this.cellHeight / 2).toString());
            text.setAttribute('text-anchor', 'end');
            text.setAttribute('font-size', (this.fontSize - 1).toString());
            text.setAttribute('fill', '#0066cc');
            text.textContent = row.group;
            svg.appendChild(text);
        }
        
        if (row.brace === 'right' || row.brace === 'both') {
            const x = this.padding + totalWidth + 5;
            this.drawBrace(svg, x, y, this.cellHeight, 'right');
            
            // Add group label
            const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', (x + braceWidth + 5).toString());
            text.setAttribute('y', (y + this.cellHeight / 2).toString());
            text.setAttribute('text-anchor', 'start');
            text.setAttribute('font-size', (this.fontSize - 1).toString());
            text.setAttribute('fill', '#0066cc');
            text.textContent = row.group;
            svg.appendChild(text);
        }
    }
    
    private drawBrace(svg: SVGSVGElement, x: number, y: number, height: number, side: 'left' | 'right'): void {
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        const width = 10;
        
        // Create curly brace path
        const mid = y + height / 2;
        const dir = side === 'left' ? -1 : 1;
        
        const d = [
            `M ${x} ${y}`,
            `Q ${x + dir * width} ${y} ${x + dir * width} ${y + 5}`,
            `L ${x + dir * width} ${mid - 5}`,
            `Q ${x + dir * width} ${mid} ${x + dir * width * 1.5} ${mid}`,
            `Q ${x + dir * width} ${mid} ${x + dir * width} ${mid + 5}`,
            `L ${x + dir * width} ${y + height - 5}`,
            `Q ${x + dir * width} ${y + height} ${x} ${y + height}`
        ].join(' ');
        
        path.setAttribute('d', d);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', '#0066cc');
        path.setAttribute('stroke-width', '1.5');
        
        svg.appendChild(path);
    }
    
    private isColorDark(color: string): boolean {
        // Simple brightness calculation
        const hex = color.replace('#', '');
        const r = parseInt(hex.substr(0, 2), 16);
        const g = parseInt(hex.substr(2, 2), 16);
        const b = parseInt(hex.substr(4, 2), 16);
        const brightness = (r * 299 + g * 587 + b * 114) / 1000;
        return brightness < 128;
    }
    
    public render(container: HTMLElement): void {
        const totalWidth = this.cellWidth * this.bitWidth + this.padding * 2;
        const headerHeight = this.spec.showBitNumbers !== false ? 25 : 5;
        const titleHeight = this.spec.name ? 30 : 0;
        const totalHeight = titleHeight + headerHeight + (this.spec.rows.length * this.cellHeight) + this.padding;
        
        // Create SVG
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', totalWidth.toString());
        svg.setAttribute('height', totalHeight.toString());
        svg.setAttribute('viewBox', `0 0 ${totalWidth} ${totalHeight}`);
        svg.style.maxWidth = '100%';
        svg.style.height = 'auto';
        
        let currentY = this.padding / 2;
        
        // Render title if present
        if (this.spec.name) {
            const title = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            title.setAttribute('x', (totalWidth / 2).toString());
            title.setAttribute('y', currentY.toString());
            title.setAttribute('text-anchor', 'middle');
            title.setAttribute('font-size', (this.fontSize + 4).toString());
            title.setAttribute('font-weight', 'bold');
            title.setAttribute('font-family', 'Arial, sans-serif');
            title.textContent = this.spec.name;
            svg.appendChild(title);
            currentY += titleHeight;
        }
        
        // Render bit number header
        if (this.spec.showBitNumbers !== false) {
            this.renderBitHeader(svg, currentY + 15);
            currentY += headerHeight;
        }
        
        // Render each row
        this.spec.rows.forEach((row, index) => {
            currentY = this.renderRow(svg, row, currentY, index);
        });
        
        // Clear container and add SVG
        container.innerHTML = '';
        container.appendChild(svg);
    }
}

// Export for D3Renderer integration
export const renderPacketDiagram = (container: HTMLElement, spec: PacketSpec): void => {
    const renderer = new PacketDiagramRenderer(spec);
    renderer.render(container);
};
