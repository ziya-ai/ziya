/**
 * Transcript row for a server-authored `projectChange` system message: the
 * conversation was moved to another project, or made global / scoped back.
 *
 * The row states the projects and their root directories as text.  The same
 * facts are in the message's `content`, which is what the model is sent
 * (app/utils/project_change_note.py).  Nothing here is carried by a title
 * attribute: a tooltip is not visible to anything that reads the page.
 */
import React from 'react';
import { useTheme } from '../context/ThemeContext';

export interface ProjectRef {
    id?: string | null;
    name?: string | null;
    path?: string | null;
}

export interface ProjectChange {
    kind: 'moved' | 'global_on' | 'global_off';
    from: ProjectRef;
    to: ProjectRef;
}

const label = (p?: ProjectRef): string => p?.name || p?.id || 'unknown project';
const dir = (p?: ProjectRef): string => p?.path || 'unknown';

export function projectChangeText(change: ProjectChange): string {
    if (change.kind === 'moved') {
        return `Moved from project ${label(change.from)} to project ${label(change.to)}`;
    }
    if (change.kind === 'global_on') {
        return `Made global (owned by project ${label(change.from)})`;
    }
    return `No longer global: scoped to project ${label(change.from)}`;
}

export function projectChangeDirs(change: ProjectChange): string {
    return change.kind === 'moved'
        ? `Root directory: ${dir(change.from)} \u2192 ${dir(change.to)}`
        : `Root directory: ${dir(change.from)}`;
}

const ProjectChangeNotification: React.FC<{ change: ProjectChange }> = ({ change }) => {
    const { isDarkMode } = useTheme();
    const rule: React.CSSProperties = {
        flex: 1,
        borderTop: `1px solid ${isDarkMode ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.2)'}`,
    };
    return (
        <div data-testid="project-change" data-kind={change.kind}
             style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, opacity: 0.8, padding: '6px 0' }}>
            <div style={rule} />
            <div style={{ textAlign: 'center', minWidth: 0 }}>
                <div>{projectChangeText(change)}</div>
                <div style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                              fontSize: 11, overflowWrap: 'anywhere' }}>
                    {projectChangeDirs(change)}
                </div>
            </div>
            <div style={rule} />
        </div>
    );
};

export default ProjectChangeNotification;
