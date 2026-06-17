"""Per-plot geometry validation and repair.

This module's value is the judgment about what NOT to auto-fix. A geometry the
system can safely, area-preservingly repair (close a ring, drop duplicate
vertices, remove a zero-area spur) is AUTO_FIXED. Anything where the "fix" would
silently change the plot's meaning -- a lat/lon swap, an unknown CRS, a spike, a
self-intersection that fragments the polygon -- is escalated to NEEDS_REVIEW so a
human decides. The system never blind-reprojects or blind-swaps a coordinate.

``validate_plot`` runs each rule (a small function returning ``Finding | None``)
and packs the results into a ``ValidationReport``; its ``disposition`` property
already rolls findings up to the worst one. The single most important invariant
is the area-stability gate (tripwire F): a repair is only ever AUTO_FIXED if the
geodesic area is unchanged within ``repair_area_epsilon_frac`` AND the repair did
not fragment a Polygon into a MultiPolygon.

Correctness pins:
- ``area.geodesic_area_ha`` (ST_Area(geography) basis) is the area authority for
  the stability gate; never planar/Web-Mercator area.
- ``make_valid(geom, method="structure")`` (shapely 2.1) is the repair op,
  matching PostGIS ST_MakeValid semantics.
- The 4 ha point/polygon boundary is a SUBMISSION FORMAT rule (Art. 9(1)(d)), not
  a compliance pass/fail; a bare <=4 ha point is a valid format.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from shapely import make_valid
from shapely import wkt as _wkt
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from veritas_eudr.area import (
    AREA_THRESHOLD_HA,
    geodesic_area_ha,
    required_format_for_area,
)
from veritas_eudr.config import Settings, get_settings
from veritas_eudr.domain import (
    Disposition,
    Finding,
    Position,
    RequiredGeometryFormat,
    Severity,
    ValidationReport,
)

# Geometry decision thresholds. These are validator heuristics (not regulatory
# constants), kept here so a reviewer can see and tune them.
#
# Polsby-Popper compactness (4*pi*A / P^2) below this is "degenerate thin"
# (sliver or spike). Healthy rectangles measure ~0.785, a donut ~0.47; slivers
# and spikes here measure <0.01, so 0.05 is a wide, safe separation.
_COMPACTNESS_MIN = 0.05
# A vertex whose distance from the polygon's median centre exceeds this multiple
# of the median vertex distance is an outlier "spike/antenna" vertex.
_SPIKE_OUTLIER_RATIO = 25.0
# Below this geodesic area (ha) a polygon is treated as degenerate for the 4 ha
# format test and zonal coverage (sub-pixel: << one ~0.09 ha Hansen pixel).
_SLIVER_AREA_HA = 0.02
# A coordinate is "grid-snapped / low precision" if its richest ordinate carries
# fewer than this many significant decimal places -- i.e. it has been snapped to a
# coarse grid (<=1 decimal ~ >=0.1 deg). This is a FORMATTING signal (tripwire G),
# informational only; 4-decimal (~11 m) field coordinates are NOT flagged.
_MIN_SIGNIFICANT_DECIMALS = 2


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def _as_geometry(geom: BaseGeometry | str) -> BaseGeometry:
    """Accept a shapely geometry or a WKT string (the pathology WKT-in-property
    convention). Unclosed-ring WKT is auto-closed before parsing because OGC WKT
    rejects an open ring, but the unclosed-ring rule still fires on the original."""
    if isinstance(geom, BaseGeometry):
        return geom
    try:
        return _wkt.loads(geom)
    except Exception:
        closed = _close_wkt_polygon_rings(geom)
        return _wkt.loads(closed)


def _close_wkt_polygon_rings(wkt_str: str) -> str:
    """Close any open POLYGON ring in a WKT string (first vertex appended last)."""
    head, _, body = wkt_str.partition("((")
    if not body or "POLYGON" not in head.upper():
        return wkt_str
    body = body.rstrip()
    if body.endswith("))"):
        body = body[:-2]
    rings = body.split(")")
    closed_rings = []
    for ring in rings:
        ring = ring.strip().lstrip("(").strip().rstrip(",").strip()
        if not ring:
            continue
        pts = [p.strip() for p in ring.split(",") if p.strip()]
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        closed_rings.append("(" + ", ".join(pts) + ")")
    return f"{head}({', '.join(closed_rings)})"


def _polygon_exterior_coords(geom: BaseGeometry) -> list[tuple[float, float]]:
    if isinstance(geom, Polygon):
        return [(x, y) for x, y, *_ in geom.exterior.coords]
    return []


def _all_coords(geom: BaseGeometry) -> list[tuple[float, float]]:
    """Flatten every (lon, lat) coordinate of any geometry."""
    gj = geom.__geo_interface__

    def walk(coords):
        out: list[tuple[float, float]] = []
        if not coords:
            return out
        if isinstance(coords[0], (int, float)):
            out.append((float(coords[0]), float(coords[1])))
        else:
            for c in coords:
                out.extend(walk(c))
        return out

    if gj["type"] == "GeometryCollection":  # pragma: no cover - not used by fixtures
        out: list[tuple[float, float]] = []
        for g in gj["geometries"]:
            out.extend(walk(g["coordinates"]))
        return out
    return walk(gj["coordinates"])


def _compactness(geom: BaseGeometry) -> float:
    """Polsby-Popper compactness on the planar ring (unitless ratio; the planar
    distortion cancels in the ratio at this AOI's scale)."""
    if not isinstance(geom, (Polygon, MultiPolygon)) or geom.length == 0:
        return 1.0
    return 4.0 * math.pi * geom.area / (geom.length**2)


def _significant_decimals(value: float) -> int:
    """Count significant decimal places, ignoring trailing zeros (so 12.700000 has
    one and 108.005309 has six)."""
    s = f"{value:.10f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1])


# --------------------------------------------------------------------------- #
# Rules (each returns Finding | None)
# --------------------------------------------------------------------------- #


def _rule_coordinate_range(geom: BaseGeometry) -> Finding | None:
    """Any |lat| > 90 or |lon| > 180 means the values are not 4326 degrees (often
    Web Mercator metres or a lat/lon swap) -> the CRS/order must be confirmed."""
    for lon, lat in _all_coords(geom):
        if abs(lat) > 90.0 or abs(lon) > 180.0:
            return Finding(
                rule_id="coordinate_out_of_range",
                severity=Severity.ERROR,
                disposition=Disposition.NEEDS_REVIEW,
                human_reason=(
                    "Coordinate is outside the EPSG:4326 degree range "
                    "(|lat|<=90, |lon|<=180); values are likely projected metres "
                    "or an axis swap. CRS/order must be confirmed, never assumed."
                ),
                failing_coordinate=[lon, lat],
            )
    return None


def _rule_lat_lon_swap(geom: BaseGeometry, settings: Settings) -> Finding | None:
    """A coordinate that is out of range as [lon, lat] but valid (and in-AOI) when
    swapped is the classic [lat, lon] spreadsheet-export bug. NEVER auto-swap --
    escalate so a human confirms the axis order."""
    for lon, lat in _all_coords(geom):
        in_range = abs(lat) <= 90.0 and abs(lon) <= 180.0
        out_of_range = abs(lat) > 90.0 and abs(lon) <= 90.0
        swapped_in_aoi = _in_aoi(lat, lon, settings) and not _in_aoi(lon, lat, settings)
        if out_of_range or (in_range and swapped_in_aoi):
            return Finding(
                rule_id="lat_lon_swap",
                severity=Severity.ERROR,
                disposition=Disposition.NEEDS_REVIEW,
                human_reason=(
                    "Coordinate order looks swapped ([lat, lon] instead of "
                    "[lon, lat]); this is the classic spreadsheet-export bug and is "
                    "not safely auto-correctable -- a human must confirm the order."
                ),
                failing_coordinate=[lon, lat],
            )
    return None


def _rule_null_island(geom: BaseGeometry) -> Finding | None:
    """A dropped/zeroed ordinate -- (0,0), (0,lat) or (lon,0) -- is null island, the
    fingerprint of a missing coordinate, never a real plot here."""
    for lon, lat in _all_coords(geom):
        if lon == 0.0 or lat == 0.0:
            return Finding(
                rule_id="null_island",
                severity=Severity.ERROR,
                disposition=Disposition.NEEDS_REVIEW,
                human_reason=(
                    "A zero longitude or latitude (null island) indicates a dropped "
                    "coordinate, not a real plot -- the source value must be recovered."
                ),
                failing_coordinate=[lon, lat],
            )
    return None


def _rule_out_of_aoi(geom: BaseGeometry, settings: Settings) -> Finding | None:
    """A coordinate in range but outside the AOI (e.g. a wrong-hemisphere negative
    longitude) cannot be auto-corrected -- the geocode must be confirmed."""
    for lon, lat in _all_coords(geom):
        if abs(lat) > 90.0 or abs(lon) > 180.0:
            return None  # range/swap rules own this; do not double-flag
    pad = 0.5  # tolerate plots straddling the painted AOI edge
    for lon, lat in _all_coords(geom):
        if not (
            settings.aoi_min_lon - pad <= lon <= settings.aoi_max_lon + pad
            and settings.aoi_min_lat - pad <= lat <= settings.aoi_max_lat + pad
        ):
            return Finding(
                rule_id="out_of_aoi",
                severity=Severity.ERROR,
                disposition=Disposition.NEEDS_REVIEW,
                human_reason=(
                    "Coordinate falls outside the expected area of interest "
                    "(Vietnam Central Highlands); the geocode must be confirmed, "
                    "not auto-corrected."
                ),
                failing_coordinate=[lon, lat],
            )
    return None


def _rule_unknown_mixed_crs(properties: dict | None) -> Finding | None:
    """A declared non-EPSG:4326 / mixed CRS is deliberately NOT blind-reprojected --
    reprojection silently moves every coordinate, so a human must confirm it."""
    if not properties:
        return None
    crs = properties.get("crs")
    if crs is None:
        return None
    crs_str = str(crs).upper()
    if "4326" in crs_str or "CRS84" in crs_str:
        return None
    return Finding(
        rule_id="unknown_mixed_crs",
        severity=Severity.ERROR,
        disposition=Disposition.NEEDS_REVIEW,
        human_reason=(
            f"Declared CRS {crs!r} is not EPSG:4326; the system never "
            "blind-reprojects coordinates -- the CRS must be confirmed first."
        ),
        details={"declared_crs": str(crs)},
    )


_COORDINATE_CREDIBILITY_RULES = frozenset(
    {"coordinate_out_of_range", "lat_lon_swap", "null_island"}
)


def _rule_grid_snapped_low_precision(
    geom: BaseGeometry, prior_findings: list[Finding] | None = None
) -> Finding | None:
    """Trailing-zero / <6-significant-decimal coordinates are a FORMATTING signal
    (tripwire G), not bad geolocation precision -> informational WARNING, AUTO_VALID.

    Short-circuits when a coordinate-credibility verdict (coordinate_out_of_range /
    lat_lon_swap / null_island) already fired for the same coordinate: reporting both
    a precision note and a credibility error for the same ordinate is noisy and
    misleading.  The rollup disposition is unchanged -- the credibility finding already
    dominates.
    """
    if prior_findings and any(f.rule_id in _COORDINATE_CREDIBILITY_RULES for f in prior_findings):
        return None
    coords = _all_coords(geom)
    if not coords:
        return None
    worst = min(max(_significant_decimals(lon), _significant_decimals(lat)) for lon, lat in coords)
    if worst >= _MIN_SIGNIFICANT_DECIMALS:
        return None
    return Finding(
        rule_id="grid_snapped_low_precision",
        severity=Severity.WARNING,
        disposition=Disposition.AUTO_VALID,
        human_reason=(
            f"Coordinates carry only ~{worst} significant decimal places; this is a "
            "formatting/precision-of-presentation signal, not a geolocation error -- "
            "informational, not blocking."
        ),
        details={"significant_decimals": worst},
    )


def _rule_geometry_type_vs_asserted_area(
    geom: BaseGeometry, properties: dict | None
) -> Finding | None:
    """A point is a valid submission for a <=4 ha plot (Art. 9(1)(d)); a point is
    AUTO_VALID by default. It is only NEEDS_REVIEW when accompanied by an asserted
    area > 4 ha -- a bare point cannot itself prove the <=4 ha point format."""
    if geom.geom_type != "Point":
        return None
    asserted = (properties or {}).get("asserted_area_ha")
    if asserted is None:
        return None
    required = required_format_for_area(float(asserted))
    if required == RequiredGeometryFormat.POLYGON:
        return Finding(
            rule_id="geometry_type_vs_asserted_area",
            severity=Severity.ERROR,
            disposition=Disposition.NEEDS_REVIEW,
            human_reason=(
                f"A single point was submitted but the asserted area "
                f"({asserted} ha) is >= {AREA_THRESHOLD_HA} ha, which requires a "
                "perimeter polygon (EUDR Art. 9(1)(d)). A point cannot prove the "
                "<=4 ha point-format eligibility -> human review."
            ),
            details={
                "asserted_area_ha": float(asserted),
                "required_format": required.value,
            },
        )
    return None


def _rule_holes(geom: BaseGeometry) -> Finding | None:
    """Per EUDR GeoJSON File Description v1.5, interior rings are not processed.
    This is not a topology error the system can silently drop: removing a hole
    changes the plot boundary and may change area/coverage -- so it is NOT
    auto-fixable. Escalated to NEEDS_REVIEW with the split-into-two-polygons
    workaround as the documented remedy (tripwire H)."""
    if not isinstance(geom, Polygon) or not geom.interiors:
        return None
    return Finding(
        rule_id="holes",
        severity=Severity.WARNING,
        disposition=Disposition.NEEDS_REVIEW,
        human_reason=(
            "Polygon has interior ring(s) (a hole). The EUDR GeoJson File "
            "Description v1.5 rejects interior rings; they cannot be silently "
            "dropped because doing so changes the plot boundary. The documented "
            "fix is to split the plot into two exterior-only polygons."
        ),
        details={"n_interior_rings": len(geom.interiors)},
    )


def _rule_sliver(geom: BaseGeometry) -> Finding | None:
    """A near-zero-width digitizing sliver has an area too small/unreliable for the
    4 ha format test and zonal coverage -> human review (do not auto-fix)."""
    if not isinstance(geom, (Polygon, MultiPolygon)):
        return None
    area = geodesic_area_ha(geom)
    if area <= 0:
        return None  # zero-area is the degenerate-collapse rule's domain
    compact = _compactness(geom)
    if area < _SLIVER_AREA_HA and compact < _COMPACTNESS_MIN:
        return Finding(
            rule_id="sliver",
            severity=Severity.ERROR,
            disposition=Disposition.NEEDS_REVIEW,
            human_reason=(
                f"Degenerate sliver polygon (area {area:.6f} ha, compactness "
                f"{compact:.4f}); the area is unreliable for the 4 ha format test "
                "and zonal coverage -> human review."
            ),
            details={"area_ha": area, "compactness": compact},
        )
    return None


def _rule_spike(geom: BaseGeometry) -> Finding | None:
    """A single far-flung outlier (spike/antenna) vertex inflates area materially;
    dropping it is a guess about intent, so escalate rather than auto-repair."""
    coords = _polygon_exterior_coords(geom)
    ring = coords[:-1] if len(coords) > 1 and coords[0] == coords[-1] else coords
    if len(ring) < 4:
        return None
    cx = statistics.median(x for x, _ in ring)
    cy = statistics.median(y for _, y in ring)
    dists = [math.hypot(x - cx, y - cy) for x, y in ring]
    med = statistics.median(dists)
    if med <= 0:
        return None
    worst_i = max(range(len(ring)), key=lambda i: dists[i])
    if dists[worst_i] / med <= _SPIKE_OUTLIER_RATIO:
        return None
    # A genuinely thin-but-uniform sliver is the sliver rule's domain, not a spike.
    if _compactness(geom) >= _COMPACTNESS_MIN:
        return None
    lon, lat = ring[worst_i]
    return Finding(
        rule_id="spike_vertex",
        severity=Severity.ERROR,
        disposition=Disposition.NEEDS_REVIEW,
        human_reason=(
            "A single far-flung outlier vertex (spike/antenna) dominates the "
            "polygon and materially changes its area; dropping it would guess the "
            "operator's intent, so this is escalated for review."
        ),
        failing_coordinate=[lon, lat],
        details={"outlier_ratio": dists[worst_i] / med},
    )


def _rule_unclosed_ring(original: BaseGeometry | str) -> Finding | None:
    """An unclosed exterior ring (first != last) is closed -- a safe, area-preserving
    repair -> AUTO_FIXED. Detected on the raw WKT/geometry before normalisation.

    Only applies to a bare POLYGON, not MULTIPOLYGON: the MULTIPOLYGON token also
    contains the substring ``POLYGON``, so we check that the type token starts with
    ``POLYGON`` but is not ``MULTIPOLYGON`` before entering the ring-level parse.
    """
    if isinstance(original, str):
        token = original.strip().upper()
        # Accept only a true single POLYGON, not MULTIPOLYGON.
        if not (token.startswith("POLYGON") and not token.startswith("MULTIPOLYGON")):
            return None
        head, _, body = original.partition("((")
        if not body:
            return None
        first_ring = body.split(")")[0]
        pts = [p.strip() for p in first_ring.split(",") if p.strip()]
        if len(pts) >= 3 and pts[0] != pts[-1]:
            return Finding(
                rule_id="unclosed_ring",
                severity=Severity.WARNING,
                disposition=Disposition.AUTO_FIXED,
                human_reason=(
                    "Exterior ring was not closed (first vertex != last); closing "
                    "the ring is a safe, area-preserving repair."
                ),
            )
    return None


def _rule_duplicate_vertices(geom: BaseGeometry) -> Finding | None:
    """Consecutive duplicate vertices (a stationary GPS logger) are dropped on
    cleaning -- the geometry is unchanged -> AUTO_FIXED."""
    coords = _polygon_exterior_coords(geom)
    for prev, cur in zip(coords, coords[1:], strict=False):
        if prev == cur:
            return Finding(
                rule_id="duplicate_vertices",
                severity=Severity.WARNING,
                disposition=Disposition.AUTO_FIXED,
                human_reason=(
                    "Consecutive duplicate vertices detected; they are dropped on "
                    "cleaning with no change to the geometry."
                ),
            )
    return None


def _invalid_geometry_finding(geom: BaseGeometry, settings: Settings) -> Finding | None:
    """The central repair rule.

    Repair an invalid geometry with make_valid(method="structure") and apply the
    area-stability gate (tripwire F): AUTO_FIXED only if the geodesic area is
    unchanged within ``repair_area_epsilon_frac`` AND the repair did not fragment a
    Polygon into a MultiPolygon. A Polygon->2-part-MultiPolygon (bowtie) or a
    collapse to a line / area change beyond epsilon escalates to NEEDS_REVIEW.

    The self-overlapping MultiPolygon is the one principled exception: unioning two
    overlapping parts is meant to remove double-counted area, so a MultiPolygon that
    collapses to a single Polygon is a legitimate AUTO_FIX even though area shrinks.
    """
    if geom.is_valid:
        return None

    area_before = geodesic_area_ha(geom)
    try:
        repaired = make_valid(geom, method="structure")
    except Exception:
        repaired = None

    base = {
        "rule_id": "invalid_geometry",
        "severity": Severity.ERROR,
        "failing_coordinate": _first_self_intersection(geom),
    }

    if repaired is None or repaired.is_empty:
        return Finding(
            **base,
            disposition=Disposition.NEEDS_REVIEW,
            human_reason="Geometry is invalid and could not be repaired -> human review.",
            details={"area_before_ha": area_before, "area_after_ha": 0.0},
        )

    area_after = geodesic_area_ha(repaired)

    # Degenerate collapse (e.g. collinear ring -> line/point): no usable area.
    if area_after <= 0 or repaired.geom_type in ("LineString", "MultiLineString", "Point"):
        return Finding(
            **base,
            disposition=Disposition.NEEDS_REVIEW,
            human_reason=(
                "Invalid geometry collapses to a zero-area / non-areal result on "
                "repair; area and coverage cannot be computed -> human review."
            ),
            details={"area_before_ha": area_before, "area_after_ha": area_after},
        )

    fragmented = isinstance(geom, Polygon) and isinstance(repaired, MultiPolygon)
    overlap_union = isinstance(geom, MultiPolygon) and isinstance(repaired, Polygon)
    if overlap_union:
        # Two overlapping parts unioned -- the area change is the intended removal of
        # double-counted overlap, not a meaning-changing repair.
        return Finding(
            **base,
            disposition=Disposition.AUTO_FIXED,
            human_reason=(
                "Self-overlapping multipolygon parts were unioned (overlap "
                "double-count removed); area recomputed and recorded."
            ),
            details={"area_before_ha": area_before, "area_after_ha": area_after},
        )

    area_frac = abs(area_after - area_before) / area_before if area_before > 0 else float("inf")
    if fragmented or area_frac > settings.repair_area_epsilon_frac:
        why = (
            "fragmented a polygon into multiple parts"
            if fragmented
            else f"changed geodesic area by {area_frac:.1%} (> epsilon "
            f"{settings.repair_area_epsilon_frac:.1%})"
        )
        return Finding(
            **base,
            disposition=Disposition.NEEDS_REVIEW,
            human_reason=(
                f"Invalid geometry; the repair {why}, so it cannot be auto-applied "
                "without changing the plot's meaning -> human review."
            ),
            details={
                "area_before_ha": area_before,
                "area_after_ha": area_after,
                "area_change_frac": area_frac,
                "fragmented": fragmented,
            },
        )

    return Finding(
        **base,
        disposition=Disposition.AUTO_FIXED,
        human_reason=(
            "Invalid geometry repaired (make_valid, structure method) with geodesic "
            "area unchanged within epsilon and no fragmentation -> safe auto-fix."
        ),
        details={
            "area_before_ha": area_before,
            "area_after_ha": area_after,
            "area_change_frac": area_frac,
        },
    )


def _first_self_intersection(geom: BaseGeometry) -> Position | None:
    """Best-effort [lon, lat] of an offending vertex (the duplicated bowtie pivot,
    when present), for the reviewer-facing finding."""
    coords = _polygon_exterior_coords(geom)
    seen: dict[tuple[float, float], int] = defaultdict(int)
    for c in coords[:-1] if coords else []:
        seen[c] += 1
    for c, n in seen.items():
        if n > 1:
            return [c[0], c[1]]
    return None


# --------------------------------------------------------------------------- #
# AOI helper
# --------------------------------------------------------------------------- #


def _in_aoi(lon: float, lat: float, settings: Settings, pad: float = 0.5) -> bool:
    return (
        settings.aoi_min_lon - pad <= lon <= settings.aoi_max_lon + pad
        and settings.aoi_min_lat - pad <= lat <= settings.aoi_max_lat + pad
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def validate_plot(
    geom: BaseGeometry | str,
    properties: dict | None = None,
    settings: Settings | None = None,
) -> ValidationReport:
    """Validate one plot geometry and return a typed ``ValidationReport``.

    ``geom`` may be a shapely geometry or a WKT string (handles the pathology
    WKT-in-property cases, including an open POLYGON ring which OGC WKT rejects).
    Each rule contributes at most one ``Finding``; the report's ``disposition``
    rolls them up to the worst.
    """
    settings = settings or get_settings()
    raw = geom
    g = _as_geometry(geom)

    findings: list[Finding] = []
    repaired_wkt: str | None = None

    # 1. Coordinate-level rules (run first; they own range/CRS/order problems).
    # Order is precedence: null-island and the swap diagnosis are more specific than
    # the generic out-of-range / out-of-AOI verdict, so they are offered first.
    for finder in (
        lambda: _rule_null_island(g),
        lambda: _rule_lat_lon_swap(g, settings),
        lambda: _rule_coordinate_range(g),
        lambda: _rule_out_of_aoi(g, settings),
    ):
        f = finder()
        if f is not None:
            findings.append(f)
            break  # one coordinate-credibility verdict is enough; avoid double-flag

    # 2. CRS assertion (property-driven).
    crs_f = _rule_unknown_mixed_crs(properties)
    if crs_f is not None and not any(
        f.rule_id in ("coordinate_out_of_range", "lat_lon_swap") for f in findings
    ):
        findings.append(crs_f)

    # 3. Submission-format (point vs polygon for an asserted area).
    fmt_f = _rule_geometry_type_vs_asserted_area(g, properties)
    if fmt_f is not None:
        findings.append(fmt_f)

    # 4. Structural repair + area-stability gate, then closely-related repairs.
    unclosed = _rule_unclosed_ring(raw)
    if unclosed is not None:
        findings.append(unclosed)
        repaired = make_valid(g, method="structure") if not g.is_valid else g
        repaired_wkt = repaired.wkt

    invalid_f = _invalid_geometry_finding(g, settings)
    if invalid_f is not None:
        findings.append(invalid_f)
        if invalid_f.disposition == Disposition.AUTO_FIXED:
            repaired_wkt = make_valid(g, method="structure").wkt

    dup_f = _rule_duplicate_vertices(g)
    if dup_f is not None and invalid_f is None:
        findings.append(dup_f)

    # 5. Degenerate-shape rules (only meaningful on a valid, areal geometry).
    if invalid_f is None:
        for finder in (lambda: _rule_spike(g), lambda: _rule_sliver(g)):
            f = finder()
            if f is not None:
                findings.append(f)
                break

    # 6. Holes (informational warning).
    hole_f = _rule_holes(g)
    if hole_f is not None:
        findings.append(hole_f)

    # 7. Precision/formatting (informational) -- short-circuits when a
    #    coordinate-credibility verdict already fired to avoid noisy co-findings.
    prec_f = _rule_grid_snapped_low_precision(g, prior_findings=findings)
    if prec_f is not None:
        findings.append(prec_f)

    plot_id = str((properties or {}).get("id", "")) if properties else ""
    return ValidationReport(
        plot_id=plot_id,
        source_geometry_type=g.geom_type,
        findings=findings,
        repaired_geometry_wkt=repaired_wkt,
    )


def validate_overlaps(features: list[dict]) -> list[Finding]:
    """Inter-plot overlap detection (ST_Overlaps semantics) via shapely STRtree.

    A pair of plots whose interiors overlap (share area but neither contains the
    other) is a boundary/double-claim ambiguity -> NEEDS_REVIEW. Returns one
    finding per overlapping pair.
    """
    geoms: list[BaseGeometry] = []
    ids: list[str] = []
    for i, feat in enumerate(features):
        gj = feat.get("geometry")
        if gj is None and feat.get("wkt"):
            g = _wkt.loads(feat["wkt"])
        elif gj is not None:
            g = shape(gj)
        else:
            continue
        if not g.is_valid:
            g = make_valid(g, method="structure")
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        geoms.append(g)
        ids.append(str(feat.get("id", i)))

    if len(geoms) < 2:
        return []

    tree = STRtree(geoms)
    findings: list[Finding] = []
    seen_pairs: set[tuple[int, int]] = set()
    for i, g in enumerate(geoms):
        for j in tree.query(g):
            j = int(j)
            if j <= i:
                continue
            pair = (i, j)
            if pair in seen_pairs:
                continue
            if g.overlaps(geoms[j]):
                seen_pairs.add(pair)
                findings.append(
                    Finding(
                        rule_id="inter_plot_overlap",
                        severity=Severity.ERROR,
                        disposition=Disposition.NEEDS_REVIEW,
                        human_reason=(
                            f"Plots {ids[i]!r} and {ids[j]!r} overlap (shared area, "
                            "neither contains the other); a double-claimed boundary "
                            "must be resolved by a human, not silently merged."
                        ),
                        details={"plot_a": ids[i], "plot_b": ids[j]},
                    )
                )
    return findings


def detect_duplicate_ids(features: list[dict]) -> list[Finding]:
    """A reused feature id is an identity collision; flag every reuse so it is never
    silently overwritten (idempotency/dedupe trap) -> NEEDS_REVIEW."""
    counts: dict[str, int] = defaultdict(int)
    for feat in features:
        fid = feat.get("id")
        if fid is None and isinstance(feat.get("properties"), dict):
            fid = feat["properties"].get("id")
        if fid is not None:
            counts[str(fid)] += 1
    findings: list[Finding] = []
    for fid, n in counts.items():
        if n > 1:
            findings.append(
                Finding(
                    rule_id="duplicate_feature_id",
                    severity=Severity.ERROR,
                    disposition=Disposition.NEEDS_REVIEW,
                    human_reason=(
                        f"Feature id {fid!r} appears {n} times; an identity "
                        "collision must be flagged, not silently overwritten."
                    ),
                    details={"feature_id": fid, "count": n},
                )
            )
    return findings


__all__ = ["validate_plot", "validate_overlaps", "detect_duplicate_ids"]
