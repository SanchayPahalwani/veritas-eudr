"""Tests for the validate module.

Two layers:
- DATA-DRIVEN: every fixture feature that carries a geometry-level
  ``expected_disposition`` is fed through ``validate_plot`` and its rolled-up
  disposition is asserted against the manifest. The manifest is the binding
  contract -- if a case here disagrees, the code is wrong, not the manifest.
- EXPLICIT one-rule-per-test cases, each with a one-line rationale docstring,
  pinning the judgment about what NOT to auto-fix.

Two manifest features are deliberately NOT asserted through ``validate_plot``
because their NEEDS_REVIEW disposition is owned by a different module, not by
single-plot geometry validation:
- ``sub-subpixel-zf``: a clean valid 1.48 ha polygon; its more-info-needed
  outcome is a deforestation/risk-axis verdict (sub-pixel loss below the
  coverage threshold), not a geometry pathology.
- ``sub-dup-id``: a clean valid polygon; its NEEDS_REVIEW is an identity
  collision visible only across the collection -- exercised via the
  collection-level duplicate-id check, not ``validate_plot``.
"""

from __future__ import annotations

import json

import pytest
from shapely import wkt as shp_wkt
from shapely.geometry import Point, Polygon, shape

from veritas_eudr.config import PROJECT_ROOT
from veritas_eudr.domain import Disposition, Severity
from veritas_eudr.validate import (
    detect_duplicate_ids,
    validate_overlaps,
    validate_plot,
)

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
PATHOLOGY = json.loads((FIXTURES / "pathology" / "pathology.json").read_text())
MESSY = json.loads((FIXTURES / "submissions" / "messy_submission.geojson").read_text())

# Manifest features whose disposition is NOT a single-plot geometry concern.
_NON_GEOMETRY_DISPOSITION = {"sub-subpixel-zf", "sub-dup-id"}


def _geom_or_wkt(feature: dict):
    """A manifest/fixture feature may carry a GeoJSON ``geometry`` or a ``wkt``
    string (the pathology / non-GeoJSON-valid convention)."""
    if feature.get("wkt"):
        return feature["wkt"]
    return shape(feature["geometry"])


def _properties_for(feature: dict) -> dict:
    """Surface contextual properties (asserted area, declared CRS) the rules read."""
    props: dict = {}
    if "asserted_area_ha" in feature:
        props["asserted_area_ha"] = feature["asserted_area_ha"]
    return props


# --------------------------------------------------------------------------- #
# DATA-DRIVEN: manifest is the contract
# --------------------------------------------------------------------------- #

_PATHOLOGY_CASES = [
    pytest.param(f, id=f["id"])
    for f in MANIFEST["features"]["pathology"]
    if f.get("expected_disposition")
]
_SUBMISSION_CASES = [
    pytest.param(f, id=f["id"])
    for f in MANIFEST["features"]["submission"]
    if f.get("expected_disposition") and f["id"] not in _NON_GEOMETRY_DISPOSITION
]


@pytest.mark.parametrize("feature", _PATHOLOGY_CASES)
def test_pathology_disposition_matches_manifest(feature):
    """Every WKT pathology must roll up to the manifest's expected disposition."""
    report = validate_plot(_geom_or_wkt(feature), properties=_properties_for(feature))
    assert report.disposition == Disposition(feature["expected_disposition"]), (
        f"{feature['id']} ({feature['scenario']}): "
        f"got {report.disposition}, findings="
        f"{[(f.rule_id, f.disposition) for f in report.findings]}"
    )


@pytest.mark.parametrize("feature", _SUBMISSION_CASES)
def test_submission_disposition_matches_manifest(feature):
    """Every messy-submission geometry case must match the manifest contract."""
    report = validate_plot(_geom_or_wkt(feature), properties=_properties_for(feature))
    assert report.disposition == Disposition(feature["expected_disposition"]), (
        f"{feature['id']} ({feature['scenario']}): "
        f"got {report.disposition}, findings="
        f"{[(f.rule_id, f.disposition) for f in report.findings]}"
    )


def test_every_pathology_feature_is_covered():
    """Guard: the pathology set must be fully exercised (no silent gaps)."""
    covered = {p.id for p in _PATHOLOGY_CASES}
    declared = {f["id"] for f in MANIFEST["features"]["pathology"] if f.get("expected_disposition")}
    assert covered == declared


# --------------------------------------------------------------------------- #
# EXPLICIT one-rule-per-test cases (with rationale docstrings)
# --------------------------------------------------------------------------- #


def test_under_4ha_point_is_auto_valid():
    """A <=4 ha plot may be a single point (Art. 9(1)(d)) -> AUTO_VALID."""
    report = validate_plot(Point(108.02, 12.69), properties={"asserted_area_ha": 1.8})
    assert report.disposition == Disposition.AUTO_VALID


def test_over_4ha_asserted_as_point_is_needs_review():
    """A point asserting >4 ha cannot prove the <=4 ha point format -> NEEDS_REVIEW."""
    report = validate_plot(Point(108.03, 12.685), properties={"asserted_area_ha": 5.2})
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "geometry_type_vs_asserted_area" for f in report.findings)


def test_bare_point_without_area_is_auto_valid():
    """A bare point with no contextual area is a valid <4 ha submission -> AUTO_VALID."""
    report = validate_plot(Point(108.02, 12.69))
    assert report.disposition == Disposition.AUTO_VALID


def test_bowtie_fragments_and_needs_review():
    """Self-intersecting bowtie fragments Polygon->MultiPolygon on repair, so the
    area-stability gate (tripwire F) blocks the auto-fix -> NEEDS_REVIEW."""
    wkt = (
        "POLYGON((108.015400 12.645400, 108.016600 12.646600, "
        "108.016600 12.645400, 108.015400 12.646600, 108.015400 12.645400))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.NEEDS_REVIEW
    finding = next(f for f in report.findings if f.rule_id == "invalid_geometry")
    assert finding.disposition == Disposition.NEEDS_REVIEW
    assert "area_before_ha" in finding.details and "area_after_ha" in finding.details


def test_zero_area_spur_repairs_area_stable_and_auto_fixes():
    """A zero-area self-touching spur is removed by make_valid with geodesic area
    unchanged within epsilon -> AUTO_FIXED (the safe counterexample to the bowtie)."""
    wkt = (
        "POLYGON((108.0300000 12.6700000, 108.0310000 12.6700000, "
        "108.0310000 12.6710000, 108.0305000 12.6710000, 108.0305000 12.6710010, "
        "108.0305000 12.6710000, 108.0300000 12.6710000, 108.0300000 12.6700000))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.AUTO_FIXED
    assert report.repaired_geometry_wkt is not None


def test_unclosed_ring_auto_closes():
    """An unclosed ring (first != last) is auto-closed -- area-preserving -> AUTO_FIXED."""
    wkt = (
        "POLYGON((108.030000 12.670000, 108.030800 12.670000, "
        "108.030800 12.670800, 108.030000 12.670800))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.AUTO_FIXED
    assert any(f.rule_id == "unclosed_ring" for f in report.findings)
    assert report.repaired_geometry_wkt is not None


def test_clean_closed_multipolygon_wkt_is_auto_valid():
    """Regression: a clean, closed MULTIPOLYGON submitted as a WKT string must NOT
    trigger the unclosed_ring rule.  The POLYGON substring-check previously matched
    MULTIPOLYGON, mis-parsed the first ring, and falsely flagged AUTO_FIXED.
    The correct result is AUTO_VALID with zero findings."""
    wkt = (
        "MULTIPOLYGON(((108.030 12.670,108.031 12.670,"
        "108.031 12.671,108.030 12.671,108.030 12.670)))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.AUTO_VALID, (
        f"expected AUTO_VALID, got {report.disposition}; "
        f"findings={[(f.rule_id, f.disposition) for f in report.findings]}"
    )
    assert not any(
        f.rule_id == "unclosed_ring" for f in report.findings
    ), "unclosed_ring must not fire for a clean closed MULTIPOLYGON WKT string"


def test_duplicate_vertices_auto_fix():
    """Consecutive duplicate vertices are dropped -- geometry unchanged -> AUTO_FIXED."""
    wkt = (
        "POLYGON((108.030000 12.670000, 108.030000 12.670000, "
        "108.030800 12.670000, 108.030800 12.670800, 108.030000 12.670800, "
        "108.030000 12.670000))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.AUTO_FIXED


def test_holes_interior_ring_needs_review():
    """EUDR GeoJson File Description v1.5 rejects interior rings; dropping a hole
    changes the plot boundary so it is not auto-fixable -> NEEDS_REVIEW (tripwire H,
    manifest: NEEDS_REVIEW).  Documented fix is to split into two polygons."""
    feature = next(f for f in MANIFEST["features"]["submission"] if f["id"] == "sub-hole-poly")
    geom = shape(feature["geometry"])
    report = validate_plot(geom)
    assert report.disposition == Disposition.NEEDS_REVIEW
    hole = next(f for f in report.findings if f.rule_id == "holes")
    assert hole.severity == Severity.WARNING
    assert hole.disposition == Disposition.NEEDS_REVIEW
    assert (
        "interior ring" in hole.human_reason.lower()
        or "interior rings" in hole.human_reason.lower()
    )
    assert "split" in hole.human_reason.lower()


def test_lat_lon_swap_point_needs_review():
    """[lat, lon] spreadsheet export (lat magnitude > 90) is never blind-swapped
    -> NEEDS_REVIEW with the offending coordinate recorded."""
    report = validate_plot(Point(12.646, 108.046))
    assert report.disposition == Disposition.NEEDS_REVIEW
    finding = next(f for f in report.findings if f.rule_id == "lat_lon_swap")
    assert finding.failing_coordinate == [12.646, 108.046]


def test_lat_lon_swap_only_valid_when_swapped():
    """A coordinate that lands in the AOI only after swapping lon/lat is a likely
    axis-order error -> NEEDS_REVIEW (never auto-swapped)."""
    # Stored [lon=12.65, lat=108.0] is invalid lat; the swapped form is in-AOI.
    report = validate_plot(shp_wkt.loads("POINT(12.670000 108.030000)"))
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "lat_lon_swap" for f in report.findings)


def test_null_island_origin():
    """(0,0) null island -> NEEDS_REVIEW."""
    report = validate_plot(Point(0.0, 0.0))
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "null_island" for f in report.findings)


def test_null_island_lon_zero():
    """(0, lat) -- a dropped longitude is still null-island, not just (0,0)."""
    report = validate_plot(Point(0.0, 12.65))
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "null_island" for f in report.findings)


def test_null_island_lat_zero():
    """(lon, 0) -- a dropped latitude is still null-island."""
    report = validate_plot(Point(108.03, 0.0))
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "null_island" for f in report.findings)


def test_grid_snapped_low_precision_is_informational():
    """Trailing-zero / <6-significant-decimal coordinates are FORMATTING, not bad
    precision (tripwire G) -> WARNING but AUTO_VALID (non-blocking)."""
    report = validate_plot(Point(108.0, 12.7))
    assert report.disposition == Disposition.AUTO_VALID
    finding = next(f for f in report.findings if f.rule_id == "grid_snapped_low_precision")
    assert finding.severity == Severity.WARNING
    assert finding.disposition == Disposition.AUTO_VALID


def test_unknown_crs_property_needs_review():
    """A declared non-EPSG:4326 / mixed CRS is never blind-reprojected -> NEEDS_REVIEW."""
    report = validate_plot(Point(108.03, 12.67), properties={"crs": "EPSG:3857"})
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "unknown_mixed_crs" for f in report.findings)


def test_web_mercator_metres_out_of_degree_range_needs_review():
    """Coordinates that are Web Mercator metres (out of degree range) -> NEEDS_REVIEW."""
    report = validate_plot(shp_wkt.loads("POINT(12027500.0 1419000.0)"))
    assert report.disposition == Disposition.NEEDS_REVIEW


def test_spike_vertex_needs_review():
    """A far-flung outlier (spike/antenna) vertex changes area materially and is not
    a safe auto-repair -> NEEDS_REVIEW."""
    wkt = (
        "POLYGON((108.030000 12.670000, 108.030800 12.670000, "
        "108.030800 12.670800, 108.530000 13.170000, 108.030000 12.670800, "
        "108.030000 12.670000))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "spike_vertex" for f in report.findings)


def test_sliver_needs_review():
    """A degenerate near-zero-width sliver has unreliable area for the 4-ha test and
    zonal coverage -> NEEDS_REVIEW (aligns to manifest sub-sliver)."""
    wkt = "POLYGON((108.0252 12.646, 108.0268 12.646004, 108.0268 12.645996, 108.0252 12.646))"
    report = validate_plot(wkt)
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "sliver" for f in report.findings)


def test_clean_polygon_auto_valid():
    """A simple valid sub-4-ha polygon passes untouched -> AUTO_VALID."""
    poly = Polygon(
        [(108.0055, 12.6455), (108.0065, 12.6455), (108.0065, 12.6465), (108.0055, 12.6465)]
    )
    report = validate_plot(poly)
    assert report.disposition == Disposition.AUTO_VALID
    assert report.findings == []


def test_self_overlapping_multipolygon_unions_auto_fix():
    """Two overlapping digitized parcels are unioned by make_valid; the area change is
    the intended removal of double-counted overlap, not fragmentation -> AUTO_FIXED."""
    wkt = (
        "MULTIPOLYGON(((108.030000 12.670000, 108.030800 12.670000, "
        "108.030800 12.670800, 108.030000 12.670800, 108.030000 12.670000)), "
        "((108.030400 12.670400, 108.031200 12.670400, 108.031200 12.671200, "
        "108.030400 12.671200, 108.030400 12.670400)))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.AUTO_FIXED


def test_collapsed_zero_area_polygon_needs_review():
    """A collinear collapsed polygon has no usable area/coverage -> NEEDS_REVIEW."""
    wkt = (
        "POLYGON((108.030000 12.670000, 108.030800 12.670000, "
        "108.031600 12.670000, 108.030000 12.670000))"
    )
    report = validate_plot(wkt)
    assert report.disposition == Disposition.NEEDS_REVIEW


def test_out_of_aoi_negative_longitude_needs_review():
    """A wrong-hemisphere (negative-longitude) point lands outside Vietnam; the
    geocode must be confirmed, never auto-corrected -> NEEDS_REVIEW."""
    report = validate_plot(shp_wkt.loads("POINT(-108.030000 12.670000)"))
    assert report.disposition == Disposition.NEEDS_REVIEW
    assert any(f.rule_id == "out_of_aoi" for f in report.findings)


# --------------------------------------------------------------------------- #
# Collection-level: overlaps + duplicate id
# --------------------------------------------------------------------------- #


def test_inter_plot_overlap_flagged():
    """Two plots that overlap (ST_Overlaps semantics) yield a flagged pair."""
    a = {
        "id": "a",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [108.0, 12.66],
                    [108.002, 12.66],
                    [108.002, 12.662],
                    [108.0, 12.662],
                    [108.0, 12.66],
                ]
            ],
        },
    }
    b = {
        "id": "b",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [108.001, 12.661],
                    [108.003, 12.661],
                    [108.003, 12.663],
                    [108.001, 12.663],
                    [108.001, 12.661],
                ]
            ],
        },
    }
    findings = validate_overlaps([a, b])
    assert len(findings) >= 1
    assert all(f.rule_id == "inter_plot_overlap" for f in findings)
    assert findings[0].disposition == Disposition.NEEDS_REVIEW


def test_disjoint_plots_do_not_overlap():
    """Disjoint plots produce no overlap findings."""
    a = {
        "id": "a",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [108.0, 12.66],
                    [108.001, 12.66],
                    [108.001, 12.661],
                    [108.0, 12.661],
                    [108.0, 12.66],
                ]
            ],
        },
    }
    b = {
        "id": "b",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [108.01, 12.66],
                    [108.011, 12.66],
                    [108.011, 12.661],
                    [108.01, 12.661],
                    [108.01, 12.66],
                ]
            ],
        },
    }
    assert validate_overlaps([a, b]) == []


def test_duplicate_feature_id_flagged():
    """A reused feature id is an identity collision; flag it, never silently
    overwrite (manifest sub-dup-id -> NEEDS_REVIEW)."""
    findings = detect_duplicate_ids(MESSY["features"])
    dup_ids = {f.details.get("feature_id") for f in findings}
    assert "sub-clean-poly" in dup_ids
    assert all(f.disposition == Disposition.NEEDS_REVIEW for f in findings)


# --------------------------------------------------------------------------- #
# Optional PostGIS cross-check
# --------------------------------------------------------------------------- #


@pytest.mark.postgis
def test_validity_verdict_agrees_with_postgis():
    """validate_plot's validity verdict agrees with fn_validate_plot.is_valid."""
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import text

    from veritas_eudr.db import get_engine

    engine = get_engine()
    try:
        conn = engine.connect()
    except Exception:  # pragma: no cover - no DB available
        pytest.skip("PostGIS database not available")
    with conn:
        for wkt, expect_valid in [
            (
                "POLYGON((108.0055 12.6455,108.0065 12.6455,108.0065 12.6465,108.0055 12.6465,108.0055 12.6455))",
                True,
            ),
            (
                "POLYGON((108.015400 12.645400, 108.016600 12.646600, 108.016600 12.645400, 108.015400 12.646600, 108.015400 12.645400))",
                False,
            ),
        ]:
            row = conn.execute(
                text("SELECT is_valid FROM fn_validate_plot(ST_GeomFromText(:w, 4326))"),
                {"w": wkt},
            ).first()
            assert row is not None
            geom = shp_wkt.loads(wkt)
            assert geom.is_valid == expect_valid
            assert bool(row.is_valid) == expect_valid
