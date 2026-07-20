/**
 * Tests for normalizeBlockTree — recursively fills missing scope arrays
 * across a whole Block tree.
 *
 * The crash class this guards: a task-card fenced block authored by a
 * model is parsed with a raw JSON.parse (never validated through the
 * backend pydantic model that fills TaskScope's tools/skills/paths
 * defaults).  A block carrying a partial scope like {"paths": [...]}
 * reached the editor and crashed it on scope.tools.length.  Normalizing
 * the tree at the parse boundary makes every scope well-formed before
 * any editor renders it.
 */

import { normalizeBlockTree, liftBlockModelFields } from '../taskCardBlocks';
import type { Block } from '../../types/task_card';

const task = (id: string, extra: Partial<Block> = {}): Block => ({
  block_type: 'task', id, name: '', body: [], ...extra,
});

describe('normalizeBlockTree', () => {
  it('fills missing arrays on a top-level partial scope', () => {
    const out = normalizeBlockTree(
      task('t1', { scope: { paths: [{ path: 'a.py' }] } as any }),
    );
    expect(out.scope!.paths).toEqual([{ path: 'a.py' }]);
    expect(out.scope!.tools).toEqual([]);
    expect(out.scope!.skills).toEqual([]);
  });

  it('normalizes a partial scope on a nested body block', () => {
    const root: Block = {
      block_type: 'group', id: 'g1', name: 'G',
      body: [task('t1', { scope: { paths: [{ path: 'a.py' }] } as any })],
    };
    const out = normalizeBlockTree(root);
    const child = out.body![0];
    expect(child.scope!.tools).toEqual([]);
    expect(child.scope!.skills).toEqual([]);
  });

  it('normalizes deeply nested partial scopes', () => {
    const root: Block = {
      block_type: 'repeat', id: 'r', name: '',
      body: [{
        block_type: 'parallel', id: 'p', name: '',
        body: [task('t2', { scope: { skills: ['sk'] } as any })],
      }],
    };
    const out = normalizeBlockTree(root);
    const leaf = out.body![0].body![0];
    expect(leaf.scope!.paths).toEqual([]);
    expect(leaf.scope!.tools).toEqual([]);
    expect(leaf.scope!.skills).toEqual(['sk']);
  });

  it('leaves a scope-less block scope-less (never fabricates grants)', () => {
    const out = normalizeBlockTree(task('t1'));
    expect(out.scope == null).toBe(true);
  });

  it('treats null scope as scope-less', () => {
    const out = normalizeBlockTree(task('t1', { scope: null }));
    expect(out.scope).toBeNull();
  });

  it('preserves populated fields and non-array extras', () => {
    const out = normalizeBlockTree(task('t1', {
      scope: {
        paths: [{ path: 'x' }], tools: ['grep'], skills: [],
        cwd: 'sub', shell_commands: ['pytest'], model_tier: 'small',
      } as any,
    }));
    expect(out.scope!.tools).toEqual(['grep']);
    expect(out.scope!.cwd).toBe('sub');
    expect(out.scope!.shell_commands).toEqual(['pytest']);
    expect((out.scope as any).model_tier).toBe('small');
  });

  it('does not mutate the input tree', () => {
    const leaf = task('t1', { scope: { paths: [] } as any });
    const root: Block = { block_type: 'group', id: 'g', name: '', body: [leaf] };
    const out = normalizeBlockTree(root);
    expect(out).not.toBe(root);
    expect(out.body![0]).not.toBe(leaf);
    // Original leaf's partial scope is untouched.
    expect('tools' in (leaf.scope as any)).toBe(false);
  });

  it('handles a block with no body', () => {
    const out = normalizeBlockTree(task('t1', { scope: {} as any }));
    expect(out.scope!.paths).toEqual([]);
  });

  it('lifts a misplaced block-level model_tier into scope', () => {
    const out = normalizeBlockTree(
      task('t1', { scope: { paths: [] }, model_tier: 'small' } as any),
    );
    expect((out.scope as any).model_tier).toBe('small');
    expect((out as any).model_tier).toBeUndefined();
  });

  it('lifts a block-level model field when the block has no scope', () => {
    const out = normalizeBlockTree(task('t1', { model_tier: 'large' } as any));
    expect((out.scope as any).model_tier).toBe('large');
    expect((out as any).model_tier).toBeUndefined();
  });

  it('lifts misplaced model fields recursively through the tree', () => {
    const root: Block = {
      block_type: 'group', id: 'g', name: '',
      body: [task('t1', { model_tier: 'medium' } as any)],
    };
    const out = normalizeBlockTree(root);
    expect((out.body![0].scope as any).model_tier).toBe('medium');
    expect((out.body![0] as any).model_tier).toBeUndefined();
  });
});

describe('liftBlockModelFields', () => {
  it('returns the same reference when nothing is misplaced', () => {
    const b = task('t1', { scope: { paths: [], tools: [], skills: [] } } as any);
    expect(liftBlockModelFields(b)).toBe(b);
  });

  it('moves all four model fields into scope and strips the top-level copies', () => {
    const out = liftBlockModelFields(task('t1', {
      model_tier: 'small', model_name: 'x',
      model_id_override: 'arn', model_endpoint: 'bedrock',
    } as any));
    const s = out.scope as any;
    expect(s.model_tier).toBe('small');
    expect(s.model_name).toBe('x');
    expect(s.model_id_override).toBe('arn');
    expect(s.model_endpoint).toBe('bedrock');
    expect((out as any).model_tier).toBeUndefined();
    expect((out as any).model_endpoint).toBeUndefined();
  });

  it('does not clobber a value already correctly placed in scope', () => {
    const out = liftBlockModelFields(task('t1', {
      scope: { model_tier: 'large' }, model_tier: 'small',
    } as any));
    // scope's own value wins; the stray top-level copy is dropped.
    expect((out.scope as any).model_tier).toBe('large');
    expect((out as any).model_tier).toBeUndefined();
  });

  it('creates scope when the block had none', () => {
    const out = liftBlockModelFields(task('t1', { model_tier: 'medium' } as any));
    expect(out.scope).toBeTruthy();
    expect((out.scope as any).model_tier).toBe('medium');
  });

  it('does not mutate the input block', () => {
    const b = task('t1', { model_tier: 'small' } as any);
    const out = liftBlockModelFields(b);
    expect(out).not.toBe(b);
    expect((b as any).model_tier).toBe('small'); // original untouched
  });
});
