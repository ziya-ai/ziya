/**
 * Wiring for the sidebar job indicator.
 *
 * Same reasoning as runStatusIndexWiring.test.ts: the defects in this area
 * are joins, not logic.  The pure module and the component can both be
 * correct while the sidebar never passes a job to them, the poll hook
 * swallows a job finishing as "no change", or the sort memo keeps a stale
 * row.  These assert each hop the ``jobs`` field crosses.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');

const API = () => read('services/taskRunApi.ts');
const HOOK = () => read('hooks/useRunStatusIndex.ts');
const SIDEBAR = () => read('components/MUIChatHistory.tsx');

describe('api client', () => {
  it('types the jobs field of the status index', () => {
    expect(API()).toMatch(/jobs\?: Record<string, JobSummary\[\]>;/);
  });
});

describe('poll hook', () => {
  it('includes jobs in the identity signature, or a job finishing is dropped as "no change"', () => {
    const fn = HOOK().match(/function signature\(index: RunStatusIndex\): string \{[\s\S]*?\n\}/);
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/jobsSignature\(index\.jobs\)/);
  });
});

describe('sidebar', () => {
  it('passes each row its jobs and its last-visit mark from the index', () => {
    const s = SIDEBAR();
    expect(s).toMatch(/const rowJobs = convId \? runStatusIndex\.jobs\?\.\[convId\] : undefined;/);
    expect(s).toMatch(/jobs=\{rowJobs\} jobsSeenAt=\{rowJobsSeenAt\}/);
  });

  it('renders JobStatusGlyphs on the row, hiding finished jobs for the open conversation', () => {
    const s = SIDEBAR();
    expect(s).toMatch(/import JobStatusGlyphs from '\.\/TaskCard\/JobStatusGlyphs'/);
    expect(s).toMatch(/<JobStatusGlyphs jobs=\{jobs\} seenAt=\{jobsSeenAt\} hideFinished=\{isCurrentItem\} compact=\{compact\} \/>/);
  });

  it('marks a conversation seen when it is opened and when it is left', () => {
    const s = SIDEBAR();
    expect(s).toMatch(/markJobsSeen\(currentProject\.id, currentConversationId/);
    expect(s).toMatch(/markJobsSeen\(currentProject\.id, prev/);
  });

  it('invalidates the sort memo when a row\'s jobs or its seen mark change', () => {
    const s = SIDEBAR();
    expect(s).toMatch(/activeConvIds\.add\('job:' \+ id \+ ':' \+ jobsSignature\(jobs\) \+ ':' \+ \(jobsSeen\[id\] \?\? 0\)\)/);
    const deps = s.match(/\}, \[conversations, folders, pinnedFolders, isProjectSwitching[^\]]*\]\);/);
    expect(deps).not.toBeNull();
    expect(deps![0]).toMatch(/jobsSeen/);
  });
});
