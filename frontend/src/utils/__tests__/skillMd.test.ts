/**
 * parseSkillMd — the one SKILL.md reader shared by the Bench editor's import
 * and the sidebar tab (moved out of SkillsSection so the two cannot drift).
 */
import { parseSkillMd } from '../skillMd';

it('reads frontmatter name/description/keywords and the body', () => {
    const r = parseSkillMd('---\nname: "fleet"\ndescription: Query fleet\nkeywords: atlas, timestream\n---\n# Body\nText.\n');
    expect(r).toEqual({ name: 'fleet', description: 'Query fleet', prompt: '# Body\nText.', keywords: ['atlas', 'timestream'] });
});

it.each([
    'no frontmatter at all',
    '---\nname: x\n---\nbody',                 // description missing
    '---\ndescription: y\n---\nbody',          // name missing
])('rejects %p', (text) => {
    expect(parseSkillMd(text)).toBeNull();
});
