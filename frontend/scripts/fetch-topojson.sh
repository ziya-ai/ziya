#!/usr/bin/env sh
# Fetch the outlines Plotly's geo traces (scattergeo, choropleth) draw their
# basemap from, so maps render without contacting cdn.plot.ly.
#
# Ziya's Content-Security-Policy only allows fetches to the Ziya server itself
# (connect-src 'self'), so Plotly's default topojsonURL (https://cdn.plot.ly/un/)
# is blocked and a geo figure hangs on "fetching topojson".  plotlyPlugin.ts
# points topojsonURL at /static/topojson/ instead; this script populates that
# directory.  The build copies frontend/public/ into the served bundle, so the
# files end up at app/templates/static/topojson/ and are served under /static/.
#
# Run once from frontend/ (needs network), then commit the files:
#   sh scripts/fetch-topojson.sh
#
# Only the 110m resolution is shipped (~600 KB total); the plugin clamps
# layout.geo.resolution to 110.  The "un/" set is the UN-boundary topojson
# Plotly 3 defaults to, matching the bundled plotly.js-dist-min.
set -eu
BASE="https://cdn.plot.ly/un/"
DEST="$(dirname "$0")/../public/static/topojson"
mkdir -p "$DEST"
for scope in world usa europe asia africa north-america south-america; do
  f="${scope}_110m.json"
  echo "fetching $f"
  curl -fsSL "${BASE}${f}" -o "$DEST/$f.tmp"
  node -e '
    const t = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    if (t.type !== "Topology") throw new Error(process.argv[1] + ": not a TopoJSON Topology");
  ' "$DEST/$f.tmp"
  mv "$DEST/$f.tmp" "$DEST/$f"
done
echo "done: $(ls "$DEST" | wc -l | tr -d ' ') files in $DEST"
