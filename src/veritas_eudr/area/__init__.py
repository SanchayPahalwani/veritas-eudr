"""Area + 4 ha submission-format determination.

Correctness contract (the expert tripwires this module exists to get right):

- ``ST_Area(geom::geography)`` (geodesic on the WGS84 spheroid) is AUTHORITATIVE.
  In-process we compute the SAME quantity with ``pyproj.Geod(ellps="WGS84")``
  (GeographicLib / Karney), which agrees with PostGIS geography area to ~1e-10
  relative -- so unit tests need no database, and an integration test asserts
  the two agree.
- EPSG:6933 (EASE-Grid 2.0 Global, equal area) is the CROSS-CHECK. We report the
  measured delta, never assert they are bit-identical.
- EPSG:3857 (Web Mercator) and planar EPSG:4326 are computed ONLY to demonstrate
  why they are wrong (negative tests). Web Mercator inflates area by ~sec^2(lat)
  PLUS an ellipsoid term: at this AOI's latitude (~12.67 N) the MEASURED delta is
  ~+5.7%, not 0 -- this AOI is mid-latitude, never "near the equator". Planar
  4326 (degrees-as-metres) is non-physical at every latitude.
- The 4 ha threshold is the geolocation SUBMISSION-FORMAT boundary (EUDR
  Art. 9(1)(d)), not a compliance pass/fail: plots < 4 ha may submit a single
  point; plots >= 4 ha must submit a polygon. A measured area within a tolerance
  band of 4 ha is BORDERLINE -> escalate to NEEDS_REVIEW upstream, because the
  geography-vs-6933 disagreement could flip a point-only submission's validity.
"""

from __future__ import annotations

import math

from pyproj import Geod, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from veritas_eudr.config import Settings, get_settings
from veritas_eudr.domain import AreaMeasurement, RequiredGeometryFormat

_WGS84 = Geod(ellps="WGS84")
AREA_THRESHOLD_HA = 4.0  # EUDR Art. 9(1)(d) submission-format boundary

# Cache the pyproj transformers (always_xy => lon/lat order, matching GeoJSON).
_TF_6933 = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
_TF_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _project_area_m2(geom: BaseGeometry, transformer: Transformer) -> float:
    """Planar area (m^2) of ``geom`` after reprojection with ``transformer``."""
    projected = shapely_transform(lambda x, y, z=None: transformer.transform(x, y), geom)
    return abs(projected.area)


def geodesic_area_m2(geom: BaseGeometry) -> float:
    """Authoritative geodesic area on the WGS84 spheroid (m^2).

    Matches ``ST_Area(geom::geography)``. Sign depends on ring orientation, so we
    return the absolute value. Non-areal geometries (points/lines) return 0.0.
    """
    if geom.is_empty or geom.geom_type in ("Point", "MultiPoint", "LineString", "MultiLineString"):
        return 0.0
    area, _perimeter = _WGS84.geometry_area_perimeter(geom)
    return abs(area)


def geodesic_area_ha(geom: BaseGeometry) -> float:
    return geodesic_area_m2(geom) / 1e4


def local_utm_epsg(lon: float) -> int:
    """Local UTM zone for the AOI: 32648 (zone 48N) west of 108E, 32649 east.

    UTM is conformal; its area distortion is worst exactly where this AOI sits
    (the 108E zone boundary), so it is a shape-faithful *cross-reference*, never
    the area authority.
    """
    return 32649 if lon >= 108.0 else 32648


def required_format_for_area(area_ha: float) -> RequiredGeometryFormat:
    """< 4 ha -> a point is an acceptable submission; >= 4 ha -> polygon required."""
    return (
        RequiredGeometryFormat.POINT
        if area_ha < AREA_THRESHOLD_HA
        else RequiredGeometryFormat.POLYGON
    )


def is_borderline(area_ha: float, band_ha: float) -> bool:
    """True when the measured area is within ``band_ha`` of the 4 ha boundary."""
    return abs(area_ha - AREA_THRESHOLD_HA) <= band_ha


def measure(geom: BaseGeometry, settings: Settings | None = None) -> AreaMeasurement:
    """Full multi-basis area measurement + 4 ha format determination for one plot.

    The Web Mercator / local-UTM figures are included for transparency and for
    the negative tests; ``measured_area_ha`` (geodesic) is the only authority.
    """
    settings = settings or get_settings()

    geo_m2 = geodesic_area_m2(geom)
    ease_m2 = _project_area_m2(geom, _TF_6933) if geo_m2 > 0 else 0.0
    merc_m2 = _project_area_m2(geom, _TF_3857) if geo_m2 > 0 else 0.0

    geo_ha = geo_m2 / 1e4
    ease_ha = ease_m2 / 1e4
    merc_ha = merc_m2 / 1e4

    # Local UTM (shape-faithful cross-reference only).
    utm_ha: float | None = None
    if geo_m2 > 0:
        lon = geom.representative_point().x
        tf_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{local_utm_epsg(lon)}", always_xy=True)
        utm_ha = _project_area_m2(geom, tf_utm) / 1e4

    delta_6933 = 100.0 * (ease_ha - geo_ha) / geo_ha if geo_ha > 0 else 0.0
    delta_merc = 100.0 * (merc_ha - geo_ha) / geo_ha if geo_ha > 0 else None

    return AreaMeasurement(
        measured_area_ha=geo_ha,
        area_ha_ease6933=ease_ha,
        area_ha_local_utm=utm_ha,
        area_ha_webmercator=merc_ha if geo_ha > 0 else None,
        delta_6933_pct=delta_6933,
        delta_webmercator_pct=delta_merc,
        required_geometry_format=required_format_for_area(geo_ha),
        borderline=is_borderline(geo_ha, settings.area_borderline_band_ha),
    )


def webmercator_inflation_factor(lat_deg: float) -> float:
    """Naive *spherical* Web Mercator area scale = sec^2(lat). The real EPSG:3857
    delta is larger (spherical projection on ellipsoidal coords) -- we report the
    MEASURED delta from ``measure()``; this helper exists only to show the
    textbook lower bound in the negative-area test."""
    return 1.0 / (math.cos(math.radians(lat_deg)) ** 2)
