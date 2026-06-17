# Evidence

Only reproducible numbers appear here. Every figure below was produced by a command
in this repo against the pinned stack; nothing is hand-typed from memory or invented.

## Resolved stack (build-time truth)

Measured on the build host (macOS arm64; the PostGIS image runs under `linux/amd64`
emulation locally and natively on amd64 CI runners / production):

| Component | Version |
|---|---|
| Python | 3.12.13 |
| rasterio | 1.5.0 (bundled GDAL 3.12.1) |
| shapely | 2.1.2 (GEOS 3.13.1) |
| pyproj | 3.7.2 (PROJ 9.5.1) |
| geopandas | 1.1.3 |
| exactextract | 0.3.0 |
| PostGIS image | `postgis/postgis:18-3.6@sha256:0d513af346b21c76bf084e90c0589043c30ff379025d079ce4befa9c3a539f72` |
| PostGIS / GEOS / PROJ (in image) | 3.6.3 / 3.13.1 / 9.6.0 (PG 18) |

The migration asserts the floor `PostGIS_Lib_Version() ≥ 3.2` **and**
`PostGIS_GEOS_Version() ≥ 3.10` (both required for `ST_MakeValid(..., 'method=structure')`).
The Dockerfile and CI print the resolved geospatial versions at build time via Python
(`rasterio.__gdal_version__` etc.) — there is no `gdalinfo` CLI in a wheel-only install.

## Area authority and error bounds

A 0.001° square with its SW corner at (108.0, 12.67), measured against the pinned
PostGIS `fn_area_hectares`:

| Basis | Area | Note |
|---|---|---|
| `ST_Area(geography)` (geodesic WGS84) | 1.2017056342512369 ha | **authoritative** |
| EPSG:6933 (EASE-Grid 2.0, equal area) | 1.2017056342073444 ha | cross-check, Δ = 3.65e-11 |
| EPSG:3857 (Web Mercator) | 1.2701337 ha | **+5.69% — wrong** |
| planar EPSG:4326 (deg² as m²) | non-physical | wrong at every latitude |

The in-process `pyproj` geodesic reproduces the PostGIS geography figure to ~1e-10
relative (asserted in `tests/test_area_bounds.py::test_python_geodesic_matches_postgis_geography`).
The measured Web Mercator inflation (+5.69%) exceeds the spherical `sec²(lat)` figure
(+5.05% at 12.67 N) because EPSG:3857 is spherical over ellipsoidal coordinates.

## PostGIS GiST vs sequential scan (`EXPLAIN (ANALYZE, BUFFERS)`)

Query — the genuine inter-plot overlap self-join a consignment-validation step runs:

```sql
SELECT a.id, b.id FROM bench_plots a JOIN bench_plots b
ON ST_Intersects(a.geom, b.geom) AND a.id < b.id
```

Reproduce: `VERITAS_DATABASE_URL=... .venv/bin/python scripts/benchmark_overlap.py`.

| N (plots) | Heap pages | Plan | Execution time | Result |
|---|---|---|---|---|
| 6,000 | 144 | forced sequential nested loop (O(N²), 36M pair evals) | **31,761 ms** | 133 overlaps |
| 6,000 | 144 | GiST index scan | **309 ms** | 133 overlaps |
| 150,000 | 2,896 | GiST index scan (parallel) | **9,844 ms** | 94,035 overlaps |
| 150,000 | 2,896 | sequential (planner cost only) | not executed | cost 1.66e11 vs GiST 1.67e7 |

Measured speedup at the shared N = 6,000: **~103×** (31.8 s → 0.31 s), both finding the
same 133 pairs. At N = 150,000 the GiST path completes in ~9.8 s; the O(N²) sequential
path is not executed (its planner cost is ~10⁴× the GiST cost). The sequential baseline
N is bounded so the fixed-N comparison is real measured wall-clock, not extrapolation.
Host, predicate, page counts, PostGIS/GEOS version, and JIT timing are all in the raw
output the script prints.

## Whisp self-grading — methodology and an honesty boundary

The convergence tiering maps one-to-one to Whisp's `Risk_PCrop` tri-state
(Low / High / More info needed), and `src/veritas_eudr/whisp/` implements the diff:
a `WhispClient` (lazy, opt-in, never called on the demo path) and a `confusion_matrix`
that produces a 3×3 matrix + agreement rate + the list of disagreements.

**A real agreement number is not available in this build, and is not faked.** With the
committed *synthetic* AOI rasters, the engine reads painted data, not real satellite
observations, so comparing it against a real Whisp run would not be a meaningful
agreement metric. Running the tooling against the committed illustrative fixtures
(`tests/fixtures/whisp/cached_pcrop.json`, labelled as a placeholder) yields 9/9
agreement — but that is **circular by construction** (the fixtures were hand-set to
each painted zone's expected tier), so it only demonstrates that the matrix machinery
runs and the tier↔`Risk_PCrop` mapping is consistent. It is **not** evidence of
real-world agreement.

To obtain a genuine confusion matrix: fetch the real Hansen/JRC/WorldCover tiles and
Sample Earth points via `scripts/fetch_data.sh`, re-run the engine, and refresh the
Whisp side with `WhispClient` (the `--refresh` path). The expected structural
limitation to investigate first is that this engine runs ~3 layers where Whisp runs
~200; see `docs/investigations/whisp_disagreement_walkthrough.md`.

## End-to-end, verified live (CLI → DB → API)

`veritas-eudr run tests/fixtures/submissions/messy_submission.geojson --operator "Acme
Coffee Co" --consignment DEMO` against a live PostGIS persists 8 plots and returns a
withheld DDS. The API then serves:

- `GET /consignments/DEMO/dds` → `compliance_complete=false`, `legality_status=NOT_ASSESSED`,
  `deforestation_determination=more-info-needed`, `due_diligence_path=simplified_dd`,
  `country_risk_class=low`, `geojson_spec_version=1.5`, `valid_until = submission + 365 d`,
  reference + verification paired; 6 of 8 plots are DDS members (the doughnut and the
  out-of-AOI `[lat,lon]`-swap plot are correctly excluded).
- `GET /plots/{id}/risk` → the rolled-up `disposition` (AUTO_VALID / AUTO_FIXED /
  NEEDS_REVIEW), `assessed` flag, and risk tier.
- `GET /runs/{run_id}/replay` → 21 evidence-ledger rows (3 layers × 7 assessed plots),
  each with its dataset version and verdict — the replayable trail.
- `GET /health` → live PostGIS version + resolved build versions;
  `GET /metrics` → Prometheus `veritas_eudr_build_info` + request counters.

The replay/mutation test (`tests/test_replay_mutation.py`) further proves a changed
Hansen dataset version flips a plot's verdict and that the evidence ledger attributes
the change to exactly that input.
