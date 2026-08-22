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
