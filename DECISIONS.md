# Decisions

Why this system is built the way it is. The regulation is the definition of
correctness, so most of these are domain-judgment calls, not coding-style choices.

## Area: one authority, cross-checked, with the wrong answers shown on purpose

`ST_Area(geom::geography)` (geodesic on the WGS84 spheroid) is the single area
authority. It matches the basis Whisp uses (a GEE `.area()` geodesic measure).
In-process we compute the same quantity with `pyproj.Geod(ellps="WGS84")`; the two
agree to ~1e-10 relative, and an integration test asserts the in-process geodesic
equals the PL/pgSQL `fn_area_hectares` geography figure.

EPSG:6933 (EASE-Grid 2.0 Global, equal area) is the cross-check; we report the
*measured* delta, never assert the two are bit-identical. EPSG:3857 (Web Mercator)
and planar 4326 are computed only to demonstrate why they are wrong:

- Planar 4326 (degrees as metres) yields square degrees — non-physical at every
  latitude, and *maximally* wrong at the equator, not minimally.
- Web Mercator inflates area by roughly `sec²(lat)`. The measured inflation at the
  AOI's real latitude (12.67 N) is **+5.7%**, which *exceeds* the textbook spherical
  `sec²(lat)` figure (+5.05%) because EPSG:3857 is a spherical projection applied to
  ellipsoidal coordinates. This AOI is mid-latitude tropics, not "near the equator",
  so the error is real and silently plausible — exactly the trap to avoid.

The 4 ha threshold is the **geolocation submission-format boundary** (EUDR
Art. 9(1)(d)): plots < 4 ha may submit a single point, plots ≥ 4 ha must submit a
polygon. It is not a compliance pass/fail. A measured area within a tolerance band
of 4 ha is flagged `borderline` and escalated to `NEEDS_REVIEW`, because the
geography-vs-6933 disagreement could flip whether a point-only submission is even a
valid format.

## What we deliberately do NOT auto-fix

The judgment about what *not* to repair is the point of the validation stage. A
repair is `AUTO_FIXED` only when `ST_MakeValid(geom, 'method=structure')` leaves the
geodesic area unchanged within an epsilon **and** does not fragment a polygon into a
multipolygon. Otherwise it escalates:

- A self-intersecting figure-8 ("bowtie") has signed area ≈ 0 and `ST_MakeValid`
  fragments it into a two-part multipolygon. There is no well-defined original area
  to preserve, so it is `NEEDS_REVIEW`, never silently "repaired". (A zero-area spur
  that `ST_MakeValid` removes area-stably *is* `AUTO_FIXED` — the counterexample.)
- A `[lat, lon]`-swapped coordinate is `NEEDS_REVIEW`, never auto-swapped. Blindly
  transposing coordinates is how you wrongly relocate a farm.
- An unknown or mixed CRS is `NEEDS_REVIEW`, never blind-reprojected.
- An interior ring (doughnut) is valid generic GeoJSON, but the EUDR GeoJson File
  Description v1.5 *rejects* interior rings. It is flagged with the
  split-into-two-polygons workaround rather than processed.

## Legality and TRACES are stubbed honestly, not faked

EUDR Art. 3 is conjunctive: production must be both deforestation-free **and** legal.
Legality (Art. 2's eight documentary categories) is not derivable from public
geospatial rasters, so `LegalityAssessment` has exactly one reachable state,
`NOT_ASSESSED`. Because the test is conjunctive, the system therefore **never emits a
fully-compliant DDS** — every DDS carries `compliance_complete: false` and a loud
`legality_status: NOT_ASSESSED`. Faking a legality finding would be worse than
admitting the boundary of what the data can prove.

Real TRACES submission is a stub. The EU does run a TRACES NT acceptance environment
(`EUDRSubmissionServiceV2`, operator-credentialed, submissions carry no legal value,
specs still draft). We stub it deliberately — acceptance access needs operator
registration we do not have, and binding to a draft spec now is premature — **not**
because it does not exist. The reference-number / verification-number chain is modeled
as internal consistency only: the verification number authenticates a *specific*
reference (HMAC), so a verification for one reference does not validate another.

## Concurrency

The API is async FastAPI, but the CPU-bound work (geometry validation, raster zonal
statistics) is synchronous. `async` overlaps I/O; it does not speed up CPU-bound
geometry, so wrapping `exactextract` in `async def` would be theatre. If this needed
to scale, the geometry/raster work would move to a process pool, not an event loop.

## Things deliberately not built

These are roadmap, not half-built claims:

- **PostGIS-raster zonal statistics.** `exactextract` already answers the
  fractional-coverage question with correct partial-pixel weighting; `raster2pgsql`
  would be a multi-evening yak-shave for no additional correctness.
- **`rasterstats`.** Not a dependency: it offers only centroid-in-polygon or binary
  `all_touched`, with no partial-pixel weighting — `all_touched` over-counts exactly
  the edge pixels the tiny-plot tripwire warns about.
- **A GraphQL / Hasura gateway.** This read API would sit behind such a gateway in a
  larger system; it is not built here.
- **Any LLM / AI feature.** This is a pure geospatial backend.
- **The FDaP commodity-presence model** as a fourth convergence axis. The engine is
  built to accept it (it is the proper commodity layer, where WorldCover is only
  land-cover context), but it is left as an optional axis behind the
  `DeforestationProvider` interface.
- **A live hosted demo URL.** `docker compose up` + a `curl` sequence is the primary,
  reproducible delivery.

## One real bug

The validation, area, risk, and API unit tests all passed, and the in-process
end-to-end run produced a correct withheld DDS. Then the first **cross-process** smoke
— run the CLI to persist a consignment, start the API in a separate process, and
`GET /consignments/{id}/dds` — failed two ways at once (fixed in `fcd51cb`):

1. The pipeline records location-untrustworthy plots (a `[lat,lon]` swap that lands
   outside the raster coverage) with their validation report but an empty `{}` for
   area/risk. The API rebuilt each plot's `RiskProfile` with
   `RiskProfile.model_validate(stored_risk)`, which raises on `{}`. The API unit tests
   never caught it because they seed fully-assessed rows.
2. The pipeline excludes non-conformant geometries (the doughnut) from the DDS before
   calling `build_dds`; the API's regeneration path did not mirror that filter, so the
   interior-ring plot reached `build_dds`, which correctly rejects it — surfacing as a
   500 instead of a clean exclusion.

The fix makes the API tolerate the `{}` sentinel (skip in DDS, `assessed: false` in
`/plots`) and mirror the pipeline's v1.5-eligibility filter, with regression tests for
both. The lesson is the usual one: a transactional in-process test and a real
two-process round-trip are different tests, and the gap between them is where the
interesting bugs live.

A second genuine finding from the day-0 spike: `postgis/postgis:18-3.6` publishes no
`linux/arm64` manifest, so on Apple Silicon it must run under `linux/amd64` emulation.
The compose file and CI pin `platform: linux/amd64` for that reason — which is also
the production target (Fargate, managed Postgres).

## No fieldwork

This project was built from public datasets and documentation only. The author has
done no ground-truthing of any coordinate and has never seen any organization's real
EUDR system. The synthetic pathology geometries and the synthetic AOI rasters are
labelled as such throughout; see `tests/fixtures/README.md`.
