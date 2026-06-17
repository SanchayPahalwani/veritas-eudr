"""Tests for DDS assembly, the due-diligence path, validity window, the TRACES
stub, and one end-to-end composition test (assess_plot -> build_dds).

The central assertion the whole module exists to make: the system NEVER emits a
fully-compliant DDS. Every assembled statement is withheld (compliance_complete
False, legality NOT_ASSESSED), even when every plot is LOW risk.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import shapely
from shapely.geometry import shape

from veritas_eudr.config import EUDR_DEFORESTATION_CUTOFF, load_policy, policy_version
from veritas_eudr.deforestation import RasterProvider, assess_plot
from veritas_eudr.domain import (
    DueDiligencePath,
    LegalityStatus,
    RiskProfile,
    RiskTier,
)
from veritas_eudr.risk import (
    TracesStubClient,
    build_dds,
    verify_pairing,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POINTS = PROJECT_ROOT / "tests" / "fixtures" / "points" / "coffee_points.geojson"


def _clean_square(lon0: float = 108.010000, lat0: float = 12.640000):
    return shapely.geometry.box(lon0, lat0, lon0 + 0.01, lat0 + 0.01)


def _profile(plot_id: str, risk: RiskTier) -> RiskProfile:
    return RiskProfile(
        plot_id=plot_id,
        risk=risk,
        rationale="test",
        axes=[],
        evidence=[],
        cutoff_date=EUDR_DEFORESTATION_CUTOFF,
    )


def _build_simple_dds(risk: RiskTier = RiskTier.LOW):
    plots = [("plot-1", _clean_square())]
    profiles = [_profile("plot-1", risk)]
    return build_dds("CONS-1", "Acme Coffee Co", plots, profiles)


# --------------------------------------------------------------------------- #
# A DDS is never a complete conformity finding
# --------------------------------------------------------------------------- #


def test_dds_is_never_complete_even_when_all_low():
    dds = _build_simple_dds(RiskTier.LOW)
    assert dds.compliance_complete is False
    assert dds.legality_status == LegalityStatus.NOT_ASSESSED


def test_dds_is_never_complete_for_any_tier():
    for tier in (RiskTier.LOW, RiskTier.MORE_INFO_NEEDED, RiskTier.HIGH):
        dds = _build_simple_dds(tier)
        assert dds.compliance_complete is False
        assert dds.legality_status == LegalityStatus.NOT_ASSESSED
        assert dds.deforestation_determination == tier


# --------------------------------------------------------------------------- #
# Due diligence path / regime (VN -> simplified_dd, not conflated)
# --------------------------------------------------------------------------- #


def test_due_diligence_path_is_simplified_for_vn():
    dds = _build_simple_dds()
    # use_enum_values on the model serializes enums to their .value strings.
    assert dds.due_diligence_path == DueDiligencePath.SIMPLIFIED_DD.value
    assert dds.country_risk_class == "low"


def test_three_due_diligence_paths_are_distinct():
    values = {
        DueDiligencePath.FULL_DD.value,
        DueDiligencePath.SIMPLIFIED_DD.value,
        DueDiligencePath.MICRO_SMALL_PRIMARY_ONE_TIME.value,
    }
    assert len(values) == 3
    # simplified_dd is NOT the one-time micro/small path.
    assert (
        DueDiligencePath.SIMPLIFIED_DD.value != DueDiligencePath.MICRO_SMALL_PRIMARY_ONE_TIME.value
    )


def test_due_diligence_regime_does_not_claim_completeness():
    dds = _build_simple_dds()
    assert dds.due_diligence_regime
    # The regime names the Art. 13 simplified path, not a clean verdict.
    assert "simplified" in dds.due_diligence_regime.lower()


# --------------------------------------------------------------------------- #
# Validity window (Art. 12)
# --------------------------------------------------------------------------- #


def test_validity_window_is_365_days():
    dds = _build_simple_dds()
    valid_for_days = int(load_policy()["dds_validity"]["valid_for_days"])
    assert valid_for_days == 365
    assert dds.valid_until == dds.valid_from + timedelta(days=365)
    assert dds.annual_review_required is True


# --------------------------------------------------------------------------- #
# Provenance stamps
# --------------------------------------------------------------------------- #


def test_dds_carries_provenance_stamps():
    dds = _build_simple_dds()
    assert dds.geojson_spec_version == "1.5"
    assert dds.policy_version == policy_version()
    assert dds.deforestation_cutoff_date == EUDR_DEFORESTATION_CUTOFF
    # The cutoff and the application date are DISTINCT (tripwire J).
    assert dds.deforestation_cutoff_date != dds.regulation_application_date
    assert dds.deforestation_cutoff_date == date(2020, 12, 31)
    assert dds.regulation_application_date == date(2026, 12, 30)


def test_dds_reference_verification_pairing_holds():
    dds = _build_simple_dds()
    assert dds.reference_number and dds.verification_number
    assert verify_pairing(dds.reference_number, dds.verification_number) is True


def test_build_dds_rejects_nonconformant_geojson():
    # A doughnut (interior ring) is rejected by v1.5, so build_dds must raise
    # rather than submit a non-conformant payload.
    exterior = [
        (108.010000, 12.640000),
        (108.030000, 12.640000),
        (108.030000, 12.660000),
        (108.010000, 12.660000),
        (108.010000, 12.640000),
    ]
    interior = [
        (108.015000, 12.645000),
        (108.025000, 12.645000),
        (108.025000, 12.655000),
        (108.015000, 12.655000),
        (108.015000, 12.645000),
    ]
    doughnut = shapely.geometry.Polygon(exterior, [interior])
    raised = False
    try:
        build_dds("CONS-X", "Acme", [("plot-d", doughnut)], [_profile("plot-d", RiskTier.LOW)])
    except ValueError as exc:
        raised = True
        assert "v1.5" in str(exc)
    assert raised, "build_dds must raise on a non-conformant GeoJson payload"


# --------------------------------------------------------------------------- #
# TRACES stub
# --------------------------------------------------------------------------- #


def test_traces_stub_returns_pairing_and_makes_no_network_call(monkeypatch):
    # Guard: any socket connection attempt fails the test (proves no network).
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("TRACES stub must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", _no_network)

    dds = _build_simple_dds()
    response = TracesStubClient().submit(dds)

    assert response["reference_number"] == dds.reference_number
    assert response["verification_number"] == dds.verification_number
    assert response["pairing_valid"] is True
    assert response["compliance_complete"] is False
    assert "stub" in response["transport"].lower()


# --------------------------------------------------------------------------- #
# Composition: real plots -> assess_plot -> build_dds -> withheld DDS
# --------------------------------------------------------------------------- #


def test_composition_real_plots_yield_a_withheld_dds():
    """Take fixture coffee points, run the deforestation engine to get real
    RiskProfiles, build a DDS, and assert it is WITHHELD with the correct
    stamps."""
    fc = json.loads(POINTS.read_text(encoding="utf-8"))
    features = fc["features"][:3]
    provider = RasterProvider()

    plots = []
    profiles = []
    for feature in features:
        plot_id = feature["id"]
        geom = shape(feature["geometry"])
        profile = assess_plot(geom, plot_id, "compose-1", provider)
        plots.append((plot_id, geom))
        profiles.append(profile)

    dds = build_dds("CONS-COMPOSE", "Acme Coffee Co", plots, profiles)

    # Withheld: never a complete conformity finding.
    assert dds.compliance_complete is False
    assert dds.legality_status == LegalityStatus.NOT_ASSESSED

    # Determination is the consignment roll-up of the per-plot tiers.
    expected = max(
        (p.risk for p in profiles),
        key=lambda t: {RiskTier.LOW: 0, RiskTier.MORE_INFO_NEEDED: 1, RiskTier.HIGH: 2}[t],
    )
    assert dds.deforestation_determination == expected

    # Stamps: spec version, policy version, cutoff vs application distinct.
    assert dds.geojson_spec_version == "1.5"
    assert dds.policy_version == policy_version()
    assert dds.deforestation_cutoff_date == date(2020, 12, 31)
    assert dds.regulation_application_date == date(2026, 12, 30)
    assert dds.deforestation_cutoff_date != dds.regulation_application_date

    # Plot ids carried through; geojson is conformant (build_dds would have raised).
    assert dds.plot_ids == [f["id"] for f in features]
    assert dds.geojson["type"] == "FeatureCollection"
    assert len(dds.geojson["features"]) == 3
