/**
 * Parse a SKILL.md (agentskills.io frontmatter + body) into the fields a
 * skill create needs.  One reader for the Bench editor's import and the
 * sidebar tab, so the two cannot drift.  null when the frontmatter is
 * missing or lacks name/description.
 */
export interface ParsedSkillMd { name: string; description: string; prompt: string; keywords?: string[]; }

export function parseSkillMd(text: string): ParsedSkillMd | null {
    const match = text.match(/^\s*---[ \t]*\n([\s\S]*?)\n---[ \t]*\n([\s\S]*)$/);
    if (!match) return null;
    const frontmatter = match[1];
    const body = match[2].trim();
    const get = (key: string): string => {
        const m = frontmatter.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'));
        return m ? m[1].trim().replace(/^["']|["']$/g, '') : '';
    };
    const name = get('name');
    const description = get('description');
    if (!name || !description) return null;
    const kwRaw = get('keywords');
    const keywords = kwRaw ? kwRaw.split(/[,\s]+/).filter(Boolean) : undefined;
    return { name, description, prompt: body, keywords };
}

export const SKILL_MD_INVALID = 'Invalid SKILL.md — needs YAML frontmatter with name and description';
