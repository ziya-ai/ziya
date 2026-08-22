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
 * Apply every Vega graph/geometry sanitizer to a spec IN PLACE and return the
 * spec. Safe to call on any spec: it no-ops when the spec has no force/geoshape
 * transforms.
 */
export function sanitizeVegaSpec(spec: any): any {
  if (!spec || typeof spec !== 'object') return spec;
  sanitizeVegaForceLinks(spec);
  sanitizeVegaGeoshapeData(spec);
  return spec;
}
