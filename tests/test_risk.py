"""Tests for the risk module's building blocks: consignment roll-up, the EUDR
GeoJson File Description v1.5 conformance pass, and the reference/verification
pairing chain.

All unit tests; no database, no network.
"""

from __future__ import annotations

import json

import shapely
from shapely.geometry import Polygon

from veritas_eudr.domain import LegalityStatus, RiskTier
from veritas_eudr.risk import (
    COORD_DECIMALS,
    build_eudr_geojson,
    build_legality_assessment,
    consignment_risk,
    make_reference_number,
    make_verification_number,
    validate_eudr_geojson,
    verify_pairing,
)


def _profile(plot_id: str, risk: RiskTier):
    """A minimal RiskProfile-shaped object for roll-up tests.

    consignment_risk only reads ``.risk``, so a tiny stand-in avoids running the
    full deforestation engine (which the composition test exercises separately).
    """

    class _P:
        def __init__(self, plot_id: str, risk: RiskTier) -> None:
            self.plot_id = plot_id
            self.risk = risk

    return _P(plot_id, risk)


# --------------------------------------------------------------------------- #
# Legality is never assessed
# --------------------------------------------------------------------------- #


def test_legality_always_not_assessed():
    legality = build_legality_assessment()
    assert legality.status == LegalityStatus.NOT_ASSESSED
    assert len(legality.categories) == 8
    assert all(v == "NOT_ASSESSED" for v in legality.categories.values())


# --------------------------------------------------------------------------- #
# consignment_risk ordering: HIGH > MORE_INFO_NEEDED > LOW
# --------------------------------------------------------------------------- #


def test_consignment_risk_high_dominates():
    profiles = [
        _profile("a", RiskTier.LOW),
        _profile("b", RiskTier.HIGH),
        _profile("c", RiskTier.MORE_INFO_NEEDED),
    ]
    assert consignment_risk(profiles) == RiskTier.HIGH


def test_consignment_risk_more_info_dominates_low():
    profiles = [_profile("a", RiskTier.LOW), _profile("b", RiskTier.MORE_INFO_NEEDED)]
    assert consignment_risk(profiles) == RiskTier.MORE_INFO_NEEDED


def test_consignment_risk_all_low():
    profiles = [_profile("a", RiskTier.LOW), _profile("b", RiskTier.LOW)]
    assert consignment_risk(profiles) == RiskTier.LOW


def test_consignment_risk_empty_is_low():
    assert consignment_risk([]) == RiskTier.LOW


# --------------------------------------------------------------------------- #
# GeoJson v1.5 conformance
# --------------------------------------------------------------------------- #


def _clean_square() -> Polygon:
    # A simple closed square inside the AOI.
    return Polygon(
        [
            (108.010000, 12.640000),
            (108.020000, 12.640000),
            (108.020000, 12.650000),
            (108.010000, 12.650000),
            (108.010000, 12.640000),
        ]
    )


def test_clean_polygon_passes():
    fc = build_eudr_geojson([("plot-clean", _clean_square())])
    assert validate_eudr_geojson(fc) == []


def test_clean_polygon_ring_is_closed_and_min_pairs():
    fc = build_eudr_geojson([("plot-clean", _clean_square())])
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]  # first == last
    assert len(ring) >= 4


def test_coordinates_rounded_to_exactly_six_decimals():
    # Coordinates with > 6 decimals must be rounded to exactly 6 on emit.
    poly = Polygon(
        [
            (108.0123456789, 12.6400000001),
            (108.0200000000, 12.6400000000),
            (108.0200000000, 12.6500000000),
            (108.0123456789, 12.6500000001),
            (108.0123456789, 12.6400000001),
        ]
    )
    fc = build_eudr_geojson([("plot-precise", poly)])
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    for lon, lat in ring:
        assert round(lon, COORD_DECIMALS) == lon
        assert round(lat, COORD_DECIMALS) == lat
    # The emitted first vertex is the 6-decimal rounding of the input.
    assert ring[0] == [round(108.0123456789, 6), round(12.6400000001, 6)]
    assert validate_eudr_geojson(fc) == []


def test_interior_ring_is_rejected():
    # A polygon WITH an interior ring (a doughnut) must be REJECTED, not silently
    # reduced to its outer ring.
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
    fc = build_eudr_geojson([("plot-doughnut", Polygon(exterior, [interior]))])
    issues = validate_eudr_geojson(fc)
    assert issues, "expected the doughnut to be flagged"
    joined = " ".join(issues).lower()
    assert "interior ring" in joined
    assert "rejected" in joined or "not processed" in joined


def test_self_crossing_ring_is_rejected():
    # A bowtie / figure-eight ring (not simple) must be REJECTED.
    bowtie = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [108.010000, 12.640000],
                            [108.020000, 12.650000],
                            [108.020000, 12.640000],
                            [108.010000, 12.650000],
                            [108.010000, 12.640000],
                        ]
                    ],
                },
            }
        ],
    }
    issues = validate_eudr_geojson(bowtie)
    joined = " ".join(issues).lower()
    assert "self-crossing" in joined
    assert "rejected" in joined or "not processed" in joined


def test_unclosed_ring_is_flagged():
    unclosed = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [108.010000, 12.640000],
                            [108.020000, 12.640000],
                            [108.020000, 12.650000],
                            [108.010000, 12.650000],
                        ]
                    ],
                },
            }
        ],
    }
    issues = validate_eudr_geojson(unclosed)
    assert any("not closed" in i.lower() for i in issues)


def test_too_few_pairs_is_flagged():
    triangle_unclosed = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [108.010000, 12.640000],
                            [108.020000, 12.640000],
                            [108.010000, 12.640000],
                        ]
                    ],
                },
            }
        ],
    }
    issues = validate_eudr_geojson(triangle_unclosed)
    assert any("pairs" in i.lower() for i in issues)


def test_excess_precision_coordinates_flagged():
    too_precise = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [108.0100001, 12.6400001],
                            [108.0200000, 12.6400000],
                            [108.0200000, 12.6500000],
                            [108.0100001, 12.6400001],
                        ]
                    ],
                },
            }
        ],
    }
    issues = validate_eudr_geojson(too_precise)
    assert any("decimal" in i.lower() for i in issues)


def test_feature_collection_is_wgs84_lon_lat_order():
    # The emitted coordinates are [lon, lat]: lon ~108 (>90), lat ~12 (<90).
    fc = build_eudr_geojson([("plot-clean", _clean_square())])
    assert fc["type"] == "FeatureCollection"
    lon, lat = fc["features"][0]["geometry"]["coordinates"][0][0]
    assert 107.0 < lon < 109.0
    assert 12.0 < lat < 14.0


def test_point_feature_passes():
    pt = shapely.geometry.Point(108.0053090001, 12.6446370001)
    fc = build_eudr_geojson([("pt-000", pt)])
    assert validate_eudr_geojson(fc) == []
    coords = fc["features"][0]["geometry"]["coordinates"]
    assert coords == [round(108.0053090001, 6), round(12.6446370001, 6)]


# --------------------------------------------------------------------------- #
# Reference / verification pairing
# --------------------------------------------------------------------------- #


def test_verify_pairing_true_for_matching_reference():
    ref = make_reference_number("CONS-1")
    assert verify_pairing(ref, make_verification_number(ref)) is True


def test_verify_pairing_false_for_mismatched_reference():
    ref_a = make_reference_number("CONS-A")
    ref_b = make_reference_number("CONS-B")
    # A verification number derived from refB must NOT verify refA: the stub
    # validates the PAIRING, not mere presence of any token.
    assert verify_pairing(ref_a, make_verification_number(ref_b)) is False


def test_verify_pairing_rejects_empty_tokens():
    ref = make_reference_number("CONS-1")
    assert verify_pairing(ref, "") is False
    assert verify_pairing("", make_verification_number(ref)) is False


def test_reference_numbers_are_distinct_per_call():
    assert make_reference_number("CONS-1") != make_reference_number("CONS-1")


def test_geojson_is_json_serializable():
    fc = build_eudr_geojson([("plot-clean", _clean_square())])
    # The payload must serialize cleanly (it is hashed/submitted downstream) and
    # round-trip back to the expected FeatureCollection shape.
    assert json.loads(json.dumps(fc))["type"] == "FeatureCollection"
