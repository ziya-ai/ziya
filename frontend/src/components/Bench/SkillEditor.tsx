/**
 * Skill editor form: create (BenchPanel's "＋ New skill", in the drawer slot)
 * and edit (CapabilityDetail, for a custom skill).  A renderer: it holds the
 * draft and hands it to ``onSave``; the owner decides what a save means.
 * Import reads a SKILL.md through the shared parser and fills the draft.
 */
import React, { useRef, useState } from 'react';
import { parseSkillMd, SKILL_MD_INVALID } from '../../utils/skillMd';

export interface SkillDraft { name: string; description: string; prompt: string; }

export interface SkillEditorProps {
    title: string;
    saveLabel: string;
    initial?: SkillDraft;
    onSave: (draft: SkillDraft) => Promise<unknown> | void;
    onCancel: () => void;
    /** Offer "Import SKILL.md" (create only; an edit has a body already). */
    allowImport?: boolean;
}

const border = '1px solid var(--mockup-border, rgba(127,127,127,0.35))';
const field: React.CSSProperties = { width: '100%', boxSizing: 'border-box', fontSize: 11, padding: '4px 6px',
                                     borderRadius: 5, border, background: 'none', color: 'inherit' };
const btn: React.CSSProperties = { fontSize: 11, padding: '3px 10px', borderRadius: 5, cursor: 'pointer', border,
                                   background: 'none', color: 'inherit' };

function readText(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result ?? ''));
        r.onerror = () => reject(r.error);
        r.readAsText(file);
    });
}

export const SkillEditor: React.FC<SkillEditorProps> = ({ title, saveLabel, initial, onSave, onCancel, allowImport }) => {
    const [draft, setDraft] = useState<SkillDraft>(initial ?? { name: '', description: '', prompt: '' });
    const [err, setErr] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const fileRef = useRef<HTMLInputElement>(null);
    const set = (k: keyof SkillDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        setDraft(d => ({ ...d, [k]: e.target.value }));
    const valid = draft.name.trim().length > 0 && draft.prompt.trim().length > 0;

    const save = async () => {
        if (!valid || busy) return;
        setBusy(true); setErr(null);
        try { await onSave(draft); }
        catch (e) { setErr(String((e as Error)?.message ?? e)); }
        finally { setBusy(false); }
    };
    const importFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        setErr(null);
        try {
            const parsed = parseSkillMd(await readText(file));
            if (!parsed) { setErr(SKILL_MD_INVALID); return; }
            setDraft({ name: parsed.name, description: parsed.description, prompt: parsed.prompt });
        } catch (ex) { setErr(String((ex as Error)?.message ?? ex)); }
    };

    return (
        <div data-testid="skill-editor" style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <b style={{ fontSize: 13 }}>{title}</b>
                {allowImport && (
                    <>
                        <button type="button" style={{ ...btn, marginLeft: 'auto' }} onClick={() => fileRef.current?.click()}>
                            Import SKILL.md
                        </button>
                        <input ref={fileRef} type="file" accept=".md" data-testid="skill-import-input"
                               style={{ display: 'none' }} onChange={e => void importFile(e)} />
                    </>
                )}
            </div>
            <input aria-label="Skill name" placeholder="Skill name" value={draft.name} onChange={set('name')} style={field} autoFocus />
            <input aria-label="Description" placeholder="Short description (when to use this skill)" value={draft.description}
                   onChange={set('description')} style={field} />
            <textarea aria-label="Prompt" placeholder="Instructions for the AI when this skill is active" value={draft.prompt}
                      onChange={set('prompt')} rows={12} style={{ ...field, fontFamily: 'monospace', fontSize: 10, resize: 'vertical' }} />
            {err && <div role="alert" style={{ color: '#d46b08' }}>{err}</div>}
            <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                <button type="button" style={btn} onClick={onCancel} disabled={busy}>Cancel</button>
                <button type="button" onClick={() => void save()} disabled={!valid || busy}
                        style={{ ...btn, color: '#fff', background: valid && !busy ? '#0d9488' : 'rgba(13,148,136,0.4)',
                                 border: '1px solid transparent' }}>
                    {saveLabel}
                </button>
            </div>
        </div>
    );
};

export default SkillEditor;
