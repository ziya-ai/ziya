/**
 * One container for the members of a parallel tool batch.
 *
 * Built by renderTokens from a 'tool_group' token (utils/toolGroupTokens.ts).
 * The header carries progress while the group runs and the wall-vs-serial
 * verdict once it ends; the members render inside, along a rail.
 */
import React from 'react';
import { groupTiming } from '../utils/toolGroupDisplay';
import '../styles/toolGroup.css';

export type ToolGroupState = 'running' | 'done' | 'interrupted';

interface Palette { border: string; header: string; accent: string; track: string; body: string }

const DARK: Record<ToolGroupState, Palette> = {
    running: { border: '#3b3a63', header: '#2d2d44', accent: '#a78bfa', track: '#1a1a2e', body: '#16172a' },
    done: { border: '#2f4a3a', header: '#24352d', accent: '#86efac', track: '#1a1a2e', body: '#16172a' },
    interrupted: { border: '#4a3f2a', header: '#3a3222', accent: '#fbbf24', track: '#1a1a2e', body: '#16172a' },
};
const LIGHT: Record<ToolGroupState, Palette> = {
    running: { border: '#b8b5e6', header: '#e9e7fb', accent: '#5b3fb8', track: '#d6d3f3', body: '#f7f6fd' },
    done: { border: '#9fd3b0', header: '#e3f4e8', accent: '#1e7a3d', track: '#cdebd6', body: '#f6fbf7' },
    interrupted: { border: '#e8cf8f', header: '#fdf3d7', accent: '#8a5a00', track: '#f3e3b5', body: '#fffbf0' },
};

export interface ToolGroupProps {
    size: number;
    /** Members with a result so far. */
    done: number;
    /** Timing from the END marker; null while running or if it never closed. */
    end: { wall_ms: number; serial_ms: number } | null;
    /** Whether the message holding the group is still streaming. */
    streaming: boolean;
    isDarkMode: boolean;
    children?: React.ReactNode;
}

export const ToolGroup: React.FC<ToolGroupProps> = ({ size, done, end, streaming, isDarkMode, children }) => {
    // A group in a finished message without an END was stopped mid-batch;
    // showing it as running would spin forever.
    const state: ToolGroupState = end ? 'done' : streaming ? 'running' : 'interrupted';
    const p = (isDarkMode ? DARK : LIGHT)[state];
    const title = (state === 'done' ? '✓ ' : '⫽ ') + size + ' calls in parallel';
    const meta = state === 'done' && end
        ? groupTiming(end)
        : state === 'running' ? done + ' / ' + size + ' done' : 'stopped · ' + done + ' / ' + size + ' finished';
    return (
        <div className="tool-group" data-testid="tool-group" data-state={state} style={{
            border: '2px solid ' + p.border, borderRadius: 12, margin: '16px 0',
            overflow: 'hidden', background: p.body,
        }}>
            <div className="tool-group-header" style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
                background: p.header, color: p.accent,
                fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                fontSize: 12, fontWeight: 'bold', letterSpacing: '0.5px',
            }}>
                <span>{title}</span>
                {state === 'running' && (
                    <span role="progressbar" aria-valuemin={0} aria-valuemax={size} aria-valuenow={done} style={{
                        flex: 1, height: 4, borderRadius: 2, background: p.track, overflow: 'hidden',
                    }}>
                        <span style={{
                            display: 'block', height: '100%', background: p.accent,
                            width: (size ? (100 * done) / size : 0) + '%', transition: 'width 0.3s ease',
                        }} />
                    </span>
                )}
                <span style={{ marginLeft: state === 'running' ? 0 : 'auto', fontWeight: 'normal', opacity: 0.85 }}>
                    {meta}
                </span>
            </div>
            <div className="tool-group-body" style={{ position: 'relative', padding: '8px 10px 8px 22px' }}>
                <div className="tool-group-rail" aria-hidden style={{
                    position: 'absolute', left: 11, top: 12, bottom: 12, width: 2,
                    background: p.border, borderRadius: 1,
                }} />
                {children}
            </div>
        </div>
    );
};

export default ToolGroup;
