/**
 * AppearancePopover — the header's Appearance button (replaces the theme
 * bulb).  Holds the global visual preferences: theme, light paper tone,
 * response font, density, prose width cap.  Everything applies live; the
 * conversation stays visible behind the popover so changes preview on real
 * content.  See design/visual-refresh.md.
 */
import React, { useEffect, useState } from 'react';
import { Button, Popover, Segmented, Switch, Tooltip } from 'antd';
import { BulbOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import {
    AppearancePrefs, FONT_LABELS, FONT_STACKS, PAPER_LABELS, PaperTone, ResponseFont,
    getAppearance, setAppearance, subscribeAppearance,
} from '../utils/appearance';

const PAPER_SWATCH: Record<PaperTone, string> = {
    warm: '#f6f3ee', linen: '#f7f6f3', cool: '#f4f5f4', white: '#ffffff',
};
const PAPERS: PaperTone[] = ['warm', 'linen', 'cool', 'white'];
const FONTS: ResponseFont[] = ['charter', 'georgia', 'palatino', 'sans'];

export function useAppearancePrefs(): AppearancePrefs {
    const [prefs, setPrefs] = useState<AppearancePrefs>(getAppearance);
    useEffect(() => subscribeAppearance(setPrefs), []);
    return prefs;
}

const THEME_KEY = 'ZIYA_THEME_PREFERENCE';

export const AppearancePanel: React.FC = () => {
    const { isDarkMode, setTheme } = useTheme();
    const prefs = useAppearancePrefs();
    const followsSystem = localStorage.getItem(THEME_KEY) === null;
    const themeValue = followsSystem ? 'system' : (isDarkMode ? 'dark' : 'light');

    const pickTheme = (v: string | number) => {
        if (v === 'system') {
            localStorage.removeItem(THEME_KEY);
            setTheme(window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
            // ThemeProvider's effect re-writes the key; clear it again so "system" sticks.
            setTimeout(() => localStorage.removeItem(THEME_KEY), 0);
        } else {
            setTheme(v as 'dark' | 'light');
        }
    };

    return (
        <div className="ziya-appearance" data-testid="appearance-panel">
            <div>
                <div className="ziya-appearance-label">Theme</div>
                <Segmented block size="small" value={themeValue} onChange={pickTheme}
                    options={[{ label: 'Light', value: 'light' }, { label: 'Dark', value: 'dark' }, { label: 'System', value: 'system' }]} />
            </div>
            <div>
                <div className="ziya-appearance-label">Light paper{isDarkMode ? ' (applies to Light)' : ''}</div>
                <div className="ziya-paper-row" role="radiogroup" aria-label="Light paper tone">
                    {PAPERS.map(p => (
                        <Tooltip key={p} title={PAPER_LABELS[p]}>
                            <button type="button" className="ziya-paper-swatch" aria-label={PAPER_LABELS[p]}
                                aria-pressed={prefs.paper === p} disabled={isDarkMode}
                                style={{ background: PAPER_SWATCH[p] }}
                                onClick={() => setAppearance({ paper: p })} />
                        </Tooltip>
                    ))}
                </div>
            </div>
            <div>
                <div className="ziya-appearance-label">Response font</div>
                <div className="ziya-font-grid">
                    {FONTS.map(f => (
                        <button key={f} type="button" className="ziya-font-choice" aria-pressed={prefs.font === f}
                            style={{ fontFamily: FONT_STACKS[f] }} onClick={() => setAppearance({ font: f })}>
                            {FONT_LABELS[f]}
                        </button>
                    ))}
                </div>
            </div>
            <div>
                <div className="ziya-appearance-label">Density</div>
                <Segmented block size="small" value={prefs.density}
                    onChange={v => setAppearance({ density: v as AppearancePrefs['density'] })}
                    options={[{ label: 'Compact', value: 'compact' }, { label: 'Comfortable', value: 'comfortable' }]} />
            </div>
            <div className="ziya-appearance-row">
                <span>Cap prose width for reading</span>
                <Switch size="small" checked={prefs.proseCap} onChange={v => setAppearance({ proseCap: v })} />
            </div>
        </div>
    );
};

export const AppearancePopover: React.FC = () => (
    <Popover trigger="click" placement="bottomRight" content={<AppearancePanel />} destroyTooltipOnHide>
        <Tooltip title="Appearance">
            <Button className="ziya-header-btn" type="text" icon={<BulbOutlined />} aria-label="Appearance"
                data-testid="appearance-button" />
        </Tooltip>
    </Popover>
);

export default AppearancePopover;
