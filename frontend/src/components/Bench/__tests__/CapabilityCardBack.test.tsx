/**
 * The in-lane unfolder ("card back") was retired in favour of the detail
 * drawer (CapabilityDetail.test.tsx covers it).  This guard keeps the old
 * component from being resurrected: the file must stay an empty module and
 * no Bench source may import it.
 */
import * as fs from 'fs';
import * as path from 'path';

it('CapabilityCardBack stays retired: empty module, imported by nothing', () => {
    const benchDir = path.join(__dirname, '..');
    const stub = fs.readFileSync(path.join(benchDir, 'CapabilityCardBack.tsx'), 'utf8');
    expect(stub.replace(/\/\*[\s\S]*?\*\//g, '').trim()).toBe('export {};');
    const importers = fs.readdirSync(benchDir)
        .filter(f => /\.tsx?$/.test(f) && f !== 'CapabilityCardBack.tsx')
        .filter(f => /CapabilityCardBack/.test(fs.readFileSync(path.join(benchDir, f), 'utf8')));
    expect(importers).toEqual([]);
});
