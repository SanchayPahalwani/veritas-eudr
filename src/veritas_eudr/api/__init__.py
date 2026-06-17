"""FastAPI surface over the EUDR backend spine.

The API is a thin, clinical read layer over the system of record. It exposes the
stored per-plot results, regenerates a consignment DDS on demand, replays a run's
append-only evidence trail, reports resolved build versions, and exposes
Prometheus metrics. Responses are plain typed JSON -- no human-stakes prose in
the payload.

Design rules honored here:
- The DB is a dependency, opened/closed per request. ``/health`` and ``/metrics``
  must NOT hard-fail when the DB is down: ``/health`` degrades to
  ``postgis="unavailable"``.
- Stored JSON columns are returned verbatim (``validation_report``, ``area``,
  ``risk``). The DDS is regenerated from the stored ``RiskProfile`` JSON plus the
  plot geometry, never persisted in a pre-baked form.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from geoalchemy2.shape import to_shape
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from veritas_eudr import __version__
from veritas_eudr import obs as observability
from veritas_eudr.db import EvidenceLedger, Plot, PlotResult, get_sessionmaker
from veritas_eudr.domain import RiskProfile
from veritas_eudr.risk import build_dds, build_eudr_geojson, validate_eudr_geojson

app = FastAPI(
    title="veritas-eudr",
    version=__version__,
    description=(
        "Read layer over the EUDR backend spine: per-plot risk, consignment DDS, "
        "replayable evidence trail, build info, and metrics."
    ),
)


def get_session() -> Iterator[Session]:
    """Per-request session dependency; always closed."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


# Annotated dependency keeps the call out of the argument default (ruff B008).
SessionDep = Annotated[Session, Depends(get_session)]


def _postgis_version(session: Session) -> str:
    """Return ``PostGIS_Full_Version()`` from the live DB, or ``"unavailable"``.

    Any connectivity/extension failure degrades to ``"unavailable"`` rather than
    failing the health check -- the API stays observable when the DB is down.
    """
    try:
        return str(session.execute(text("SELECT PostGIS_Full_Version()")).scalar_one())
    except Exception:
        return "unavailable"


def _latest_plot_result(session: Session, plot_id: str) -> PlotResult | None:
    """The most recent stored result for a plot (newest run first)."""
    stmt = (
        select(PlotResult)
        .where(PlotResult.plot_id == plot_id)
        .order_by(PlotResult.created_at.desc(), PlotResult.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


@app.get("/health")
def health() -> dict[str, object]:
    """Liveness + resolved build info. Degrades gracefully without a DB.

    ``postgis`` carries the live ``PostGIS_Full_Version()`` when the DB is
    reachable, otherwise ``"unavailable"`` -- the endpoint never hard-fails.
    """
    observability.record_request("/health", "GET")
    postgis = "unavailable"
    try:
        session = get_sessionmaker()()
        try:
            postgis = _postgis_version(session)
        finally:
            session.close()
    except Exception:
        postgis = "unavailable"
    return {"status": "ok", "build": observability.build_info(), "postgis": postgis}


@app.get("/plots/{plot_id}/risk")
def plot_risk(plot_id: str, session: SessionDep) -> dict[str, object]:
    """The latest stored validation / area / risk JSON for a plot. 404 if unknown."""
    observability.record_request("/plots/{plot_id}/risk", "GET")
    result = _latest_plot_result(session, plot_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no result for plot {plot_id!r}")
    # An unsampleable plot (e.g. location flagged NEEDS_REVIEW, or outside dataset
    # coverage) is recorded with its validation report but no area/risk: the pipeline
    # stores an empty {} for those columns. Surface that as null + assessed=False
    # rather than an empty object, so a reader cannot mistake it for a measurement.
    return {
        "plot_id": plot_id,
        "validation": result.validation_report,
        "area": result.area or None,
        "risk": result.risk or None,
        "assessed": bool(result.risk),
    }


@app.get("/consignments/{consignment_id}/dds")
def consignment_dds(consignment_id: str, session: SessionDep) -> dict[str, object]:
    """Regenerate the consignment's DDS from stored plots + RiskProfiles. 404 if unknown.

    Plots are loaded for the consignment (shapely geometry decoded from
    ``Plot.geom``); each plot's latest ``RiskProfile`` is reconstructed from the
    stored ``risk`` JSON via ``RiskProfile.model_validate`` and fed to
    ``risk.build_dds``. The DDS is built on demand, never persisted pre-baked.
    """
    observability.record_request("/consignments/{consignment_id}/dds", "GET")
    plots = (
        session.execute(select(Plot).where(Plot.consignment_id == consignment_id).order_by(Plot.id))
        .scalars()
        .all()
    )
    if not plots:
        raise HTTPException(status_code=404, detail=f"no plots for consignment {consignment_id!r}")

    operator_name = ""
    if plots[0].consignment is not None:
        operator_name = plots[0].consignment.operator_name

    plot_geoms: list[tuple[str, object]] = []
    profiles: list[RiskProfile] = []
    for plot in plots:
        result = _latest_plot_result(session, plot.id)
        # Skip plots that were not risk-assessed: pipeline stores an empty {} for
        # risk on unsampleable / NEEDS_REVIEW plots. They live in /plots and the
        # evidence trail, not the DDS.
        if result is None or not result.risk:
            continue
        geom = to_shape(plot.geom)
        # Mirror the pipeline (pipeline.py): only EUDR GeoJson v1.5-conformant plots
        # flow into the DDS. A non-conformant geometry (e.g. a doughnut / interior
        # ring) is recorded but excluded here -- otherwise build_dds rejects the
        # whole payload. An all-excluded consignment still yields a withheld DDS.
        if validate_eudr_geojson(build_eudr_geojson([(plot.id, geom)])):
            continue
        plot_geoms.append((plot.id, geom))
        profiles.append(RiskProfile.model_validate(result.risk))

    dds = build_dds(consignment_id, operator_name, plot_geoms, profiles)
    return dds.model_dump(mode="json")


@app.get("/runs/{run_id}/replay")
def run_replay(run_id: str, session: SessionDep) -> dict[str, object]:
    """The append-only evidence-ledger rows for a run, ordered -- the replay trail.

    Returns an empty ``evidence`` list for an unknown run rather than 404: a run
    that produced no evidence is a valid (if empty) replay.
    """
    observability.record_request("/runs/{run_id}/replay", "GET")
    rows = (
        session.execute(
            select(EvidenceLedger)
            .where(EvidenceLedger.run_id == run_id)
            .order_by(EvidenceLedger.id)
        )
        .scalars()
        .all()
    )
    evidence = [
        {
            "id": row.id,
            "run_id": row.run_id,
            "plot_id": row.plot_id,
            "dataset_name": row.dataset_name,
            "dataset_version": row.dataset_version,
            "rule_id": row.rule_id,
            "pixel_value": row.pixel_value,
            "covered_fraction": row.covered_fraction,
            "verdict": row.verdict,
            "ts": row.ts.isoformat() if row.ts is not None else None,
        }
        for row in rows
    ]
    return {"run_id": run_id, "evidence": evidence}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    """Prometheus exposition for the project registry."""
    body, content_type = observability.render_metrics()
    return PlainTextResponse(content=body, media_type=content_type)


__all__ = ["app"]
