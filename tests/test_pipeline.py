"""Tests for the orchestrator (``veritas_eudr.pipeline``).

Two layers:
- Pure-python (no DB): ``process_features`` over the messy-submission fixture --
  every feature gets a ``ValidationReport``; features with a usable geometry get
  an ``AreaMeasurement`` and a ``RiskProfile`` whose tier is sane; raw-WKT
  pathologies get a report but no area/risk.
- Persistence (postgis-marked; skips without a DB): ``run_pipeline`` end to end --
  plots + plot_results + evidence_ledger rows are written, a withheld DDS is
  returned, and a re-run is idempotent (no duplicate plots).
"""

from __future__ import annotations

import pytest

from veritas_eudr.domain import AreaMeasurement, RiskProfile, RiskTier, ValidationReport
from veritas_eudr.ingest import parse_submission
from veritas_eudr.pipeline import PlotOutcome, process_features, run_pipeline

_VALID_TIERS = {RiskTier.LOW, RiskTier.MORE_INFO_NEEDED, RiskTier.HIGH}


@pytest.fixture()
def geojson_path(fixtures_dir):
    return fixtures_dir / "submissions" / "messy_submission.geojson"


@pytest.fixture()
def features(geojson_path):
    return parse_submission(geojson_path)


# --------------------------------------------------------------------------- #
# process_features (pure)
# --------------------------------------------------------------------------- #


def test_process_features_returns_one_outcome_per_feature(features):
    outcomes = process_features(features, run_id="test-run")
    assert len(outcomes) == len(features)
    assert all(isinstance(o, PlotOutcome) for o in outcomes)


def test_every_feature_gets_a_validation_report(features):
    outcomes = process_features(features, run_id="test-run")
    for outcome in outcomes:
        assert isinstance(outcome.validation, ValidationReport)


def test_features_with_geometry_get_area_and_risk(features):
    outcomes = process_features(features, run_id="test-run")
    by_id = {o.plot_id: o for o in outcomes}

    # The clean polygon has a usable geometry -> area + risk present, tier sane.
    clean = by_id["sub-clean-poly"]
    assert isinstance(clean.area, AreaMeasurement)
    assert isinstance(clean.risk, RiskProfile)
    assert clean.risk.risk in _VALID_TIERS


def test_sampleable_geometry_features_have_sane_risk_tier(features):
    """Every feature that carries a usable, in-AOI geometry is risk-assessed with
    a sane tier; only the un-locatable ones are left without a profile."""
    outcomes = process_features(features, run_id="test-run")
    assessed = [o for o in outcomes if o.risk is not None]
    assert assessed, "at least one feature must be risk-assessed"
    for outcome in assessed:
        assert outcome.risk.risk in _VALID_TIERS
        assert outcome.area is not None


def test_out_of_aoi_swap_point_is_recorded_but_not_risk_assessed(features):
    """The lat/lon-swapped point lands outside the AOI rasters: it cannot be
    sampled, so it gets a validation report (NEEDS_REVIEW) but no area/risk --
    the honest outcome rather than a fabricated tier on a wrong location."""
    outcomes = process_features(features, run_id="test-run")
    swap = next(o for o in outcomes if o.plot_id == "sub-latlon-swap")
    assert swap.validation.needs_review
    assert swap.area is None
    assert swap.risk is None


def test_raw_wkt_pathology_is_validated_but_not_risk_assessed(features):
    """The bowtie is carried as raw WKT (no canonical geometry): it must be
    validated (so its findings surface) but cannot be area/risk-assessed."""
    outcomes = process_features(features, run_id="test-run")
    bowtie = next(o for o in outcomes if o.plot_id == "sub-bowtie")
    assert isinstance(bowtie.validation, ValidationReport)
    assert bowtie.area is None
    assert bowtie.risk is None
    # The self-intersection is surfaced as a finding, not silently dropped.
    assert bowtie.validation.findings


def test_clean_polygon_validation_is_auto_valid(features):
    outcomes = process_features(features, run_id="test-run")
    clean = next(o for o in outcomes if o.plot_id == "sub-clean-poly")
    assert clean.validation.disposition.value == "AUTO_VALID"


def test_over_4ha_point_is_flagged_needs_review(features):
    """The format rule (point + asserted area > 4 ha) must fire: this proves the
    asserted area is plumbed from the CanonicalFeature into validate_plot."""
    outcomes = process_features(features, run_id="test-run")
    bigpoint = next(o for o in outcomes if o.plot_id == "sub-bigpoint")
    assert bigpoint.validation.needs_review
    assert any(f.rule_id == "geometry_type_vs_asserted_area" for f in bigpoint.validation.findings)


def test_process_features_is_deterministic(features):
    a = process_features(features, run_id="test-run")
    b = process_features(features, run_id="test-run")
    assert [o.plot_id for o in a] == [o.plot_id for o in b]
    assert [o.risk.risk if o.risk else None for o in a] == [
        o.risk.risk if o.risk else None for o in b
    ]


# --------------------------------------------------------------------------- #
# run_pipeline (postgis-marked; skips without a DB)
# --------------------------------------------------------------------------- #


@pytest.mark.postgis
def test_run_pipeline_persists_results_and_returns_withheld_dds(db_session, geojson_path):
    from veritas_eudr.db import EvidenceLedger, Plot, PlotResult

    result = run_pipeline(
        geojson_path,
        operator_name="Acme Coffee Co",
        consignment_id="CONS-PIPE-1",
        session=db_session,
    )
    db_session.flush()

    # Plots persisted; one PlotResult per persisted plot.
    n_plots = db_session.query(Plot).filter(Plot.consignment_id == "CONS-PIPE-1").count()
    assert n_plots == result["n_plots"] > 0

    n_results = db_session.query(PlotResult).filter(PlotResult.run_id == result["run_id"]).count()
    assert n_results == n_plots

    # Three convergence layers per RISK-ASSESSED plot (an unsampleable plot --
    # e.g. an out-of-AOI point -- is recorded but carries no evidence rows).
    n_assessed = sum(1 for o in result["outcomes"] if o.risk is not None)
    assert n_assessed > 0
    n_evidence = (
        db_session.query(EvidenceLedger).filter(EvidenceLedger.run_id == result["run_id"]).count()
    )
    assert n_evidence == 3 * n_assessed

    # The DDS is always WITHHELD (legality NOT_ASSESSED; Art. 3 is conjunctive).
    dds = result["dds"]
    assert dds.compliance_complete is False


@pytest.mark.postgis
def test_run_pipeline_is_idempotent(db_session, geojson_path):
    from veritas_eudr.db import EvidenceLedger, Plot, PlotResult

    r1 = run_pipeline(
        geojson_path,
        operator_name="Acme Coffee Co",
        consignment_id="CONS-PIPE-2",
        session=db_session,
    )
    db_session.flush()
    plots_after_first = db_session.query(Plot).count()
    results_after_first = db_session.query(PlotResult).count()
    evidence_after_first = db_session.query(EvidenceLedger).count()

    r2 = run_pipeline(
        geojson_path,
        operator_name="Acme Coffee Co",
        consignment_id="CONS-PIPE-2",
        session=db_session,
    )
    db_session.flush()
    plots_after_second = db_session.query(Plot).count()
    results_after_second = db_session.query(PlotResult).count()
    evidence_after_second = db_session.query(EvidenceLedger).count()

    # Re-running the identical file adds no new plots and replays the same run_id.
    assert plots_after_second == plots_after_first
    assert r1["run_id"] == r2["run_id"]

    # And it appends no second set of plot_results / evidence_ledger rows: the
    # durable record is idempotent on the content-derived run_id.
    assert results_after_second == results_after_first
    assert evidence_after_second == evidence_after_first
