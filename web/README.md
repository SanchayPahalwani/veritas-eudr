# Veritas EUDR — read-only plot risk map

A deliberately small, fully self-contained web map: the ~50 AOI plots coloured by
their convergence-of-evidence deforestation risk, over a self-hosted vector
basemap. Click a plot to see its validation disposition and evidence trail. There
is **no live third-party dependency at runtime** — MapLibre, the pmtiles
protocol, the basemap tiles, and the plot data are all vendored/committed under
this directory.

## View it

```
cd web
python -m http.server 8000
# open http://localhost:8000/
```

A static file server is enough; nothing here talks to the network at runtime.
(`file://` will not work — the browser blocks `fetch` of `plots.geojson` and the
range requests the PMTiles reader makes under that scheme.)

## What you see

- A MapLibre map centred on the AOI (~108.03 E, 12.66 N — Vietnam Central
  Highlands robusta belt).
- One circle per plot, coloured by the `risk` property:

  | colour | meaning            | hex       |
  | ------ | ------------------ | --------- |
  | green  | `low`              | `#2e7d32` |
  | red    | `high`             | `#c62828` |
  | amber  | `more-info-needed` | `#f9a825` |
  | grey   | `null` (not risk-assessed — an unsampleable location) | `#9e9e9e` |

- A blue highlight ring on plots flagged `boundary_uncertain` (the Hansen
  band-21 latency case — see below).
- Visible **© OpenStreetMap contributors** attribution (bottom-right), the
  provenance of the basemap.

Clicking any plot opens a popup with: `plot_id`, the risk tier, the rolled-up
validation **disposition** (`AUTO_VALID` / `AUTO_FIXED` / `NEEDS_REVIEW`), the
number of evidence-ledger records behind the verdict, and the human-readable
rationale.

The committed coffee-point fixture is all valid and sampleable, so the demo map
shows only `low` / `high` / `more-info-needed` plots — no grey "not assessed"
points and no `NEEDS_REVIEW`. The validation pathologies and not-assessed cases
are exercised by the `messy_submission` CLI/API demo, not by this map.

## The band-21 default story (tripwire B)

On load the map performs a **default click-through**: it flies to the first
`boundary_uncertain` plot (`pt-014`) and opens its popup automatically.

These plots have post-cutoff Hansen forest loss **only in band 21** — calendar
year 2021, the first annual band after the 31 Dec 2020 EUDR cutoff. Hansen
reports *year-of-first-detection* with latency, so a 2019–2020 clearing can
surface in 2021. Treating that as a hard HIGH would over-flag; treating it as
LOW would under-flag. The engine instead downgrades a would-be HIGH to
`more-info-needed` and marks the plot `boundary_uncertain`. The map surfaces that
nuance as the headline interaction.

## Files

```
web/
  index.html         the page (legend + map container; loads vendored JS/CSS)
  app.js             map setup, risk colouring, popups, band-21 default
  plots.geojson      the plot data (generated; see below)
  vendor/            pinned, vendored libraries (no runtime CDN)
    maplibre-gl.js   MapLibre GL JS 4.7.1
    maplibre-gl.css  MapLibre GL JS 4.7.1 stylesheet
    pmtiles.js       pmtiles protocol 3.2.1
  basemap/
    aoi.pmtiles      self-hosted vector basemap of the AOI (see provenance)
```

## Regenerating `plots.geojson`

`plots.geojson` is produced by a committed, deterministic exporter that runs the
**same pure pipeline** the demo uses (ingest → validate → area → deforestation),
with no database and no network:

```
.venv/bin/python scripts/export_web_plots.py
```

It reads `tests/fixtures/points/coffee_points.geojson`, processes every feature
through `process_features`, and writes one GeoJSON `FeatureCollection` whose
features carry exactly:

```
plot_id, risk, disposition, boundary_uncertain, rationale, n_evidence
```

The exporter is idempotent — re-running overwrites the file byte-for-byte (sorted
keys, fixed separators), so it is safe to commit and review in a diff.

## Basemap provenance

**Shipped: a real self-hosted PMTiles basemap** (`basemap/aoi.pmtiles`), not the
inline fallback.

It is a vector tileset (MVT, zoom 6–14) of the AOI built from an OpenStreetMap
extract — roads and water only, ~3,300 features, ~2 MB. At runtime the vendored
`pmtiles.js` protocol serves tiles directly out of this single file via HTTP
range requests; there is no tile server and no `tile.openstreetmap.org` call.
The single source-layer is `osm`, with a `layer` field (`road` / `water`) and a
`kind` field (the OSM `highway`/`waterway` value) that `app.js` styles.

### Build command (how `basemap/aoi.pmtiles` was produced)

The data is © OpenStreetMap contributors (ODbL). The build is a one-time,
build-time download — never a runtime dependency. Tooling: `tippecanoe` and the
`pmtiles` CLI (`brew install tippecanoe pmtiles`).

1. Fetch a small OSM extract for the AOI bounding box (lat 12.4–14.0,
   lon 107.5–108.6) via Overpass, and convert to GeoJSON. The committed file was
   built from this Overpass query (roads + rivers + waterbodies), each way tagged
   with a `layer` (`road`/`water`) and `kind` property:

   ```
   [out:json][timeout:120];
   (
     way["highway"~"motorway|trunk|primary|secondary|tertiary"](12.4,107.5,14.0,108.6);
     way["waterway"="river"](12.4,107.5,14.0,108.6);
     way["natural"="water"](12.4,107.5,14.0,108.6);
   );
   out geom;
   ```

   POST it to `https://overpass-api.de/api/interpreter` (send a `User-Agent`
   header — Overpass returns HTTP 406 without one), then flatten each `way`'s
   `geometry` into GeoJSON LineStrings/Polygons.

2. Build the PMTiles directly with tippecanoe:

   ```
   tippecanoe -o basemap/aoi.pmtiles -z14 -Z6 -l osm \
     --drop-densest-as-needed --extend-zooms-if-still-dropping \
     --force aoi.geojson
   ```

3. Inspect the result:

   ```
   pmtiles show basemap/aoi.pmtiles
   ```

### Fallback if the build tooling is unavailable

If `tippecanoe`/`pmtiles` cannot be installed or the OSM extract cannot be
fetched, the map should still render standalone with no network. The fallback is
to replace the PMTiles source in `app.js` with a minimal **inline GeoJSON** vector
background — the AOI bounding box plus a light graticule — so the page is fully
self-contained. The real PMTiles is preferred and is what ships here; the inline
fallback is the documented degradation path, not the default.
