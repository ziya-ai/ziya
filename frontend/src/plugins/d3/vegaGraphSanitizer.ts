/**
 * Vega spec sanitizers for degenerate graph / geometry input (Issue 34).
 *
 * The raw-Vega runtime throws UNCAUGHT synchronous errors — collapsing the
 * whole render to a blank canvas — on two classes of malformed data that a
 * spec can legitimately contain:
 *
 *  1. A `force` transform whose `link` force references links with an endpoint
 *     (source/target) that does not resolve to a node in the node set. d3-force's
 *     `forceLink.initialize` builds a node map keyed by the node `id` accessor
 *     (default = the node's `index`, i.e. its position 0..n-1) and THROWS
 *     `Error: node not found: <id>` for any link endpoint absent from that map.
 *     A single dangling link (e.g. `{source:1,target:99}` or `{source:-1,...}`)
 *     therefore kills the entire force simulation → no node ever draws.
 *
 *  2. A `geoshape` transform fed a GeoJSON feature whose `geometry` is null or
 *     whose `geometry.coordinates` is null. d3-geo's path generator throws
 *     `TypeError: Cannot read properties of null (reading 'length')` per bad
 *     feature; good features still draw but the console fills with throws and
 *     any downstream consumer of the full feature set breaks.
 *
 * This is the SAME dangling-reference class already fixed for the network
 * renderer (Issue 11 `sanitizeNetworkGraph`) and d3-force (Issue 3): drop the
 * references the underlying library cannot resolve, BEFORE handing the spec to
 * the runtime, instead of letting one bad datum erase the whole chart.
 *
 * Both helpers are PURE + exported so they can be unit-tested without a DOM,
 * and both are conservative: they touch ONLY the datasets actually consumed by
 * a force/geoshape transform, and they NEVER empty a dataset that had valid
 * rows (a widened predicate must still reject only the genuinely-broken rows).
 */

/**
 * Resolve the inline `values` array backing a named dataset, following any
 * chain of `source` references (Vega allows `{name, source:'other'}`).
 * Returns the array of row objects, or null if it cannot be resolved to an
 * inline array (e.g. the data is loaded from a URL).
 */
function resolveDatasetValues(
  spec: any,
  name: string,
  seen: Set<string> = new Set(),
): any[] | null {
  if (!name || seen.has(name)) return null;
  seen.add(name);
  const datasets: any[] = Array.isArray(spec?.data) ? spec.data : [];
  const ds = datasets.find((d) => d && d.name === name);
  if (!ds) return null;
  if (Array.isArray(ds.values)) return ds.values;
  if (typeof ds.source === 'string') return resolveDatasetValues(spec, ds.source, seen);
  // `source` can also be an array of dataset names — resolve the first inline one.
  if (Array.isArray(ds.source)) {
    for (const s of ds.source) {
      if (typeof s === 'string') {
        const v = resolveDatasetValues(spec, s, seen);
        if (v) return v;
      }
    }
  }
  return null;
}

/**
 * True iff `mark` (or a group mark's nested `data`) declares a `force`
 * transform. Used to walk the mark tree.
 */
function forEachForceTransform(
  spec: any,
  visit: (transform: any, dataDef: any) => void,
): void {
  const walkMarks = (marks: any[]): void => {
    if (!Array.isArray(marks)) return;
    for (const mark of marks) {
      if (!mark || typeof mark !== 'object') continue;
      // A group mark can define its own `data` tables, each with transforms.
      const dataDefs: any[] = Array.isArray(mark.data) ? mark.data : [];
      for (const dataDef of dataDefs) {
        const transforms: any[] = Array.isArray(dataDef?.transform) ? dataDef.transform : [];
        for (const t of transforms) {
          if (t && t.type === 'force') visit(t, dataDef);
        }
      }
      if (Array.isArray(mark.marks)) walkMarks(mark.marks);
    }
  };
  walkMarks(Array.isArray(spec?.marks) ? spec.marks : []);
}

/**
 * Drop force-`link` links whose source/target endpoint cannot be resolved to a
 * node in the node set, mutating the referenced links dataset's `values` array
 * in place. Returns the number of links dropped (for logging/testing).
 *
 * Node id resolution mirrors d3-force / Vega:
 *   - If the `link` force declares an `id` field, valid endpoints are the SET
 *     of that field's values across the node rows.
 *   - Otherwise endpoints are treated as INDICES into the node table; valid
 *     endpoints are the integers 0..nodeCount-1.
 * An endpoint that is neither a resolvable id nor a valid index → link dropped.
 */
export function sanitizeVegaForceLinks(spec: any): number {
  if (!spec || typeof spec !== 'object') return 0;
  let dropped = 0;

  forEachForceTransform(spec, (transform, dataDef) => {
    const forces: any[] = Array.isArray(transform.forces) ? transform.forces : [];
    // Resolve the node set for THIS transform: the data table it runs on.
    // A group's data def has either its own `values` or a `source` chain.
    let nodeValues: any[] | null = null;
    if (Array.isArray(dataDef?.values)) {
      nodeValues = dataDef.values;
    } else if (typeof dataDef?.source === 'string') {
      nodeValues = resolveDatasetValues(spec, dataDef.source);
    } else if (typeof dataDef?.name === 'string') {
      nodeValues = resolveDatasetValues(spec, dataDef.name);
    }
    if (!Array.isArray(nodeValues)) return; // can't resolve nodes → leave alone
    const nodeCount = nodeValues.length;

    for (const f of forces) {
      if (!f || f.force !== 'link') continue;
      const linksName = f.links;
      if (typeof linksName !== 'string') continue;
      const linkDataset = (Array.isArray(spec.data) ? spec.data : []).find(
        (d: any) => d && d.name === linksName,
      );
      if (!linkDataset || !Array.isArray(linkDataset.values)) continue;

      // Build the valid-endpoint predicate.
      const idField: string | undefined =
        typeof f.id === 'string' ? f.id : undefined;
      let validId: ((v: any) => boolean) | null = null;
      if (idField) {
        const ids = new Set(nodeValues.map((n) => n?.[idField]));
        validId = (v: any) => ids.has(v);
      }
      const isValidIndex = (v: any): boolean =>
        typeof v === 'number' && Number.isInteger(v) && v >= 0 && v < nodeCount;

      const endpointOk = (v: any): boolean => {
        // Force `source`/`target` may already be objects (rare in inline
        // specs) — those are considered resolved and left alone.
        if (v && typeof v === 'object') return true;
        if (validId) return validId(v) || isValidIndex(v);
        return isValidIndex(v);
      };

      const before = linkDataset.values.length;
      linkDataset.values = linkDataset.values.filter(
        (lk: any) => lk && endpointOk(lk.source) && endpointOk(lk.target),
      );
      dropped += before - linkDataset.values.length;
    }
  });

  return dropped;
}

/**
 * True iff a GeoJSON geometry object is renderable by d3-geo's path generator,
 * i.e. it has a non-null `coordinates` (for the coordinate-bearing types) or is
 * a GeometryCollection with non-null `geometries`. A null geometry or null
 * `coordinates` throws inside d3-geo, so those features must be dropped.
 */
export function isRenderableGeometry(geom: any): boolean {
  if (!geom || typeof geom !== 'object') return false;
  const type = geom.type;
  if (type === 'GeometryCollection') {
    return Array.isArray(geom.geometries);
  }
  // Point, MultiPoint, LineString, MultiLineString, Polygon, MultiPolygon.
  return geom.coordinates != null;
}

/**
 * Drop GeoJSON features with null/absent geometry or null coordinates from any
 * dataset consumed by a `geoshape` transform, mutating that dataset's `values`
 * in place. Returns the number of features dropped.
 *
 * Handles both top-level Feature rows (`{type:'Feature', geometry:{...}}`) and
 * bare-geometry rows (`{type:'Polygon', coordinates:[...]}`) that a geoshape
 * mark can be bound to. A row with no geometry-like shape at all is left
 * untouched (it isn't the crash class and may be consumed by another mark).
 */
export function sanitizeVegaGeoshapeData(spec: any): number {
  if (!spec || typeof spec !== 'object') return 0;
  const datasetNames = new Set<string>();

  const collect = (marks: any[]): void => {
    if (!Array.isArray(marks)) return;
    for (const mark of marks) {
      if (!mark || typeof mark !== 'object') continue;
      const transforms: any[] = Array.isArray(mark.transform) ? mark.transform : [];
      const hasGeoshape = transforms.some((t) => t && t.type === 'geoshape');
      if (hasGeoshape && mark.from && typeof mark.from.data === 'string') {
        datasetNames.add(mark.from.data);
      }
      if (Array.isArray(mark.marks)) collect(mark.marks);
    }
  };
  collect(Array.isArray(spec.marks) ? spec.marks : []);
  if (datasetNames.size === 0) return 0;

  let dropped = 0;
  const datasets: any[] = Array.isArray(spec.data) ? spec.data : [];
  for (const name of datasetNames) {
    const ds = datasets.find((d) => d && d.name === name);
    if (!ds || !Array.isArray(ds.values)) continue;
    const before = ds.values.length;
    ds.values = ds.values.filter((row: any) => {
      if (!row || typeof row !== 'object') return true; // not a feature — leave it
      // Feature wrapper?
      if (row.type === 'Feature' || 'geometry' in row) {
        return isRenderableGeometry(row.geometry);
      }
      // Bare geometry row?
      if (typeof row.type === 'string' && ('coordinates' in row || 'geometries' in row)) {
        return isRenderableGeometry(row);
      }
      return true; // unknown shape — not the crash class
    });
    dropped += before - ds.values.length;
  }
  return dropped;
}

/**
 * (D-510) Planar signed area of a linear ring in lon/lat, using the shoelace
 * sum Σ(x₂−x₁)(y₂+y₁). In lon/lat (latitude increasing north = "up") a POSITIVE
 * sum is a CLOCKWISE ring and a NEGATIVE sum is counter-clockwise.
 */
function ringSignedArea(ring: any): number {
  if (!Array.isArray(ring) || ring.length < 4) return 0;
  let s = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const p1 = ring[i], p2 = ring[i + 1];
    if (!Array.isArray(p1) || !Array.isArray(p2)) return 0;
    s += (p2[0] - p1[0]) * (p2[1] + p1[1]);
  }
  return s;
}

/**
 * (D-510) Rewind ONE GeoJSON geometry's rings to d3-geo's winding convention:
 * an exterior ring must be CLOCKWISE (planar area > 0) and holes
 * counter-clockwise. d3-geo (used by Vega's `geoshape`) does NOT follow the
 * RFC 7946 counter-clockwise-exterior rule; fed a CCW exterior ring it treats
 * the polygon as the COMPLEMENT and fills (almost) the whole sphere — a solid
 * flood over the panel that buries every other feature (vega-w1-11). Reversing
 * a mis-wound ring is geometrically identity for the shape it describes, so it
 * only ever CORRECTS a flood; a correctly-wound polygon is left untouched.
 */
function rewindGeometry(geom: any): void {
  if (!geom || typeof geom !== 'object') return;
  if (geom.type === 'Polygon' && Array.isArray(geom.coordinates)) {
    geom.coordinates.forEach((ring: any, idx: number) => {
      if (!Array.isArray(ring)) return;
      const area = ringSignedArea(ring);
      // exterior (idx 0) wants CW (area > 0); holes want CCW (area < 0).
      const wantClockwise = idx === 0;
      if (wantClockwise ? area < 0 : area > 0) ring.reverse();
    });
  } else if (geom.type === 'MultiPolygon' && Array.isArray(geom.coordinates)) {
    for (const poly of geom.coordinates) {
      if (!Array.isArray(poly)) continue;
      poly.forEach((ring: any, idx: number) => {
        if (!Array.isArray(ring)) return;
        const area = ringSignedArea(ring);
        const wantClockwise = idx === 0;
        if (wantClockwise ? area < 0 : area > 0) ring.reverse();
      });
    }
  } else if (geom.type === 'GeometryCollection' && Array.isArray(geom.geometries)) {
    geom.geometries.forEach(rewindGeometry);
  }
}

/**
 * (D-510) Rewind every polygon in a dataset consumed by a `geoshape` transform
 * so d3-geo does not read a mis-wound exterior ring as the whole-sphere
 * complement. Handles the three shapes a Vega geo dataset takes: a bare
 * `values` array of Feature / geometry rows, a single `FeatureCollection`
 * object, and a single Feature / geometry object. Returns the number of rings
 * reversed. No-op for a spec with no geoshape-bound polygon data.
 */
export function rewindVegaGeoshapePolygons(spec: any): void {
  if (!spec || typeof spec !== 'object') return;
  const datasetNames = new Set<string>();
  const collect = (marks: any[]): void => {
    if (!Array.isArray(marks)) return;
    for (const mark of marks) {
      if (!mark || typeof mark !== 'object') continue;
      const transforms: any[] = Array.isArray(mark.transform) ? mark.transform : [];
      if (transforms.some((t) => t && t.type === 'geoshape') && mark.from && typeof mark.from.data === 'string') {
        datasetNames.add(mark.from.data);
      }
      if (Array.isArray(mark.marks)) collect(mark.marks);
    }
  };
  collect(Array.isArray(spec.marks) ? spec.marks : []);
  if (datasetNames.size === 0) return;

  const rewindRow = (row: any): void => {
    if (!row || typeof row !== 'object') return;
    if (row.type === 'FeatureCollection' && Array.isArray(row.features)) {
      row.features.forEach((f: any) => rewindGeometry(f && f.geometry));
    } else if (row.type === 'Feature') {
      rewindGeometry(row.geometry);
    } else if (typeof row.type === 'string') {
      rewindGeometry(row);
    }
  };

  const datasets: any[] = Array.isArray(spec.data) ? spec.data : [];
  for (const name of datasetNames) {
    const ds = datasets.find((d) => d && d.name === name);
    if (!ds) continue;
    if (Array.isArray(ds.values)) ds.values.forEach(rewindRow);
    else if (ds.values && typeof ds.values === 'object') rewindRow(ds.values);
  }
}

/**
 * Apply every Vega graph/geometry sanitizer to a spec IN PLACE and return the
 * spec. Safe to call on any spec: it no-ops when the spec has no force/geoshape
 * transforms.
 */
export function sanitizeVegaSpec(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  sanitizeVegaForceLinks(spec);
  sanitizeVegaGeoshapeData(spec);
  rewindVegaGeoshapePolygons(spec);
  sanitizeVegaFacetGroupTitles(spec);
  return spec;
}

/**
 * (D-511) In a native Vega group mark that is faceted (`from.facet`), a `title`
 * whose text expression reads the facet key via `datum.<field>` renders the
 * literal "undefined" (e.g. 'pundefined'). Vega evaluates a mark title's text
 * in the TITLE's own single-row data scope, where `datum` is the title datum —
 * NOT the enclosing group's facet datum. The facet datum is reachable from a
 * title expression only through the `parent` reference. Confusingly, the same
 * group's `encode` blocks DO see the facet datum as `datum`, so specs commonly
 * (and reasonably) write `datum.col` in `encode.update` and `datum.panel` in
 * the title — only the latter is wrong, and it silently yields the undefined
 * label rather than an error.
 *
 * This rewrites `datum.<f>` / `datum['<f>']` / `datum["<f>"]` to the matching
 * `parent` form INSIDE such a group's title text signal(s), for each `<f>`
 * named in the facet `groupby`. Scoped to (a) faceted groups, (b) their title
 * text signals only (never `encode`, where `datum` is correct), and (c) fields
 * that are actually facet keys — so a title referencing a genuine title-scope
 * field, or any non-faceted title, is left untouched. Returns the number of
 * title signals rewritten.
 */
export function sanitizeVegaFacetGroupTitles(spec: any): number {
  if (!spec || typeof spec !== 'object') return 0;
  let count = 0;

  const groupbyFields = (facet: any): string[] => {
    const gb = facet?.groupby;
    if (!gb) return [];
    const arr = Array.isArray(gb) ? gb : [gb];
    const out: string[] = [];
    for (const g of arr) {
      if (typeof g === 'string') out.push(g);
      else if (g && typeof g === 'object' && typeof g.field === 'string') out.push(g.field);
    }
    return out;
  };

  const rewriteExpr = (expr: string, fields: string[]): string => {
    let next = expr;
    for (const f of fields) {
      const esc = f.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      // datum.field  -> parent.field  (dot access, whole-word field name)
      next = next.replace(new RegExp(`\\bdatum\\.${esc}\\b`, 'g'), `parent.${f}`);
      // datum['field'] / datum["field"] -> parent['field']
      next = next.replace(new RegExp(`\\bdatum\\[(['"])${esc}\\1\\]`, 'g'), `parent['${f}']`);
    }
    return next;
  };

  // Rewrite every `signal` string that produces title TEXT: the shorthand
  // `title.text.signal`, and any `title.encode.<set>.text.signal`. Both are
  // evaluated in the title's data scope, so both mis-see the facet key as
  // `datum`. Non-text title signals (e.g. an offset) are deliberately left
  // alone to keep the rewrite narrow.
  const rewriteTitleTextSignals = (title: any, fields: string[]): void => {
    if (!title || typeof title !== 'object') return;
    const t = title.text;
    if (t && typeof t === 'object' && typeof t.signal === 'string') {
      const r = rewriteExpr(t.signal, fields);
      if (r !== t.signal) { t.signal = r; count++; }
    }
    const enc = title.encode;
    if (enc && typeof enc === 'object') {
      for (const setName of Object.keys(enc)) {
        const txt = enc[setName]?.text;
        if (txt && typeof txt === 'object' && typeof txt.signal === 'string') {
          const r = rewriteExpr(txt.signal, fields);
          if (r !== txt.signal) { txt.signal = r; count++; }
        }
      }
    }
  };

  const walk = (marks: any[]): void => {
    if (!Array.isArray(marks)) return;
    for (const mark of marks) {
      if (!mark || typeof mark !== 'object') continue;
      const fields = groupbyFields(mark?.from?.facet);
      if (fields.length && mark.title && typeof mark.title === 'object') {
        rewriteTitleTextSignals(mark.title, fields);
      }
      if (Array.isArray(mark.marks)) walk(mark.marks);
    }
  };

  walk(Array.isArray(spec.marks) ? spec.marks : []);
  return count;
}
