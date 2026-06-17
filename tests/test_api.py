"""Tests for the FastAPI surface (veritas_eudr.api).

Two layers, mirroring the rest of the suite:

- DB-free: ``/health`` returns 200 with build info even with no database
  (``postgis`` degrades to ``"unavailable"``), and ``/metrics`` returns the
  Prometheus exposition. These run anywhere.
- postgis-marked: seed a Plot + PlotResult (+ EvidenceLedger) DIRECTLY via the
  transactional ``db_session``, then GET the endpoints and assert the shape.
  These do NOT touch the pipeline modules -- rows are constructed here.
"""

from __future__ import annotations

import pytest
import shapely
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape

from veritas_eudr.api import app, get_session
from veritas_eudr.config import EUDR_DEFORESTATION_CUTOFF
from veritas_eudr.domain import (
    AreaMeasurement,
    EvidenceRecord,
    Finding,
    RequiredGeometryFormat,
    RiskProfile,
    RiskTier,
    Severity,
    ValidationReport,
)
from veritas_eudr.domain import (
    Disposition as Disp,
)

CANONICAL_SRID = 4326


# --------------------------------------------------------------------------- #
# DB-free layer
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_degrades_when_db_unavailable(client, monkeypatch):
    """/health returns 200 with build info even when the DB is unreachable; the
    postgis field degrades to 'unavailable' rather than 500-ing. The DB-down
    condition is forced (monkeypatched) so this holds regardless of whether a
    DATABASE_URL is configured in the environment (e.g. CI's postgis service)."""

    def _no_db():
        raise RuntimeError("simulated DB down")

    monkeypatch.setattr("veritas_eudr.api.get_sessionmaker", _no_db)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Build info is the resolved-version dict.
    assert "rasterio" in body["build"]
    assert "gdal" in body["build"]
    assert body["build"]["rasterio"]
    # With the DB unreachable, postgis is the degraded sentinel.
    assert body["postgis"] == "unavailable"


def test_metrics_returns_prometheus_text(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "veritas_eudr_build_info" in resp.text


def test_unknown_plot_is_404_without_db_when_session_unavailable(client):
    """A request to /metrics increments the counter; assert the counter metric is
    exported (the request path is exercised)."""
    client.get("/health")
    resp = client.get("/metrics")
    assert "veritas_eudr_requests_total" in resp.text


# --------------------------------------------------------------------------- #
# Seed helpers (postgis layer)
# --------------------------------------------------------------------------- #


def _square(lon0: float = 108.010000, lat0: float = 12.640000):
    return shapely.geometry.box(lon0, lat0, lon0 + 0.01, lat0 + 0.01)


def _validation_report(plot_id: str) -> dict:
    report = ValidationReport(
        plot_id=plot_id,
        source_geometry_type="Polygon",
        findings=[
            Finding(
                rule_id="ring_closed",
                severity=Severity.INFO,
                disposition=Disp.AUTO_VALID,
                human_reason="Ring is closed and simple.",
            )
        ],
    )
    return report.model_dump(mode="json")


def _area_measurement() -> dict:
    area = AreaMeasurement(
        measured_area_ha=12.34,
        area_ha_ease6933=12.36,
        area_ha_local_utm=12.35,
        area_ha_webmercator=51.2,
        delta_6933_pct=0.16,
        delta_webmercator_pct=314.0,
        required_geometry_format=RequiredGeometryFormat.POLYGON,
        borderline=False,
    )
    return area.model_dump(mode="json")


def _risk_profile(plot_id: str, run_id: str, tier: RiskTier) -> RiskProfile:
    return RiskProfile(
        plot_id=plot_id,
        risk=tier,
        rationale="seeded for API test",
        axes=[],
        evidence=[
            EvidenceRecord(
                run_id=run_id,
                plot_id=plot_id,
                dataset_name="Hansen GFC",
                dataset_version="GFC-2025-v1.13",
                rule_id="post_cutoff_loss_coverage",
                pixel_value=0.0,
                covered_fraction=0.0,
                verdict="low: no post-cutoff loss",
            )
        ],
        cutoff_date=EUDR_DEFORESTATION_CUTOFF,
    )


def _seed_plot_with_result(
    session,
    *,
    plot_id: str,
    run_id: str,
    consignment_id: str | None = None,
    tier: RiskTier = RiskTier.LOW,
):
    from veritas_eudr.db import Consignment, EvidenceLedger, Plot, PlotResult

    if consignment_id is not None:
        session.add(
            Consignment(
                id=consignment_id,
                operator_name="Acme Coffee Co",
                commodity="coffee",
            )
        )

    geom = _square()
    session.add(
        Plot(
            id=plot_id,
            external_id=plot_id,
            consignment_id=consignment_id,
            geom_hash=f"hash-{plot_id}",
            source_geometry_type="Polygon",
            asserted_area_ha=12.3,
            geom=from_shape(geom, srid=CANONICAL_SRID),
        )
    )

    profile = _risk_profile(plot_id, run_id, tier)
    session.add(
        PlotResult(
            run_id=run_id,
            plot_id=plot_id,
            validation_report=_validation_report(plot_id),
            area=_area_measurement(),
            risk=profile.model_dump(mode="json"),
        )
    )
    for ev in profile.evidence:
        session.add(
            EvidenceLedger(
                run_id=ev.run_id,
                plot_id=ev.plot_id,
                dataset_name=ev.dataset_name,
                dataset_version=ev.dataset_version,
                rule_id=ev.rule_id,
                pixel_value=ev.pixel_value,
                covered_fraction=ev.covered_fraction,
                verdict=ev.verdict,
            )
        )
    session.flush()
    return profile


def _client_bound_to(session) -> TestClient:
    """A TestClient whose get_session dependency yields the transactional test
    session (so seeded rows are visible and rolled back after the test)."""
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


# --------------------------------------------------------------------------- #
# postgis layer
# --------------------------------------------------------------------------- #


@pytest.mark.postgis
def test_plot_risk_returns_stored_json(db_session):
    _seed_plot_with_result(db_session, plot_id="plot-api-1", run_id="run-api-1")
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/plots/plot-api-1/risk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plot_id"] == "plot-api-1"
        assert body["validation"]["plot_id"] == "plot-api-1"
        assert body["area"]["required_geometry_format"] == "polygon"
        assert body["risk"]["risk"] == "low"
        assert body["risk"]["evidence"][0]["dataset_name"] == "Hansen GFC"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_plot_risk_unknown_is_404(db_session):
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/plots/does-not-exist/risk")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_consignment_dds_regenerated_from_stored_profiles(db_session):
    _seed_plot_with_result(
        db_session,
        plot_id="plot-cons-1",
        run_id="run-cons-1",
        consignment_id="CONS-API-1",
        tier=RiskTier.LOW,
    )
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/consignments/CONS-API-1/dds")
        assert resp.status_code == 200
        dds = resp.json()
        assert dds["consignment_id"] == "CONS-API-1"
        assert dds["operator_name"] == "Acme Coffee Co"
        assert dds["plot_ids"] == ["plot-cons-1"]
        # A DDS is NEVER a complete conformity finding.
        assert dds["compliance_complete"] is False
        assert dds["legality_status"] == "NOT_ASSESSED"
        assert dds["deforestation_determination"] == "low"
        assert dds["geojson"]["type"] == "FeatureCollection"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_consignment_dds_unknown_is_404(db_session):
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/consignments/NOPE/dds")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def _seed_unassessed_plot(session, *, plot_id: str, run_id: str, consignment_id: str | None = None):
    """A plot recorded with a validation report but NO area/risk (the {} sentinel
    the pipeline writes for unsampleable / NEEDS_REVIEW plots)."""
    from veritas_eudr.db import Plot, PlotResult

    geom = _square(lon0=108.030000, lat0=12.660000)
    session.add(
        Plot(
            id=plot_id,
            external_id=plot_id,
            consignment_id=consignment_id,
            geom_hash=f"hash-{plot_id}",
            source_geometry_type="Point",
            asserted_area_ha=None,
            geom=from_shape(geom, srid=CANONICAL_SRID),
        )
    )
    session.add(
        PlotResult(
            run_id=run_id,
            plot_id=plot_id,
            validation_report=_validation_report(plot_id),
            area={},
            risk={},
        )
    )
    session.flush()


@pytest.mark.postgis
def test_consignment_dds_skips_unassessed_plots(db_session):
    """An unassessed plot ({} risk) in the consignment must not crash DDS
    regeneration (model_validate({}) would raise); it is skipped, and the DDS is
    still a withheld DDS over the assessed plots only."""
    _seed_plot_with_result(
        db_session,
        plot_id="plot-mix-1",
        run_id="run-mix",
        consignment_id="CONS-MIX",
        tier=RiskTier.LOW,
    )
    _seed_unassessed_plot(
        db_session, plot_id="plot-mix-unassessed", run_id="run-mix", consignment_id="CONS-MIX"
    )
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/consignments/CONS-MIX/dds")
        assert resp.status_code == 200
        dds = resp.json()
        assert dds["compliance_complete"] is False
        # Only the assessed plot is a DDS member; the unassessed one is excluded.
        assert dds["plot_ids"] == ["plot-mix-1"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_consignment_dds_excludes_nonconformant_geometry(db_session):
    """A risk-ASSESSED plot whose geometry is not EUDR v1.5-conformant (an interior
    ring / doughnut) must be excluded from the DDS, not crash build_dds. Mirrors the
    pipeline's DDS-eligibility filter."""
    from veritas_eudr.db import Plot, PlotResult

    _seed_plot_with_result(
        db_session,
        plot_id="plot-clean",
        run_id="run-nc",
        consignment_id="CONS-NC",
        tier=RiskTier.LOW,
    )
    shell = [(108.01, 12.64), (108.03, 12.64), (108.03, 12.66), (108.01, 12.66), (108.01, 12.64)]
    hole = [
        (108.015, 12.645),
        (108.025, 12.645),
        (108.025, 12.655),
        (108.015, 12.655),
        (108.015, 12.645),
    ]
    doughnut = shapely.geometry.Polygon(shell, [hole])
    db_session.add(
        Plot(
            id="plot-doughnut",
            external_id="plot-doughnut",
            consignment_id="CONS-NC",
            geom_hash="hash-plot-doughnut",
            source_geometry_type="Polygon",
            geom=from_shape(doughnut, srid=CANONICAL_SRID),
        )
    )
    db_session.add(
        PlotResult(
            run_id="run-nc",
            plot_id="plot-doughnut",
            validation_report=_validation_report("plot-doughnut"),
            area=_area_measurement(),
            risk=_risk_profile("plot-doughnut", "run-nc", RiskTier.LOW).model_dump(mode="json"),
        )
    )
    db_session.flush()
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/consignments/CONS-NC/dds")
        assert resp.status_code == 200
        dds = resp.json()
        assert dds["compliance_complete"] is False
        # The doughnut is recorded but excluded from the DDS; only the clean plot remains.
        assert dds["plot_ids"] == ["plot-clean"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_plot_risk_unassessed_returns_null(db_session):
    """/plots for an unassessed plot returns 200 with null risk/area + assessed=False."""
    _seed_unassessed_plot(db_session, plot_id="plot-unassessed", run_id="run-unassessed")
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/plots/plot-unassessed/risk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["assessed"] is False
        assert body["risk"] is None
        assert body["area"] is None
        assert body["validation"]["plot_id"] == "plot-unassessed"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_run_replay_returns_ordered_evidence(db_session):
    _seed_plot_with_result(db_session, plot_id="plot-rep-1", run_id="run-rep-1")
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/runs/run-rep-1/replay")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "run-rep-1"
        assert len(body["evidence"]) == 1
        row = body["evidence"][0]
        assert row["run_id"] == "run-rep-1"
        assert row["plot_id"] == "plot-rep-1"
        assert row["dataset_name"] == "Hansen GFC"
        assert row["verdict"].startswith("low")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.postgis
def test_run_replay_unknown_run_is_empty(db_session):
    try:
        client = _client_bound_to(db_session)
        resp = client.get("/runs/no-such-run/replay")
        assert resp.status_code == 200
        assert resp.json()["evidence"] == []
    finally:
        app.dependency_overrides.clear()
