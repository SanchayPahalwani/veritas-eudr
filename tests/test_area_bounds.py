"""Area error-bound tests -- positive AND negative, at the AOI's REAL latitude.

These anchor the area authority. The reference value 12017.056 m^2 for a
1-milli-degree square at 12.67 N was measured against the pinned PostGIS image
(ST_Area(geography)); the in-process pyproj geodesic must reproduce it.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Point, Polygon

from veritas_eudr.area import (
    AREA_THRESHOLD_HA,
    geodesic_area_m2,
    measure,
    webmercator_inflation_factor,
)
from veritas_eudr.domain import RequiredGeometryFormat

# The exact spike polygon: a 0.001-degree square with SW corner at (108.0, 12.67).
SPIKE_SQUARE = Polygon(
    [(108.0, 12.67), (108.001, 12.67), (108.001, 12.671), (108.0, 12.671), (108.0, 12.67)]
)
SPIKE_AREA_M2 = 12017.056  # measured against PostGIS ST_Area(geography)


def _square_of_hectares(target_ha: float, lat: float = 12.67, lon0: float = 108.0) -> Polygon:
    """Build a near-square polygon whose geodesic area ~= target_ha at latitude lat."""
    side_m = math.sqrt(target_ha * 1e4)
    dlat = side_m / 110567.0  # ~m per degree latitude at this latitude
    dlon = side_m / (111320.0 * math.cos(math.radians(lat)))
    return Polygon(
        [(lon0, lat), (lon0 + dlon, lat), (lon0 + dlon, lat + dlat), (lon0, lat + dlat), (lon0, lat)]
    )


# --------------------------------------------------------------------------- #
# Positive: the authoritative geodesic area matches the measured PostGIS value
# --------------------------------------------------------------------------- #


def test_geodesic_area_matches_postgis_reference():
    got = geodesic_area_m2(SPIKE_SQUARE)
    assert got == pytest.approx(SPIKE_AREA_M2, abs=0.05)  # within 5 cm^2 of PostGIS


def test_ease6933_crosscheck_within_0_1_percent():
    m = measure(SPIKE_SQUARE)
    assert abs(m.delta_6933_pct) < 0.1  # equal-area cross-check agrees to <0.1%


# --------------------------------------------------------------------------- #
# Negative: the WRONG bases, demonstrated at the REAL latitude (not the equator)
# --------------------------------------------------------------------------- #


def test_webmercator_inflation_is_measurable_and_not_near_zero_at_aoi_latitude():
    """Web Mercator over-counts area at 12.67 N -- the silently-plausible trap.
    The MEASURED delta (~+5.7%) exceeds the textbook spherical sec^2(lat) (~+5.05%)
    because EPSG:3857 is a spherical projection applied to ellipsoidal coords."""
    m = measure(SPIKE_SQUARE)
    assert m.delta_webmercator_pct is not None
    assert 5.0 < m.delta_webmercator_pct < 6.5  # NOT 0 (this AOI is not near the equator)
    spherical_lower_bound = 100.0 * (webmercator_inflation_factor(12.67) - 1.0)
    assert spherical_lower_bound == pytest.approx(5.05, abs=0.1)
    assert m.delta_webmercator_pct > spherical_lower_bound  # ellipsoid term on top


def test_webmercator_inflation_grows_with_latitude():
    south = measure(_square_of_hectares(1.0, lat=12.67))
    north = measure(_square_of_hectares(1.0, lat=13.75))
    assert north.delta_webmercator_pct > south.delta_webmercator_pct  # worse further from equator


def test_planar_4326_is_non_physical():
    """Treating degrees as metres yields square degrees -- physically meaningless.
    A 0.001-deg square is 1e-6 deg^2; multiplying by a nominal (111320 m/deg)^2
    over-counts by ~3% here and is *maximally* wrong at the equator, not minimally."""
    planar_deg2 = SPIKE_SQUARE.area
    assert planar_deg2 == pytest.approx(1e-6, rel=1e-6)
    naive_m2 = planar_deg2 * (111320.0**2)
    # The naive degrees-as-metres figure disagrees with the geodesic truth.
    assert abs(naive_m2 - SPIKE_AREA_M2) / SPIKE_AREA_M2 > 0.02


# --------------------------------------------------------------------------- #
# 4 ha submission-format boundary
# --------------------------------------------------------------------------- #


def test_small_plot_allows_point_format():
    m = measure(_square_of_hectares(1.0))
    assert m.required_geometry_format == RequiredGeometryFormat.POINT
    assert m.borderline is False


def test_large_plot_requires_polygon_format():
    m = measure(_square_of_hectares(10.0))
    assert m.required_geometry_format == RequiredGeometryFormat.POLYGON
    assert m.borderline is False


@pytest.mark.parametrize("target_ha", [3.95, 4.05])
def test_borderline_4ha_flagged(target_ha):
    m = measure(_square_of_hectares(target_ha))
    assert m.measured_area_ha == pytest.approx(target_ha, rel=0.02)
    assert m.borderline is True  # within the +/-0.10 ha tolerance band -> escalate upstream


def test_point_geometry_has_zero_area_and_point_format():
    m = measure(Point(108.02, 12.68))
    assert m.measured_area_ha == 0.0
    assert m.required_geometry_format == RequiredGeometryFormat.POINT
    assert m.area_ha_webmercator is None


def test_threshold_constant_is_four_hectares():
    assert AREA_THRESHOLD_HA == 4.0


# --------------------------------------------------------------------------- #
# Integration: the in-process geodesic area equals PostGIS fn_area_hectares
# --------------------------------------------------------------------------- #


@pytest.mark.postgis
@pytest.mark.parametrize(
    "wkt",
    [
        "POLYGON((108.0 12.67,108.001 12.67,108.001 12.671,108.0 12.671,108.0 12.67))",
        "POLYGON((108.02 13.75,108.022 13.75,108.022 13.752,108.02 13.752,108.02 13.75))",
    ],
)
def test_python_geodesic_matches_postgis_geography(db_session, wkt):
    from shapely import wkt as shapely_wkt
    from sqlalchemy import text

    row = db_session.execute(
        text("SELECT geography_ha, epsg6933_ha FROM fn_area_hectares(ST_GeomFromText(:w, 4326))"),
        {"w": wkt},
    ).one()
    pg_geography_ha = row[0]
    py_ha = geodesic_area_m2(shapely_wkt.loads(wkt)) / 1e4
    assert py_ha == pytest.approx(pg_geography_ha, rel=1e-6)
