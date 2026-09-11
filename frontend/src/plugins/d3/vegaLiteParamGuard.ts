// Issue 32 — Vega-Lite dangling-param condition guard.
//
// A Vega-Lite encoding `condition` may reference a parameter/selection by
// name, e.g.:
//     "color": { "field": "cat", "type": "nominal",
//                "condition": { "param": "brush", "value": "red" } }
//
// If that param name is NOT declared in any `params` block anywhere in the
// spec, the Vega-Lite compiler enters a NON-TERMINATING (synchronous) loop
// while trying to resolve the selection's predicate — it never throws and
// never returns, so the whole render hangs. Because the loop is synchronous,
// the plugin's own `Promise.race`/`setTimeout` render-timeout can never fire
// (the event loop is blocked), and the outer harness only surfaces it as a
// 30s timeout with zero DOM output.
//
// This module removes the whole CLASS of malformed input: any `condition`
// (object or array form) whose `param` references a name declared nowhere in
// the spec is dropped, leaving the un-conditional base of the encoding intact
// (field/type/value/scale/etc.). A condition whose `param` IS declared, or a
// `test`-expression condition, is preserved unchanged — so a well-formed
// interactive spec is byte-identical after this pass.
//
// Exported pure helpers so the logic is unit-testable without a DOM.

/** Recursively collect every parameter name declared in any `params` block. */
export const collectDeclaredParamNames = (node: any, acc?: Set<string>): Set<string> => {
  const names = acc ?? new Set<string>();
  if (!node || typeof node !== 'object') return names;
  if (Array.isArray(node)) {
    for (const item of node) collectDeclaredParamNames(item, names);
    return names;
  }
  if (Array.isArray(node.params)) {
    for (const p of node.params) {
      if (p && typeof p === 'object' && typeof p.name === 'string' && p.name.length > 0) {
        names.add(p.name);
      }
    }
  }
  for (const key in node) {
    if (Object.prototype.hasOwnProperty.call(node, key) && key !== 'params') {
      collectDeclaredParamNames(node[key], names);
    }
  }
  return names;
};

/** True when a single condition clause references a param not in `declared`. */
const isDanglingParamCondition = (cond: any, declared: Set<string>): boolean => {
  return !!cond && typeof cond === 'object' && !Array.isArray(cond) &&
    typeof cond.param === 'string' && !declared.has(cond.param);
};

/**
 * Walk the spec and remove any `condition` clause that references an
 * undeclared param. Mutates `node` in place. Returns the number of dropped
 * clauses (for diagnostics/tests).
 */
export const dropDanglingParamConditions = (node: any, declared: Set<string>): number => {
  let dropped = 0;
  if (!node || typeof node !== 'object') return dropped;
  if (Array.isArray(node)) {
    for (const item of node) dropped += dropDanglingParamConditions(item, declared);
    return dropped;
  }

  if (Object.prototype.hasOwnProperty.call(node, 'condition')) {
    const cond = node.condition;
    if (Array.isArray(cond)) {
      const kept = cond.filter((c) => {
        if (isDanglingParamCondition(c, declared)) {
          dropped += 1;
          return false;
        }
        return true;
      });
      if (kept.length === 0) {
        delete node.condition;
      } else if (kept.length !== cond.length) {
        node.condition = kept;
      }
    } else if (isDanglingParamCondition(cond, declared)) {
      delete node.condition;
      dropped += 1;
    }
  }

  for (const key in node) {
    if (Object.prototype.hasOwnProperty.call(node, key)) {
      dropped += dropDanglingParamConditions(node[key], declared);
    }
  }
  return dropped;
};

/**
 * Pure entry point: returns a NEW spec with every dangling-param condition
 * removed. The input is not mutated. A spec with no dangling conditions is
 * returned structurally equal (deep-cloned) to the input.
 */
export const sanitizeDanglingParamConditions = <T>(spec: T): { spec: T; dropped: number } => {
  if (!spec || typeof spec !== 'object') return { spec, dropped: 0 };
  let clone: any;
  try {
    clone = JSON.parse(JSON.stringify(spec));
  } catch {
    return { spec, dropped: 0 };
  }
  const declared = collectDeclaredParamNames(clone);
  const dropped = dropDanglingParamConditions(clone, declared);
  return { spec: clone as T, dropped };
};

// ── D-314 / G-14daa0: selection-param fan-out inside a faceted spec ─────────
//
// A point/interval SELECTION param (a `params` entry with a `select` key,
// e.g. `{name:"pick", select:{type:"point", fields:["c"]}, bind:"legend"}`)
// declared on a FACETED spec is instantiated once per facet cell by the
// Vega-Lite compiler. The per-cell selection signals plus their legend
// bindings compile to a dataflow whose size grows with the cell count, and
// the render exceeds the harness timeout in both themes with zero DOM output.
// The dangling-param guard above cannot help: the param IS declared, so it is
// left untouched.
//
// A headless screenshot cannot exercise hover/click anyway, so the correct,
// general repair is to REFUSE the per-cell fan-out: drop the selection
// param declarations from a faceted spec and resolve every encoding
// `condition` that referenced them to its matched (selected-state) branch —
// the appearance the author intended for a highlighted mark. The chart then
// renders as a static small-multiples view with full visual content and no
// runaway dataflow. Non-faceted specs are left completely untouched, so
// ordinary interactive charts keep their behaviour; variable params (sliders
// etc., which have no `select` key) are preserved even inside facets.

/** True when the spec uses a facet/repeat operator that fans sub-views out per cell. */
export const isFacetOperatorSpec = (spec: any): boolean => {
  if (!spec || typeof spec !== 'object') return false;
  if (spec.facet && spec.spec) return true;
  if (spec.repeat && spec.spec) return true;
  const enc = spec.encoding;
  if (enc && typeof enc === 'object' && (enc.row || enc.column)) return true;
  return false;
};

/** Collect the names of every SELECTION param (a `params` entry with `select`). */
export const collectSelectionParamNames = (node: any, acc?: Set<string>): Set<string> => {
  const names = acc ?? new Set<string>();
  if (!node || typeof node !== 'object') return names;
  if (Array.isArray(node)) {
    for (const item of node) collectSelectionParamNames(item, names);
    return names;
  }
  if (Array.isArray(node.params)) {
    for (const p of node.params) {
      if (p && typeof p === 'object' && typeof p.name === 'string' && p.name.length > 0 &&
          p.select !== undefined && p.select !== null) {
        names.add(p.name);
      }
    }
  }
  for (const key in node) {
    if (Object.prototype.hasOwnProperty.call(node, key) && key !== 'params') {
      collectSelectionParamNames(node[key], names);
    }
  }
  return names;
};

/** Remove the declarations of the named selection params from every `params` block. */
const removeSelectionParamDecls = (node: any, names: Set<string>): void => {
  if (!node || typeof node !== 'object') return;
  if (Array.isArray(node)) {
    for (const item of node) removeSelectionParamDecls(item, names);
    return;
  }
  if (Array.isArray(node.params)) {
    node.params = node.params.filter(
      (p: any) => !(p && typeof p === 'object' && typeof p.name === 'string' && names.has(p.name)),
    );
    if (node.params.length === 0) delete node.params;
  }
  for (const key in node) {
    if (Object.prototype.hasOwnProperty.call(node, key) && key !== 'params') {
      removeSelectionParamDecls(node[key], names);
    }
  }
};

/**
 * Resolve every encoding `condition` that references one of `names` to its
 * matched branch: `{condition:{param:"pick", value:1}, value:0.3}` becomes
 * `{value:1}` (the selected-state appearance), and the `param`/`empty` keys
 * are dropped. An ARRAY condition drops only the clauses that reference a
 * named param (like the dangling-param path), keeping the rest. Mutates in
 * place; returns the number of conditions resolved.
 */
const resolveSelectionConditions = (node: any, names: Set<string>): number => {
  let resolved = 0;
  if (!node || typeof node !== 'object') return resolved;
  if (Array.isArray(node)) {
    for (const item of node) resolved += resolveSelectionConditions(item, names);
    return resolved;
  }
  if (Object.prototype.hasOwnProperty.call(node, 'condition')) {
    const cond = node.condition;
    if (Array.isArray(cond)) {
      const kept = cond.filter((c: any) => {
        const refs = !!c && typeof c === 'object' && typeof c.param === 'string' && names.has(c.param);
        if (refs) resolved += 1;
        return !refs;
      });
      if (kept.length === 0) delete node.condition;
      else if (kept.length !== cond.length) node.condition = kept;
    } else if (cond && typeof cond === 'object' && typeof cond.param === 'string' && names.has(cond.param)) {
      const { param, empty, test, ...matched } = cond;
      delete node.condition;
      // Overlay the matched-branch props (e.g. value:1) onto the else-branch
      // props already on the node (e.g. value:0.3), so the resolved state wins.
      Object.assign(node, matched);
      resolved += 1;
    }
  }
  for (const key in node) {
    if (Object.prototype.hasOwnProperty.call(node, key) && key !== 'condition') {
      resolved += resolveSelectionConditions(node[key], names);
    }
  }
  return resolved;
};

/**
 * Neutralize selection params on a FACETED spec so they can no longer fan out
 * per facet cell. Non-faceted specs are returned unchanged. Mutates `spec` in
 * place (the render path clones beforehand). Returns diagnostics.
 */
export const neutralizeFacetedSelectionParams = (
  spec: any,
): { removedParams: number; resolvedConditions: number } => {
  if (!isFacetOperatorSpec(spec)) return { removedParams: 0, resolvedConditions: 0 };
  const selNames = collectSelectionParamNames(spec);
  if (selNames.size === 0) return { removedParams: 0, resolvedConditions: 0 };
  removeSelectionParamDecls(spec, selNames);
  const resolvedConditions = resolveSelectionConditions(spec, selNames);
  return { removedParams: selNames.size, resolvedConditions };
};
