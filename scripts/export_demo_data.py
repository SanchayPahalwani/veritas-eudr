"""Deterministic exporter: real pipeline output -> static JSON for the demo SPA.

The recruiter-facing demo under ``web-demo/`` is a pure static Next.js app: it
must work on Vercel with **no backend, no database, no network** at runtime. So
we run the SAME pure pipeline the real system uses, once, offline, and freeze its
output as committed JSON whose shapes are **byte-identical to the live API**:

    web-demo/public/data/
      plots.geojson           <- the ~50 AOI plots for the map (== web/plots.geojson)
      plots_index.json        <- run_id, risk tier counts, band-21 ids, hero id
      plot_risk/<id>.json     <- one per plot; SAME shape as GET /plots/{id}/risk
      consignment_dds.json    <- SAME shape as GET /consignments/{id}/dds (withheld)
      evidence_ledger.json    <- SAME shape as GET /runs/{id}/replay (append-only)
      area_demo.json          <- one polygon's multi-basis area (the Web-Mercator lie)
      manifest.json           <- provenance: run_id, policy version, source fixtures

Because the live API reconstructs each of these from the same domain models
(``ValidationReport`` / ``AreaMeasurement`` / ``RiskProfile`` / ``EvidenceRecord``
/ ``DueDiligenceStatement``), a frontend written against these files would work
unchanged against a running backend -- the static snapshot is not a mock, it is
the real engine's output with the clock pinned.

Determinism / idempotency:
- Input is the committed ``tests/fixtures/points/coffee_points.geojson`` (offline,
  synthetic, replays identically against the committed raster fixtures).
- ``process_features`` is pure; feature order follows the source file.
- The only non-deterministic fields the domain models carry are wall-clock stamps
  (``EvidenceRecord.ts``, ``DueDiligenceStatement.generated_at`` / validity dates)
  and the reference-number nonce. We PIN all of them to fixed, internally
  consistent values (the verification number stays a valid HMAC of the reference),
  so re-running overwrites every file byte-for-byte and reviews cleanly in a diff.

Run:
    .venv/bin/python scripts/export_demo_data.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

# Reuse the exact, already-committed map exporter so plots.geojson stays in lockstep
# with web/plots.geojson (same property contract, same rounding, same ordering).
from export_web_plots import build_feature_collection  # noqa: E402  (script-local import)
from shapely.geometry import Point, Polygon

from veritas_eudr import area as area_mod
from veritas_eudr.config import PROJECT_ROOT, get_settings, load_policy, policy_version
from veritas_eudr.ingest import parse_submission
from veritas_eudr.pipeline import PlotOutcome, _run_id_from_submission, process_features
from veritas_eudr.risk import build_dds, make_verification_number
from veritas_eudr.validate import validate_plot

# --------------------------------------------------------------------------- #
# Paths + demo identity
# --------------------------------------------------------------------------- #

SUBMISSION = PROJECT_ROOT / "tests" / "fixtures" / "points" / "coffee_points.geojson"
OUT_DIR = PROJECT_ROOT / "web-demo" / "public" / "data"

# A believable consignment for the AOI: a robusta cooperative in the Central
# Highlands (Đắk Lắk). These are presentation labels only -- the risk verdicts
# and DDS withholding come entirely from the engine.
OPERATOR_NAME = "Đắk Lắk Highlands Robusta Cooperative"
CONSIGNMENT_ID = "VN-DLK-2026-Q2"
HERO_PLOT_ID = "pt-014"  # the band-21 (tripwire B) story plot

# Pinned clock: a fixed "as-of" instant so every stamp is reproducible. Chosen as a
# plain UTC midnight so the diff is obvious and the dates read as recent.
PINNED_DATE = date(2026, 6, 18)
PINNED_TS = "2026-06-18T00:00:00+00:00"

# The textbook latitude for the Web-Mercator inflation caption (AOI mid-latitude).
AOI_LAT = 12.67
AOI_CENTER = [108.03, 12.66]

_COORD_DECIMALS = 6


# --------------------------------------------------------------------------- #
# Serialization (stable, diff-friendly)
# --------------------------------------------------------------------------- #


def _dumps(obj: Any) -> str:
    """Stable JSON text: sorted keys, fixed indent, trailing newline."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write(rel_path: str, obj: Any) -> None:
    path = OUT_DIR / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dumps(obj), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Per-plot risk JSON  (== GET /plots/{plot_id}/risk)
# --------------------------------------------------------------------------- #


def _plot_risk_json(outcome: PlotOutcome) -> dict[str, Any]:
    """One plot's frozen risk payload, identical in shape to the API endpoint.

    ``area`` / ``risk`` are null for an unsampleable plot (mirrors the API, which
    surfaces the empty stored columns as null + assessed=False). Evidence-record
    timestamps are pinned for determinism.
    """
    risk_json: dict[str, Any] | None = None
    if outcome.risk is not None:
        risk_json = outcome.risk.model_dump(mode="json")
        for record in risk_json.get("evidence", []):
            record["ts"] = PINNED_TS

    return {
        "plot_id": outcome.plot_id,
        "validation": outcome.validation.model_dump(mode="json"),
        "area": outcome.area.model_dump(mode="json") if outcome.area is not None else None,
        "risk": risk_json,
        "assessed": outcome.risk is not None,
    }


# --------------------------------------------------------------------------- #
# Consignment DDS JSON  (== GET /consignments/{id}/dds)
# --------------------------------------------------------------------------- #


def _consignment_dds_json(outcomes: list[PlotOutcome], run_id: str) -> dict[str, Any]:
    """Assemble the (always withheld) consignment DDS over every sampleable,
    v1.5-conformant plot -- exactly the set the DB pipeline/API would submit.

    The clock-derived fields are pinned afterwards while keeping the
    reference/verification pairing internally valid (verification = HMAC(reference)).
    """
    # Every sampleable plot (carries a RiskProfile) flows into the DDS, paired with
    # its canonical geometry -- the same set the DB pipeline/API submit. build_dds
    # enforces v1.5 conformance and raises on a non-conformant payload.
    plot_geoms = [(o.plot_id, o._geom) for o in outcomes if o.risk is not None]
    profiles = [o.risk for o in outcomes if o.risk is not None]

    dds = build_dds(CONSIGNMENT_ID, OPERATOR_NAME, plot_geoms, profiles)
    dds_json = dds.model_dump(mode="json")

    # Pin the clock-derived fields, keeping the pairing valid.
    valid_for_days = int(load_policy()["dds_validity"]["valid_for_days"])
    reference = f"EUDR-{PINNED_DATE:%Y}-{CONSIGNMENT_ID}-{run_id[:8].upper()}"
    dds_json["reference_number"] = reference
    dds_json["verification_number"] = make_verification_number(reference)
    dds_json["valid_from"] = PINNED_DATE.isoformat()
    dds_json["valid_until"] = (PINNED_DATE + timedelta(days=valid_for_days)).isoformat()
    dds_json["generated_at"] = PINNED_TS
    return dds_json


# --------------------------------------------------------------------------- #
# Evidence ledger JSON  (== GET /runs/{run_id}/replay)
# --------------------------------------------------------------------------- #


def _evidence_ledger_json(outcomes: list[PlotOutcome], run_id: str) -> dict[str, Any]:
    """Flatten every plot's append-only evidence into the run's replay trail, with
    monotonic ids (the durable-record row ids) and pinned timestamps."""
    evidence: list[dict[str, Any]] = []
    next_id = 1
    for outcome in outcomes:
        if outcome.risk is None:
            continue
        for record in outcome.risk.evidence:
            row = record.model_dump(mode="json")
            row["id"] = next_id
            row["ts"] = PINNED_TS
            evidence.append(row)
            next_id += 1
    return {"run_id": run_id, "evidence": evidence}


# --------------------------------------------------------------------------- #
# Area demo JSON (the Web-Mercator lie, on a real >4 ha polygon)
# --------------------------------------------------------------------------- #


def _area_demo_json() -> dict[str, Any]:
    """Measure one synthetic ~4.3 ha polygon so the narrative can animate the real
    geodesic vs EPSG:6933 vs Web-Mercator figures (the AOI fixtures are all points,
    so they carry no Web-Mercator delta)."""
    cx, cy = 108.045, 12.665
    half = 0.00095  # ~0.0019 deg square -> ~4.3 ha at this latitude
    polygon = Polygon(
        [
            (cx - half, cy - half),
            (cx + half, cy - half),
            (cx + half, cy + half),
            (cx - half, cy + half),
            (cx - half, cy - half),
        ]
    )
    measurement = area_mod.measure(polygon)
    out = measurement.model_dump(mode="json")
    out["aoi_lat"] = AOI_LAT
    out["sec2_lat_factor"] = area_mod.webmercator_inflation_factor(AOI_LAT)
    return out


# --------------------------------------------------------------------------- #
# Validation showcase (the "validate" pipeline stage)
# --------------------------------------------------------------------------- #

# A curated spectrum of real submission pathologies, ordered AUTO_VALID ->
# AUTO_FIXED -> NEEDS_REVIEW. Each case is run through the SAME `validate_plot`
# the pipeline uses; the disposition + findings below are the engine's, not
# scripted. Geometries mirror the committed messy_submission fixture where one
# exists; the two AUTO_FIXED cases (safe, area-preserving repairs) are added here
# because that fixture happens to carry none.
_VALIDATION_CASES: list[dict[str, Any]] = [
    {
        "scenario": "clean_valid_polygon",
        "title": "Clean sub-4 ha polygon",
        "blurb": "Simple, closed, in-AOI polygon. Passes untouched.",
        "geom": Polygon(
            [
                (108.0055, 12.6455),
                (108.0065, 12.6455),
                (108.0065, 12.6465),
                (108.0055, 12.6465),
                (108.0055, 12.6455),
            ]
        ),
        "properties": {"id": "sub-clean-poly"},
    },
    {
        "scenario": "unclosed_ring",
        "title": "Unclosed ring",
        "blurb": "First vertex != last. Closing the ring is area-preserving — safe to auto-fix.",
        "geom": "POLYGON((108.0055 12.6455, 108.0065 12.6455, 108.0065 12.6465, 108.0055 12.6465))",
        "properties": {"id": "sub-unclosed-ring"},
    },
    {
        "scenario": "duplicate_vertices",
        "title": "Duplicate vertices",
        "blurb": "A stationary GPS logger repeated a vertex. Dropping it leaves the geometry unchanged.",
        "geom": Polygon(
            [
                (108.0055, 12.6455),
                (108.0065, 12.6455),
                (108.0065, 12.6455),  # consecutive duplicate
                (108.0065, 12.6465),
                (108.0055, 12.6465),
                (108.0055, 12.6455),
            ]
        ),
        "properties": {"id": "sub-dup-vertices"},
    },
    {
        "scenario": "lat_lon_swapped_point",
        "title": "Lat/lon swap",
        "blurb": "Spreadsheet wrote [lat, lon]; swapped, it lands outside Vietnam. Never auto-swap.",
        "geom": Point(12.646, 108.046),
        "properties": {"id": "sub-latlon-swap"},
    },
    {
        "scenario": "polygon_with_hole",
        "title": "Doughnut (interior ring)",
        "blurb": "EUDR GeoJSON v1.5 does not process interior rings. Dropping the hole changes the boundary.",
        "geom": Polygon(
            [
                (108.0352, 12.6452),
                (108.0368, 12.6452),
                (108.0368, 12.6468),
                (108.0352, 12.6468),
                (108.0352, 12.6452),
            ],
            holes=[
                [
                    (108.0358, 12.6458),
                    (108.0358, 12.6462),
                    (108.0362, 12.6462),
                    (108.0362, 12.6458),
                    (108.0358, 12.6458),
                ]
            ],
        ),
        "properties": {"id": "sub-hole-poly"},
    },
    {
        "scenario": "self_intersecting_bowtie",
        "title": "Self-intersecting bow-tie",
        "blurb": "Repair fragments one polygon into two. The fix would guess intent — escalate.",
        "geom": (
            "POLYGON((108.015400 12.645400, 108.016600 12.646600, "
            "108.016600 12.645400, 108.015400 12.646600, 108.015400 12.645400))"
        ),
        "properties": {"id": "sub-bowtie"},
    },
    {
        "scenario": "over_4ha_point",
        "title": "Point for a >4 ha plot",
        "blurb": "Art. 9(1)(d): ≥4 ha needs a perimeter polygon. A point cannot prove eligibility.",
        "geom": Point(108.03, 12.685),
        "properties": {"id": "sub-bigpoint", "asserted_area_ha": 5.2},
    },
]


def _validation_showcase_json(settings: Any) -> dict[str, Any]:
    """Run each curated case through `validate_plot` and freeze the engine's
    disposition + findings — the data behind the pipeline's `validate` stage."""
    cases: list[dict[str, Any]] = []
    for case in _VALIDATION_CASES:
        report = validate_plot(case["geom"], properties=case["properties"], settings=settings)
        cases.append(
            {
                "scenario": case["scenario"],
                "title": case["title"],
                "blurb": case["blurb"],
                "plot_id": case["properties"]["id"],
                "source_geometry_type": report.source_geometry_type,
                "disposition": report.disposition.value,
                "needs_review": report.needs_review,
                "findings": [
                    {
                        "rule_id": f.rule_id,
                        "severity": f.severity.value,
                        "disposition": f.disposition.value,
                        "human_reason": f.human_reason,
                        "failing_coordinate": f.failing_coordinate,
                    }
                    for f in report.findings
                ],
            }
        )
    return {"cases": cases}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    settings = get_settings()
    features = parse_submission(SUBMISSION)
    geom_by_id = {f.external_id: f.geometry for f in features if f.geometry is not None}
    run_id = _run_id_from_submission(features)
    outcomes = process_features(features, run_id, settings=settings)

    # Attach the canonical geometry to each outcome so the DDS builder can use it
    # (PlotOutcome is frozen; stash on a private attribute for this in-process pass).
    for outcome in outcomes:
        object.__setattr__(outcome, "_geom", geom_by_id.get(outcome.plot_id))

    # 1. Map data (reuse the committed exporter verbatim).
    fc = build_feature_collection()
    _write("plots.geojson", fc)

    # 2. Per-plot risk payloads (API-identical).
    for outcome in outcomes:
        _write(f"plot_risk/{outcome.plot_id}.json", _plot_risk_json(outcome))

    # 3. Consignment DDS (withheld) + evidence ledger (replay).
    _write("consignment_dds.json", _consignment_dds_json(outcomes, run_id))
    _write("evidence_ledger.json", _evidence_ledger_json(outcomes, run_id))

    # 4. The Web-Mercator area demonstration.
    _write("area_demo.json", _area_demo_json())

    # 4b. The validation-stage showcase (AUTO_VALID -> AUTO_FIXED -> NEEDS_REVIEW).
    _write("validation_showcase.json", _validation_showcase_json(settings))

    # 5. Index + manifest (provenance the console footer surfaces).
    counts: dict[str, int] = {}
    band21: list[str] = []
    for outcome in outcomes:
        tier = outcome.risk.risk.value if outcome.risk is not None else "unassessed"
        counts[tier] = counts.get(tier, 0) + 1
        if outcome.risk is not None and outcome.risk.boundary_uncertain:
            band21.append(outcome.plot_id)

    _write(
        "plots_index.json",
        {
            "run_id": run_id,
            "counts": counts,
            "band21_plot_ids": sorted(band21),
            "hero_plot_id": HERO_PLOT_ID,
            "aoi_center": AOI_CENTER,
            "n_plots": len(fc["features"]),
        },
    )
    _write(
        "manifest.json",
        {
            "run_id": run_id,
            "generated_at": PINNED_TS,
            "policy_version": policy_version(),
            "operator_name": OPERATOR_NAME,
            "consignment_id": CONSIGNMENT_ID,
            "source_fixtures": ["tests/fixtures/points/coffee_points.geojson"],
            "counts": counts,
        },
    )

    print(f"wrote demo data -> {OUT_DIR.relative_to(PROJECT_ROOT)}")
    print(f"  run_id: {run_id}")
    print(f"  plots: {len(fc['features'])}  risk tiers: {dict(sorted(counts.items()))}")
    print(f"  band-21 plots: {band21}")


if __name__ == "__main__":
    main()
