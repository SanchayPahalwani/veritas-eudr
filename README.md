# veritas-eudr

[![ci](https://github.com/SanchayPahalwani/veritas-eudr/actions/workflows/ci.yml/badge.svg)](https://github.com/SanchayPahalwani/veritas-eudr/actions/workflows/ci.yml)

**▶ Live demo:** https://web-demo-nine-nu.vercel.app/ — a static, pre-baked walkthrough of the full pipeline (no backend required).

Under the EU Deforestation Regulation, the regulation *is* the definition of
correctness. One wrong hectare calculation, or one blindly reprojected coordinate,
can wrongly block a smallholder coffee farmer from the EU market — or wrongly clear
real deforestation. `veritas-eudr` walks a real customer-style farm submission through
the stages an EUDR backend owns — **ingest → validate/repair → intersect public
deforestation rasters → risk → Due Diligence Statement** — and keeps a replayable
evidence trail behind every result.

> An independent open-source learning project. Not affiliated with or endorsed by
> Enveritas. Uses only public datasets. Built to understand the EUDR backend problem
> space — not a model of any organization's real system, which I have never seen.

It refuses to fake a proprietary model: public rasters sit behind a
`DeforestationProvider` interface as clearly-labelled stand-ins, and legality — which
no raster can establish — is modelled honestly as `NOT_ASSESSED` rather than invented.
Boring stack, scoped with restraint: one service, one database, one API, one map.

## Run it

```bash
docker compose up --build          # postgis + app: migrate -> seed the messy demo -> serve on :8000

# walk one messy submission to a withheld DDS:
curl -s localhost:8000/consignments/DEMO/dds | jq '{compliance_complete, legality_status, deforestation_determination, due_diligence_path, country_risk_class, n_plots: (.geojson.features|length)}'

# plot/run ids are content hashes (not the human submission ids), so read a real plot id
# out of the DDS, then drill into its rolled-up risk and the run's evidence trail:
plot=$(curl -s localhost:8000/consignments/DEMO/dds | jq -r '.plot_ids[0]')
curl -s localhost:8000/plots/$plot/risk | jq '{plot_id, assessed, risk: .risk.risk, disposition: .validation.disposition}'
run=$(curl -s localhost:8000/plots/$plot/risk | jq -r '.risk.evidence[0].run_id')
curl -s localhost:8000/runs/$run/replay | jq '{run_id, n_evidence: (.evidence|length)}'
```

`docker compose up` is the primary, reproducible delivery. The image pins
`postgis/postgis:18-3.6` by digest and targets `linux/amd64` — the postgis image has
no arm64 manifest and the `exactextract` wheel has no linux-aarch64 build, so the stack
is amd64 (native on CI runners, production, and Linux x86_64; emulated via BuildKit on
Apple Silicon — see `DECISIONS.md`). The same ingest → DDS flow is reproducible without
Docker: `veritas-eudr migrate && veritas-eudr run <submission> --consignment DEMO && veritas-eudr serve`
against any PostGIS ≥ 3.2 / GEOS ≥ 3.10 — that CLI → DB → API path is the one exercised
end-to-end in `EVIDENCE.md`. The map UI is served from `web/` (`python -m http.server`
there; see `web/README.md`).

## Pipeline

```
submission (GeoJSON / CSV / Excel)
  -> ingest      canonicalize to EPSG:4326 [lon,lat] 6dp; SHA-256 geom_hash; idempotent (ON CONFLICT)
  -> validate    typed ValidationReport; disposition AUTO_VALID | AUTO_FIXED | NEEDS_REVIEW
  -> area        geodesic ST_Area(geography) authority + EPSG:6933 cross-check; 4 ha format boundary
  -> deforestation  rasterio + exactextract fractional coverage -> ground hectares; convergence of evidence
  -> risk + DDS  low | high | more-info-needed; legality NOT_ASSESSED; TRACES-shaped, never fully-compliant
  -> FastAPI     /plots/{id}/risk  /consignments/{id}/dds  /runs/{id}/replay  /health  /metrics
```

PostGIS is the system of record and the vector spatial engine (`geometry(Geometry,4326)`
+ GiST, PL/pgSQL `fn_validate_plot` / `fn_area_hectares`). Risk is never decided by a
single dataset; the tiering mirrors the FAO/WRI **Whisp** `Risk_PCrop` decision tree and
credits it — it is not presented as novel.

## Datasets

Open licences first; non-commercial and attribution-required sources are flagged.

| Dataset | Role | Licence |
|---|---|---|
| Hansen GFC-2025-v1.13 `lossyear` | post-2020 tree-cover-loss time series (EPSG:4326 ~1 arc-sec) | CC-BY 4.0 |
| ESA WorldCover v200 | land-cover **context** (crop/tree) — not a commodity layer | CC-BY 4.0 |
| FDaP coffee model 2025b | optional commodity-presence axis (not built; interface ready) | CC-BY 4.0 |
| JRC GFC2020 V3 | forest-at-cutoff baseline (10 m) | free of charge, **attribution required** |
| Sample Earth (CIAT/Alliance) | land-cover reference points | **CC-BY-NC 4.0 (NonCommercial)** |
| Whisp REST API | self-grading reference (cached; opt-in) | see Whisp terms |

The committed `tests/fixtures` rasters/points are **synthetic** stand-ins for offline,
deterministic CI; `scripts/fetch_data.sh` names the exact real tiles + the Sample Earth
DOI and clips them to the AOI. AOI: the **Vietnam Central Highlands** robusta belt
around Buon Ma Thuot (~12.67 N — mid-latitude tropics, not the equator); the
committed **synthetic** fixtures cover a ~12.64–12.70 N window.

## Regulation (pinned)

Regulation (EU) **2023/1115** (EUDR), as amended by **2025/2650**, with country
benchmarking **Commission Implementing Regulation (EU) 2025/1093** → **Vietnam = low
risk → simplified due diligence** (Art. 13: skips the Art. 10/11 steps, 1% inspection,
but plot-level geolocation, DDS submission and 5-year retention still apply — *low-risk
is not no-diligence*). Deforestation cutoff **31 Dec 2020**, kept distinct from the
application date (30 Dec 2026, medium/large operators). Geometry conforms to the **EUDR
GeoJson File Description v1.5**. Citations with CELEX + access dates: `policy/eudr_policy.yaml`.

## How we read one plot

Take plot `pt-014`, a coffee point in the AOI. On its three layers it looks like a
clear **high-risk** case: it sits inside the 2020 forest baseline, has no cropland
context, and has tree-cover loss covering essentially the whole plot. The system still
returns **`more-info-needed`**, not `high`.

Why: the loss falls *only* in Hansen band 21 — calendar year 2021, the first annual
band after the 31 Dec 2020 cutoff. Hansen reports the *year of first detection*, with
latency, so a clearing in late 2019 or 2020 routinely surfaces in the 2021 band. We
cannot tell, from this data alone, whether the clearing happened before or after the
cutoff. Calling it `high` would risk wrongly blocking a farmer over a pre-cutoff event;
calling it `low` would risk clearing a real one. So the honest answer is *more
information is needed* — and the evidence trail records exactly which band drove that
(`mins.band21_latency`, `loss_fraction = 1.0`, `inside_2020_forest`), so a reviewer can see
the reasoning and a trader knows to request a higher-cadence source before deciding.
This is the plot the map opens by default; `docs/investigations/` works it in full.

## What ships

`docker compose up` end-to-end (ingest → validate → 4 ha format → exactextract
convergence → per-plot risk → withheld DDS); PostGIS GiST + PL/pgSQL with a real
`EXPLAIN (ANALYZE, BUFFERS)` benchmark; pathology + area-bound + idempotency +
replay/mutation tests; a versioned CELEX-cited policy; the Whisp diff tooling; a thin
read-only MapLibre map; and a validated (not applied) Terraform reference under
`deploy/`. See `EVIDENCE.md` for reproducible numbers and `DECISIONS.md` for the
design judgments and what was deliberately left out.

## Layout

```
src/veritas_eudr/{ingest,validate,area,deforestation,risk,api,obs,whisp}/  pipeline.py  cli.py  db.py  domain.py  config.py
policy/eudr_policy.yaml   migrations/  sql/functions/   tests/{fixtures,...}
docker-compose.yml  Dockerfile  docker/   .github/workflows/ci.yml
web/   web-demo/   deploy/   scripts/{fetch_data.sh,make_fixtures.py,benchmark_overlap.py,export_web_plots.py,export_demo_data.py}
README.md  DECISIONS.md  EVIDENCE.md  docs/investigations/
```

## License

MIT (code) — see `LICENSE`. Datasets retain their own licences (above).
