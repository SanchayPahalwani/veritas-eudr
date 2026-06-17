"""Tests for the convergence-of-evidence deforestation engine.

Data-driven over the six painted zones (and the AOI background) via the
machine-readable manifest's ``expected_risk_tier``. The tripwires (B band-21
latency, C ground-hectare sub-threshold, E context-not-commodity, L
JRC-over-maps) get explicit assertions on top of the zone sweep.

No database is needed: exactextract/rasterio/shapely run in-process over the
local synthetic AOI tiles.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import shapely
from shapely.geometry import shape

from veritas_eudr.config import get_settings
from veritas_eudr.deforestation import (
    BAND21_LATENCY,
    JRC_FOREST_VALUE,
    WORLDCOVER_CROPLAND,
    DeforestationProvider,
    RasterProvider,
    assess_plot,
)
from veritas_eudr.domain import RiskTier, SamplingStrategy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def provider() -> RasterProvider:
    return RasterProvider()


def _geom_of(feature: dict):
    """A shapely geometry from a manifest feature (GeoJSON geometry or WKT)."""
    if "geometry" in feature:
        return shape(feature["geometry"])
    return shapely.from_wkt(feature["wkt"])


def _expected_tier(value: str) -> RiskTier:
    return {
        "low": RiskTier.LOW,
        "high": RiskTier.HIGH,
        "more-info-needed": RiskTier.MORE_INFO_NEEDED,
    }[value]


# --------------------------------------------------------------------------- #
# Data-driven zone sweep over the manifest points
# --------------------------------------------------------------------------- #


def _point_cases() -> list[dict]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [f for f in data["features"]["points"] if f.get("expected_risk_tier") is not None]


@pytest.mark.parametrize("feature", _point_cases(), ids=lambda f: f["id"])
def test_point_risk_matches_manifest(feature, provider):
    """Every manifest point's verdict matches its expected_risk_tier."""
    expected = feature["expected_risk_tier"]
    geom = _geom_of(feature)
    profile = assess_plot(geom, feature["id"], "v1", provider)
    assert profile.risk == _expected_tier(expected), (
        f"{feature['id']} (zone {feature.get('zone')}): got {profile.risk}, "
        f"expected {expected}; rationale={profile.rationale}"
    )


def test_each_zone_has_a_representative_match(manifest, provider):
    """At least one representative feature per painted zone matches its tier --
    a guard that the zone-by-zone contract (Z_A..Z_F) is exercised."""
    seen: dict[str, str] = {}
    for f in manifest["features"]["points"]:
        zone = f["zone"]
        if zone in seen or f.get("expected_risk_tier") is None:
            continue
        profile = assess_plot(_geom_of(f), f["id"], "v1", provider)
        assert profile.risk == _expected_tier(
            f["expected_risk_tier"]
        ), f"{zone}/{f['id']}: {profile.risk} != {f['expected_risk_tier']}"
        seen[zone] = f["id"]
    for zone in ("Z_A", "Z_B", "Z_C", "Z_D", "Z_E", "Z_F"):
        assert zone in seen, f"no representative point asserted for {zone}"


# --------------------------------------------------------------------------- #
# Submission polygons (where expected_risk_tier is set)
# --------------------------------------------------------------------------- #


def test_submission_polygon_risk_matches_manifest(manifest, provider):
    for f in manifest["features"]["submission"]:
        expected = f.get("expected_risk_tier")
        if expected is None or "geometry" not in f:
            continue
        if f["geometry"]["type"] not in ("Polygon", "MultiPolygon", "Point"):
            continue
        geom = _geom_of(f)
        profile = assess_plot(geom, f["id"], "v1", provider)
        assert profile.risk == _expected_tier(expected), (
            f"{f['id']} (zone {f.get('zone')}): {profile.risk} != {expected}; "
            f"rationale={profile.rationale}"
        )


# --------------------------------------------------------------------------- #
# Tripwire B: band-21 latency dominates a would-be HIGH
# --------------------------------------------------------------------------- #


def test_tripwire_b_band21_latency(manifest, provider):
    """A Z_C plot whose only post-cutoff loss is band 21 (2021) -- even at high
    coverage -- must be MORE_INFO_NEEDED with boundary_uncertain=True, not HIGH."""
    z_c = next(f for f in manifest["features"]["points"] if f["zone"] == "Z_C")
    profile = assess_plot(_geom_of(z_c), z_c["id"], "v1", provider)

    assert profile.risk == RiskTier.MORE_INFO_NEEDED
    assert profile.boundary_uncertain is True

    hansen = next(a for a in profile.axes if a.layer == "lossyear")
    # Coverage is high (it would be HIGH but for the latency rule).
    assert hansen.covered_fraction is not None and hansen.covered_fraction >= 0.10
    # The reported dominant post-cutoff band is the latency band.
    assert hansen.value == float(BAND21_LATENCY)


def test_band21_dominates_even_when_coverage_above_threshold(provider):
    """Explicit construction: a fully-band-21 plot with coverage well above the
    threshold is still downgraded by the latency rule (tripwire B)."""
    # A polygon inside Z_C (band 21, JRC forest, tree context).
    geom = shapely.geometry.box(108.024, 12.644, 108.028, 12.648)
    profile = assess_plot(geom, "z_c_box", "v1", provider)
    hansen = next(a for a in profile.axes if a.layer == "lossyear")
    assert hansen.covered_fraction is not None and hansen.covered_fraction >= 0.5
    assert profile.risk == RiskTier.MORE_INFO_NEEDED
    assert profile.boundary_uncertain is True


# --------------------------------------------------------------------------- #
# Tripwire C: ground-hectare sub-threshold + ground != naive 30m count
# --------------------------------------------------------------------------- #


def test_tripwire_c_sub_threshold_is_more_info(manifest, provider):
    """The Z_F submission polygon straddles a single post-cutoff loss pixel
    (~6.25% ground coverage, below the 0.10 threshold) -> MORE_INFO_NEEDED, never
    HIGH on a bare intersection."""
    settings = get_settings()
    zf = next(f for f in manifest["features"]["submission"] if f["id"] == "sub-subpixel-zf")
    profile = assess_plot(_geom_of(zf), zf["id"], "v1", provider)

    assert profile.risk == RiskTier.MORE_INFO_NEEDED
    hansen = next(a for a in profile.axes if a.layer == "lossyear")
    assert hansen.covered_fraction is not None
    assert hansen.covered_fraction < settings.loss_coverage_threshold_frac
    # Matches the manifest's pre-computed expected coverage fraction.
    assert hansen.covered_fraction == pytest.approx(zf["expected_loss_coverage_frac"], abs=0.01)


def test_tripwire_c_ground_ha_differs_from_naive_degree_pixel(provider):
    """The ground-hectare conversion (geodesic per-cell area) must differ from a
    naive degree-pixel * 30 m x 30 m count -- the whole point of tripwire C."""
    # A Z_B box with substantial post-cutoff loss so the difference is measurable.
    geom = shapely.geometry.box(108.013, 12.644, 108.019, 12.649)
    samples = provider.sample_plot(geom)
    hansen = next(s for s in samples if s.layer == "lossyear")

    # The loss is recorded in true ground hectares (per-cell geodesic weight).
    assert hansen.covered_ha is not None and hansen.covered_ha > 0
    assert hansen.strategy == SamplingStrategy.FRACTIONAL_OVERLAP

    # Re-derive the summed loss coverage (in cell units) from the layer to compare
    # against a naive 30 m x 30 m nominal pixel area.
    from veritas_eudr.deforestation import _extract_cells

    cov, vals, _cx, _cy = _extract_cells(provider.hansen_path, geom)
    loss_mask = (vals >= 21) & (vals <= 25)
    naive_ha = float(cov[loss_mask].sum()) * (30.0 * 30.0 / 1e4)

    # Ground hectares and the naive count disagree (cells are non-square here),
    # and they disagree by a non-trivial amount.
    assert hansen.covered_ha != pytest.approx(naive_ha, abs=1e-6)
    assert abs(hansen.covered_ha - naive_ha) > 1e-3


# --------------------------------------------------------------------------- #
# Tripwire E / L: WorldCover is context, JRC-only is not enough for HIGH
# --------------------------------------------------------------------------- #


def test_tripwire_e_worldcover_is_context_only(provider):
    """The WorldCover layer is recorded as land-cover CONTEXT, never as a
    commodity layer; its note must say so."""
    geom = shapely.geometry.box(108.044, 12.644, 108.049, 12.648)  # Z_E cropland
    samples = provider.sample_plot(geom)
    wc = next(s for s in samples if s.layer == "landcover_context")
    assert wc.value == float(WORLDCOVER_CROPLAND)
    assert "context" in (wc.note or "").lower()
    assert "commodity" in (wc.note or "").lower()


def test_tripwire_l_inside_forest_alone_is_not_high(provider):
    """Z_D: inside the 2020 forest baseline with tree context but NO post-cutoff
    loss is MORE_INFO_NEEDED, not HIGH -- 'inside forest' alone is a weak signal
    for a coffee AOI where JRC over-maps shaded coffee."""
    geom = shapely.geometry.box(108.034, 12.644, 108.039, 12.648)  # Z_D intact forest
    profile = assess_plot(geom, "z_d_box", "v1", provider)
    jrc = next(a for a in profile.axes if a.layer == "forest_2020")
    assert jrc.value == float(JRC_FOREST_VALUE)  # genuinely inside forest
    assert profile.risk == RiskTier.MORE_INFO_NEEDED  # but not HIGH


# --------------------------------------------------------------------------- #
# Evidence + provenance + axes
# --------------------------------------------------------------------------- #


def test_profile_populates_axes_and_evidence(provider):
    geom = shapely.geometry.box(108.013, 12.644, 108.018, 12.648)  # Z_B
    profile = assess_plot(geom, "plot-x", "run-7", provider)

    layers = {a.layer for a in profile.axes}
    assert layers == {"lossyear", "forest_2020", "landcover_context"}

    # One evidence row per axis, each stamped with run/plot/dataset provenance.
    assert len(profile.evidence) == len(profile.axes)
    for rec in profile.evidence:
        assert rec.run_id == "run-7"
        assert rec.plot_id == "plot-x"
        assert rec.dataset_name and rec.dataset_version
        assert rec.verdict.startswith(profile.risk.value)

    assert profile.cutoff_date.isoformat() == "2020-12-31"


def test_datasets_carry_policy_versions(provider):
    """Dataset versions are sourced from the policy file (single source)."""
    geom = shapely.geometry.box(108.013, 12.644, 108.018, 12.648)
    samples = provider.sample_plot(geom)
    by_layer = {s.layer: s for s in samples}
    assert by_layer["lossyear"].dataset_version == "GFC-2025-v1.13"
    assert by_layer["forest_2020"].dataset_version == "V3"
    assert by_layer["landcover_context"].dataset_version == "v200"


def test_provider_is_pluggable_abc():
    """assess_plot accepts any DeforestationProvider; the ABC contract holds."""

    class StubProvider(DeforestationProvider):
        def __init__(self, samples):
            self._samples = samples

        def sample_plot(self, geom):
            return list(self._samples)

    from veritas_eudr.domain import LayerSample

    samples = [
        LayerSample(
            dataset_name="Hansen",
            dataset_version="x",
            layer="lossyear",
            strategy=SamplingStrategy.FRACTIONAL_OVERLAP,
            value=0.0,
            covered_fraction=0.0,
            covered_ha=0.0,
            details={
                "post_cutoff_bands": [],
                "pre_cutoff_bands": [],
                "band21_only": False,
                "loss_ground_ha": 0.0,
                "loss_fraction": 0.0,
            },
        ),
        LayerSample(
            dataset_name="JRC",
            dataset_version="x",
            layer="forest_2020",
            strategy=SamplingStrategy.ZONAL_MAJORITY,
            value=0.0,
            covered_fraction=0.0,
        ),
        LayerSample(
            dataset_name="WorldCover",
            dataset_version="x",
            layer="landcover_context",
            strategy=SamplingStrategy.ZONAL_MAJORITY,
            value=0.0,
            covered_fraction=0.0,
        ),
    ]
    profile = assess_plot(shapely.geometry.Point(108.03, 12.66), "p", "v1", StubProvider(samples))
    # Outside forest -> LOW.
    assert profile.risk == RiskTier.LOW
