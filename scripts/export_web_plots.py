"""Deterministic exporter: AOI points -> web/plots.geojson for the map UI.

A committed, idempotent build step for the read-only web map under ``web/``. It
runs the SAME pure pipeline the demo uses (no database, no live third-party
call):

    ingest.parse_submission  ->  pipeline.process_features
                                 (validate -> area -> deforestation.assess_plot)

and writes one GeoJSON FeatureCollection (``web/plots.geojson``) whose features
carry exactly the properties the map needs to colour and explain each plot:

    plot_id            the canonical feature id (e.g. "pt-014")
    risk               "low" | "high" | "more-info-needed" | null
                       (null == not risk-assessed: an unsampleable location)
    disposition        the rolled-up validation disposition
                       (AUTO_VALID | AUTO_FIXED | NEEDS_REVIEW)
    boundary_uncertain the Hansen band-21 latency flag (tripwire B); the default
                       click-through plot on the map is one of these
    rationale          the human-readable risk rationale (or the validation note
                       for an unsampleable plot)
    n_evidence         number of append-only evidence-ledger records behind the
                       verdict (0 when not risk-assessed)

Determinism / idempotency:
- The input is the committed ``tests/fixtures/points/coffee_points.geojson``.
- ``process_features`` is pure and replays identically against the committed
  raster fixtures; feature order follows the source file order.
- Coordinates are written at the 6 dp regulatory grid the canonical form uses.
- Re-running overwrites ``web/plots.geojson`` byte-for-byte (sorted keys, fixed
  separators, trailing newline), so it is safe to commit and diff.

Run:
    .venv/bin/python scripts/export_web_plots.py
"""

from __future__ import annotations

import json
from typing import Any

from shapely.geometry import mapping

from veritas_eudr.config import PROJECT_ROOT, get_settings
from veritas_eudr.ingest import parse_submission
from veritas_eudr.pipeline import PlotOutcome, _run_id_from_submission, process_features

# Input: the committed ~50-point AOI submission (synthetic, offline, CC-safe).
SUBMISSION = PROJECT_ROOT / "tests" / "fixtures" / "points" / "coffee_points.geojson"
# Output: consumed directly by web/app.js as a MapLibre GeoJSON source.
OUTPUT = PROJECT_ROOT / "web" / "plots.geojson"

# 6 dp regulatory grid (matches ingest.COORD_DECIMALS) -- keeps the written
# coordinates stable and free of float-repr drift across runs/platforms.
_COORD_DECIMALS = 6


def _round_geometry(geojson_geom: dict[str, Any]) -> dict[str, Any]:
    """Round every coordinate of a GeoJSON geometry to the 6 dp grid in place-safe
    fashion (returns a new dict). Deterministic, platform-independent output."""

    def _round(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [_round(v) for v in value]
        if isinstance(value, float):
            return round(value, _COORD_DECIMALS)
        return value

    geom = dict(geojson_geom)
    geom["coordinates"] = _round(geom["coordinates"])
    return geom


def _feature_for(outcome: PlotOutcome, geometry: dict[str, Any]) -> dict[str, Any]:
    """Build one GeoJSON Feature with the map's property contract.

    ``risk`` is null for an unsampleable plot (no trustworthy location to sample
    against the rasters); in that case the rationale falls back to the worst
    validation finding's reason so the popup still explains the disposition.
    """
    report = outcome.validation
    profile = outcome.risk

    if profile is not None:
        risk: str | None = profile.risk.value
        boundary_uncertain = bool(profile.boundary_uncertain)
        rationale = profile.rationale
        n_evidence = len(profile.evidence)
    else:
        # Not risk-assessed (unsampleable location). Surface the dominating
        # validation finding's reason so the click-through is still informative.
        risk = None
        boundary_uncertain = False
        n_evidence = 0
        worst = next(
            (f for f in report.findings if f.disposition == report.disposition),
            None,
        )
        rationale = (
            worst.human_reason
            if worst is not None
            else "Location could not be sampled against the AOI rasters; not risk-assessed."
        )

    return {
        "type": "Feature",
        "id": outcome.plot_id,
        "geometry": geometry,
        "properties": {
            "plot_id": outcome.plot_id,
            "risk": risk,
            "disposition": str(report.disposition.value),
            "boundary_uncertain": boundary_uncertain,
            "rationale": rationale,
            "n_evidence": n_evidence,
        },
    }


def build_feature_collection() -> dict[str, Any]:
    """Run the pure pipeline over the AOI submission and assemble the map's
    FeatureCollection. No database, no network."""
    settings = get_settings()
    features = parse_submission(SUBMISSION)
    # Map external_id -> canonical shapely geometry for the geometry of each plot.
    geom_by_id = {f.external_id: f.geometry for f in features if f.geometry is not None}

    run_id = _run_id_from_submission(features)
    outcomes = process_features(features, run_id, settings=settings)

    out_features: list[dict[str, Any]] = []
    for outcome in outcomes:
        geom = geom_by_id.get(outcome.plot_id)
        if geom is None:
            # A raw-WKT pathology with no usable geometry: nothing to draw on the
            # map (it is validated and recorded elsewhere, just not plottable).
            continue
        geometry = _round_geometry(mapping(geom))
        out_features.append(_feature_for(outcome, geometry))

    return {
        "type": "FeatureCollection",
        "name": "veritas_eudr_web_plots",
        "_note": (
            "Deterministic export of process_features over the synthetic AOI "
            "submission; properties drive the read-only web map. Regenerate with "
            "scripts/export_web_plots.py."
        ),
        "_run_id": run_id,
        "features": out_features,
    }


def main() -> None:
    fc = build_feature_collection()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Stable serialization: sorted keys + fixed separators + trailing newline so
    # the file is byte-identical across runs and reviews cleanly in a diff.
    text = json.dumps(fc, indent=2, sort_keys=True, ensure_ascii=False)
    OUTPUT.write_text(text + "\n", encoding="utf-8")

    risk_counts: dict[str, int] = {}
    n_band21 = 0
    for feat in fc["features"]:
        key = str(feat["properties"]["risk"])
        risk_counts[key] = risk_counts.get(key, 0) + 1
        if feat["properties"]["boundary_uncertain"]:
            n_band21 += 1
    print(f"wrote {OUTPUT.relative_to(PROJECT_ROOT)}: {len(fc['features'])} features")
    print(f"  risk tiers: {dict(sorted(risk_counts.items()))}")
    print(f"  boundary_uncertain (band-21) features: {n_band21}")


if __name__ == "__main__":
    main()
