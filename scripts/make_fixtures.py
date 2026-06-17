#!/usr/bin/env python3
"""Deterministic synthetic-AOI fixture regenerator for veritas-eudr.

Running this script (idempotently) writes the entire committed fixture set under
``tests/fixtures/``:

  rasters/        three small EPSG:4326 GeoTIFFs (Hansen lossyear, JRC GFC2020,
                  ESA WorldCover) painted with six labelled scenario zones.
  points/         ~50 representative coffee/cocoa coordinate points.
  submissions/    one "messy customer farm list" (GeoJSON + CSV) for `validate`.
  pathology/      8-12 documented real-world geometry failure modes (WKT).
  manifest.json   the MACHINE-READABLE expected-outcome contract consumed by the
                  wave-2 test agents.
  README.md       human-readable provenance + licence notes.

IMPORTANT -- these fixtures are SYNTHETIC, illustrative stand-ins built solely so
the test/CI path is offline and deterministic. They are NOT real Hansen / JRC /
ESA WorldCover / Sample Earth data. ``scripts/fetch_data.sh`` names the real
sources for production. Every value here is hand-painted to exercise a specific
EUDR correctness tripwire; the data must be read as fabricated, not observed.

Determinism: all randomness is seeded (SEED below). Rasters are integer arrays
painted by explicit rules. Running this script twice produces byte-stable rasters
and identical JSON/CSV files.

Self-verification: after writing, the script re-opens the rasters and samples
every manifest feature, asserting the sampled Hansen/JRC/WorldCover values match
the zone's intended encoding. It raises loudly on any mismatch.

AOI window (EPSG:4326): lon 108.000..108.060, lat 12.640..12.700
(Vietnam Central Highlands, robusta coffee; ~12.67 N -- mid-latitude tropics,
NOT near the equator).

Facts pinned (see policy/eudr_policy.yaml, sourced via veritas_eudr.config):
- Deforestation cutoff = 31 Dec 2020 (distinct from the 30 Dec 2026 application date).
- Hansen GFC-2025-v1.13: lossyear band 1..25 == calendar 2001..2025; 0 == no loss.
  Band 21 == 2021, the first post-cutoff annual band -> boundary-uncertain.
- Vietnam = LOW country risk (Comm. Impl. Reg. (EU) 2025/1093) -> simplified_dd.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod
from rasterio.transform import from_origin
from shapely import make_valid as _shapely_make_valid
from shapely.wkt import loads as _wkt_loads

from veritas_eudr.config import EUDR_DEFORESTATION_CUTOFF, load_policy

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SEED = 20201231  # the deforestation cutoff date, as a nod; any fixed int works.

# Regulatory facts sourced from the single source of truth.
_POLICY = load_policy()
_CUTOFF_DATE_STR: str = str(EUDR_DEFORESTATION_CUTOFF)          # "2020-12-31"
_APPLICATION_DATE_STR: str = _POLICY["regulation_application_date"]  # "2026-12-30"
_HANSEN_RELEASE: str = _POLICY["datasets"][0]["version"]             # "GFC-2025-v1.13"
_VN_RISK_STR: str = (
    f"low (Comm. Impl. Reg. (EU) 2025/1093) -> "
    f"{_POLICY['country_risk']['VN']['due_diligence_path']}"
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIX = PROJECT_ROOT / "tests" / "fixtures"
RASTERS = FIX / "rasters"
POINTS = FIX / "points"
SUBMISSIONS = FIX / "submissions"
PATHOLOGY = FIX / "pathology"

# AOI window (EPSG:4326).
AOI_MIN_LON, AOI_MAX_LON = 108.000, 108.060
AOI_MIN_LAT, AOI_MAX_LAT = 12.640, 12.700

# Raster grids (degrees-per-pixel).
HANSEN_DEG = 1.0 / 3600.0  # ~1 arc-sec (~30.7 m ground at 12.67 N).
TENM_DEG = 1.0 / 11250.0  # ~10 m ground (used by JRC + WorldCover fixtures).

NODATA_U8 = 255

# Hansen lossyear encoding: band == calendar_year - 2000.
HANSEN_PRE_CUTOFF = 18  # 2018 -- before 31 Dec 2020 cutoff.
HANSEN_BAND_21 = 21  # 2021 -- first post-cutoff annual band (boundary-uncertain).
HANSEN_BAND_22 = 22  # 2022 -- unambiguously post-cutoff.

# ESA WorldCover v200 class codes used here.
WC_TREE = 10
WC_CROPLAND = 40

_GEOD = Geod(ellps="WGS84")


# --------------------------------------------------------------------------- #
# Zones -- six labelled sub-boxes (non-overlapping) inside the AOI.
# Each is (min_lon, min_lat, max_lon, max_lat). Painted into all three rasters.
# --------------------------------------------------------------------------- #

ZONES: dict[str, dict] = {
    "Z_A": {
        "scenario": "old_clearing_coffee",
        "bbox": (108.002, 12.642, 108.010, 12.650),
        "jrc": 0,
        "hansen": HANSEN_PRE_CUTOFF,  # 2018, pre-cutoff
        "wc": WC_CROPLAND,
        "expected_risk_tier": "low",
        "rationale": (
            "Outside the 2020 forest baseline (JRC=0); only pre-2021 disturbance "
            "(Hansen=2018) and cropland context (WorldCover=40)."
        ),
    },
    "Z_B": {
        "scenario": "post_cutoff_high",
        "bbox": (108.012, 12.642, 108.020, 12.650),
        "jrc": 1,
        "hansen": HANSEN_BAND_22,  # 2022, post-cutoff, painted over a meaningful fraction
        "wc": WC_TREE,
        "expected_risk_tier": "high",
        "rationale": (
            "Inside 2020 forest (JRC=1), tree-cover context, post-cutoff loss "
            "(Hansen=2022) over a meaningful fraction (coverage >= threshold)."
        ),
    },
    "Z_C": {
        "scenario": "band21_latency",
        "bbox": (108.022, 12.642, 108.030, 12.650),
        "jrc": 1,
        "hansen": HANSEN_BAND_21,  # 2021, boundary-uncertain (tripwire B)
        "wc": WC_TREE,
        "expected_risk_tier": "more-info-needed",
        "rationale": (
            "Inside 2020 forest with post-cutoff loss in Hansen band 21 (2021), "
            "the first post-cutoff annual band -> boundary-uncertain (tripwire B)."
        ),
    },
    "Z_D": {
        "scenario": "intact_forest",
        "bbox": (108.032, 12.642, 108.040, 12.650),
        "jrc": 1,
        "hansen": 0,  # no loss
        "wc": WC_TREE,
        "expected_risk_tier": "more-info-needed",
        "rationale": (
            "Inside 2020 forest (JRC=1), tree context, no recorded loss and no "
            "commodity context -> inside forest, no context (more info needed)."
        ),
    },
    "Z_E": {
        "scenario": "outside_forest_crop",
        "bbox": (108.042, 12.642, 108.050, 12.650),
        "jrc": 0,
        "hansen": 0,  # no loss
        "wc": WC_CROPLAND,
        "expected_risk_tier": "low",
        "rationale": (
            "Outside the 2020 forest baseline (JRC=0), no loss, cropland context "
            "(WorldCover=40) -> low."
        ),
    },
    "Z_F": {
        "scenario": "subpixel_loss_signal_below_threshold",
        # Single-pixel loss zone: one Hansen=2022 pixel; a plot straddles it so the
        # post-cutoff loss coverage fraction falls BELOW the threshold (tripwire C).
        # Background of the box is JRC=1 / WC=tree so only the single loss pixel is
        # the signal. bbox here describes the painted single-pixel neighbourhood.
        "bbox": (108.0520, 12.6520, 108.0540, 12.6540),
        "jrc": 1,
        "hansen": 0,  # box background is no-loss; ONE pixel is set to 2022 (see paint).
        "wc": WC_TREE,
        "expected_risk_tier": "more-info-needed",
        "rationale": (
            "A single post-cutoff Hansen loss pixel (2022); the straddling plot is "
            "so small that summed loss coverage is below threshold (tripwire C)."
        ),
        # The single loss pixel: placed OFF the box centre so the zone-centre
        # sample reads the no-loss background and only this pixel carries loss.
        "loss_pixel_lonlat": (108.0535, 12.6535),
        "loss_band": HANSEN_BAND_22,
    },
}


# --------------------------------------------------------------------------- #
# Raster helpers
# --------------------------------------------------------------------------- #


def _grid_shape(deg: float) -> tuple[int, int, rasterio.Affine]:
    """Pixel grid + affine covering the AOI exactly at the given resolution."""
    width = int(round((AOI_MAX_LON - AOI_MIN_LON) / deg))
    height = int(round((AOI_MAX_LAT - AOI_MIN_LAT) / deg))
    # north-up: origin is top-left (min_lon, max_lat).
    transform = from_origin(AOI_MIN_LON, AOI_MAX_LAT, deg, deg)
    return height, width, transform


def _rowcol(transform, lon: float, lat: float) -> tuple[int, int]:
    """Pixel (row, col) for a lon/lat under an affine transform."""
    col, row = ~transform * (lon, lat)
    return int(math.floor(row)), int(math.floor(col))


def _paint_box(arr: np.ndarray, transform, bbox, value: int) -> None:
    """Set every pixel whose CENTRE falls in bbox to value."""
    min_lon, min_lat, max_lon, max_lat = bbox
    r0, c0 = _rowcol(transform, min_lon, max_lat)  # top-left
    r1, c1 = _rowcol(transform, max_lon, min_lat)  # bottom-right
    r_lo, r_hi = sorted((r0, r1))
    c_lo, c_hi = sorted((c0, c1))
    r_lo = max(r_lo, 0)
    c_lo = max(c_lo, 0)
    r_hi = min(r_hi, arr.shape[0] - 1)
    c_hi = min(c_hi, arr.shape[1] - 1)
    arr[r_lo : r_hi + 1, c_lo : c_hi + 1] = value


def _write_cog(path: Path, arr: np.ndarray, transform) -> None:
    """Write a small COG-style uint8 GeoTIFF (tiled, deflate, nodata set)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 1,
        "height": arr.shape[0],
        "width": arr.shape[1],
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": NODATA_U8,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
        "predictor": 2,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)
        dst.build_overviews([2, 4], rasterio.enums.Resampling.nearest)
        dst.update_tags(ns="rio_overview", resampling="nearest")


# --------------------------------------------------------------------------- #
# Raster painting
# --------------------------------------------------------------------------- #


def build_rasters() -> None:
    """Paint and write the three AOI rasters."""
    # ---- Hansen lossyear (uint8, ~1 arc-sec) ----
    h_h, h_w, h_t = _grid_shape(HANSEN_DEG)
    hansen = np.zeros((h_h, h_w), dtype=np.uint8)  # 0 = no loss everywhere
    for zid, z in ZONES.items():
        if zid == "Z_F":
            continue  # handled below as a single pixel
        if z["hansen"]:
            _paint_box(hansen, h_t, z["bbox"], z["hansen"])
    # Z_F: exactly one Hansen loss pixel.
    lon, lat = ZONES["Z_F"]["loss_pixel_lonlat"]
    r, c = _rowcol(h_t, lon, lat)
    hansen[r, c] = ZONES["Z_F"]["loss_band"]
    # Record the EXACT centre of that pixel AND its upper-left corner so the
    # straddling messy plot can be built deterministically against the Hansen
    # grid (the plot is centred on the corner so the loss pixel is one quadrant
    # of it -> summed loss coverage stays below threshold, tripwire C).
    px_lon, px_lat = (h_t * (c + 0.5, r + 0.5))
    corner_lon, corner_lat = (h_t * (c, r))
    ZONES["Z_F"]["loss_pixel_center"] = [round(px_lon, 8), round(px_lat, 8)]
    ZONES["Z_F"]["loss_pixel_corner"] = [round(corner_lon, 8), round(corner_lat, 8)]
    ZONES["Z_F"]["hansen_deg"] = HANSEN_DEG
    _write_cog(RASTERS / "hansen_lossyear_aoi.tif", hansen, h_t)

    # ---- JRC GFC2020 (uint8, ~10 m): 1 = forest at 2020, 0 = non-forest ----
    j_h, j_w, j_t = _grid_shape(TENM_DEG)
    jrc = np.zeros((j_h, j_w), dtype=np.uint8)
    for z in ZONES.values():
        if z["jrc"]:
            _paint_box(jrc, j_t, z["bbox"], 1)
    _write_cog(RASTERS / "jrc_gfc2020_aoi.tif", jrc, j_t)

    # ---- ESA WorldCover v200 (uint8, ~10 m) ----
    w_h, w_w, w_t = _grid_shape(TENM_DEG)
    # Default background = cropland (the AOI is a coffee landscape mosaic).
    wc = np.full((w_h, w_w), WC_CROPLAND, dtype=np.uint8)
    for z in ZONES.values():
        _paint_box(wc, w_t, z["bbox"], z["wc"])
    _write_cog(RASTERS / "worldcover_aoi.tif", wc, w_t)


# --------------------------------------------------------------------------- #
# Vector helpers
# --------------------------------------------------------------------------- #


def _zone_center(zid: str) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = ZONES[zid]["bbox"]
    return ((min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0)


def _poly_area_ha(ring: list[list[float]]) -> float:
    """Geodesic (WGS84) area in hectares for a closed lon/lat ring."""
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    area, _ = _GEOD.polygon_area_perimeter(lons, lats)
    return abs(area) / 1e4


def _square_ring(cx: float, cy: float, half_deg: float) -> list[list[float]]:
    """Closed square ring (lon/lat) centred on (cx, cy)."""
    return [
        [round(cx - half_deg, 8), round(cy - half_deg, 8)],
        [round(cx + half_deg, 8), round(cy - half_deg, 8)],
        [round(cx + half_deg, 8), round(cy + half_deg, 8)],
        [round(cx - half_deg, 8), round(cy + half_deg, 8)],
        [round(cx - half_deg, 8), round(cy - half_deg, 8)],
    ]


# --------------------------------------------------------------------------- #
# Points fixture
# --------------------------------------------------------------------------- #


def build_points() -> list[dict]:
    """~50 representative coffee/cocoa points, several inside each zone.

    HONEST LABEL: these are representative *synthesized* coordinates inside the
    AOI window. They are NOT real Sample Earth observations. Sample Earth
    (DOI 10.7910/DVN/U7HWY1) is a CC-BY-NC reference dataset and its points are
    NOT embedded here; ``scripts/fetch_data.sh`` fetches the real points.
    """
    rng = random.Random(SEED)
    features: list[dict] = []
    manifest_points: list[dict] = []

    # Several points per zone (interior, away from edges), plus AOI-wide filler.
    per_zone = 7
    idx = 0
    for zid, z in ZONES.items():
        min_lon, min_lat, max_lon, max_lat = z["bbox"]
        # inset so points land cleanly inside the painted zone (avoid pixel edges)
        ins_lon = (max_lon - min_lon) * 0.15
        ins_lat = (max_lat - min_lat) * 0.15
        for _ in range(per_zone):
            lon = round(rng.uniform(min_lon + ins_lon, max_lon - ins_lon), 6)
            lat = round(rng.uniform(min_lat + ins_lat, max_lat - ins_lat), 6)
            pid = f"pt-{idx:03d}"
            features.append(
                {
                    "type": "Feature",
                    "id": pid,
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "id": pid,
                        "zone": zid,
                        "scenario": z["scenario"],
                        "commodity": "coffee" if idx % 5 else "cocoa",
                        "synthetic": True,
                    },
                }
            )
            manifest_points.append(
                {
                    "id": pid,
                    "zone": zid,
                    "scenario": z["scenario"],
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "expected_risk_tier": z["expected_risk_tier"],
                    "rationale": z["rationale"],
                }
            )
            idx += 1

    # Filler points to reach ~50, scattered across the AOI interior (LOW context:
    # cropland background, outside any painted forest zone).
    target = 50
    while idx < target:
        lon = round(rng.uniform(AOI_MIN_LON + 0.001, AOI_MAX_LON - 0.001), 6)
        lat = round(rng.uniform(AOI_MIN_LAT + 0.001, AOI_MAX_LAT - 0.001), 6)
        pid = f"pt-{idx:03d}"
        features.append(
            {
                "type": "Feature",
                "id": pid,
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": pid,
                    "zone": "AOI_background",
                    "scenario": "cropland_mosaic",
                    "commodity": "coffee",
                    "synthetic": True,
                },
            }
        )
        manifest_points.append(
            {
                "id": pid,
                "zone": "AOI_background",
                "scenario": "cropland_mosaic",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "expected_risk_tier": "low",
                "rationale": "AOI cropland-mosaic background outside any painted forest zone.",
            }
        )
        idx += 1

    fc = {
        "type": "FeatureCollection",
        "name": "coffee_points",
        "_note": (
            "SYNTHETIC representative coffee/cocoa coordinates inside the AOI; "
            "NOT real Sample Earth points (CC-BY-NC, DOI 10.7910/DVN/U7HWY1). "
            "Committed offline fallback for deterministic CI."
        ),
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    _write_json(POINTS / "coffee_points.geojson", fc)
    return manifest_points


# --------------------------------------------------------------------------- #
# Messy submission fixture (GeoJSON + CSV)
# --------------------------------------------------------------------------- #


def build_submission() -> list[dict]:
    """One messy customer farm list exercising the `validate` showpiece.

    The GeoJSON file stays VALID JSON. Geometries that are not expressible as
    valid GeoJSON (the self-intersecting bowtie) are carried as WKT strings in a
    property and ALSO emitted to tests/fixtures/pathology/, as the manifest notes.
    """
    items: list[dict] = []
    features: list[dict] = []

    # 1) clean valid polygon (Z_A, comfortably < 4 ha, AUTO_VALID).
    cx, cy = _zone_center("Z_A")
    ring = _square_ring(cx, cy, 0.0005)  # ~0.6  ha
    items.append(
        {
            "id": "sub-clean-poly",
            "zone": "Z_A",
            "scenario": "clean_valid_polygon",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "expected_risk_tier": ZONES["Z_A"]["expected_risk_tier"],
            "expected_disposition": "AUTO_VALID",
            "rationale": "Valid, simple, sub-4-ha polygon over old-clearing coffee; passes as-is.",
            "area_ha": round(_poly_area_ha(ring), 4),
        }
    )

    # 2) self-intersecting bowtie (GPS drift). NOT valid as a GeoJSON polygon ring
    #    in the OGC sense -> carried as WKT in a property + a pathology file.
    bcx, bcy = _zone_center("Z_B")
    bowtie_wkt = (
        "POLYGON(("
        f"{bcx - 0.0006:.6f} {bcy - 0.0006:.6f}, "
        f"{bcx + 0.0006:.6f} {bcy + 0.0006:.6f}, "
        f"{bcx + 0.0006:.6f} {bcy - 0.0006:.6f}, "
        f"{bcx - 0.0006:.6f} {bcy + 0.0006:.6f}, "
        f"{bcx - 0.0006:.6f} {bcy - 0.0006:.6f}))"
    )
    items.append(
        {
            "id": "sub-bowtie",
            "zone": "Z_B",
            "scenario": "self_intersecting_bowtie",
            "wkt": bowtie_wkt,
            "expected_risk_tier": ZONES["Z_B"]["expected_risk_tier"],
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Self-intersecting figure-8 ring: signed area ~0 and "
                "ST_MakeValid(method=structure) fragments it into a 2-part MultiPolygon, "
                "so area is not meaningfully preserved; per the AUTO_FIXED-only-if-area-"
                "unchanged-within-epsilon gate (policy repair_area_epsilon_frac, tripwire F) "
                "this escalates to NEEDS_REVIEW rather than being silently auto-fixed."
            ),
            "origin": "GPS drift",
        }
    )

    # 3) lat/lon-swapped point (spreadsheet export). The swapped coords land at
    #    (lat=108.., lon=12.6..) -> outside the AOI -> the system must NOT
    #    auto-"correct" it; NEEDS_REVIEW.
    scx, scy = _zone_center("Z_E")
    items.append(
        {
            "id": "sub-latlon-swap",
            "zone": "Z_E",
            "scenario": "lat_lon_swapped_point",
            # stored swapped on purpose: [lat, lon] instead of [lon, lat]
            "geometry": {"type": "Point", "coordinates": [round(scy, 6), round(scx, 6)]},
            "expected_risk_tier": None,
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Spreadsheet export wrote [lat, lon]; swapped coordinates fall outside "
                "the AOI (stored lon=12.6 is a valid longitude but lands in sub-Saharan "
                "Africa, not Vietnam) -- [lat,lon] order is the classic spreadsheet-export "
                "bug and is not safely auto-correctable -> NEEDS_REVIEW."
            ),
            "origin": "spreadsheet export (lat/lon swap)",
            "intended_lonlat": [round(scx, 6), round(scy, 6)],
        }
    )

    # 4) polygon with an interior ring/hole (Z_D). Valid; AUTO_VALID.
    dcx, dcy = _zone_center("Z_D")
    outer = _square_ring(dcx, dcy, 0.0008)
    hole = _square_ring(dcx, dcy, 0.0002)
    hole = list(reversed(hole))  # interior rings wind opposite to the shell
    items.append(
        {
            "id": "sub-hole-poly",
            "zone": "Z_D",
            "scenario": "polygon_with_hole",
            "geometry": {"type": "Polygon", "coordinates": [outer, hole]},
            "expected_risk_tier": ZONES["Z_D"]["expected_risk_tier"],
            "expected_disposition": "AUTO_VALID",
            "rationale": "Donut polygon (legitimate unplanted patch); topologically valid.",
            "area_ha": round(_poly_area_ha(outer) - _poly_area_ha(hole), 4),
        }
    )

    # 5) sliver polygon (digitizing artefact) -- near-zero-width spike (Z_C).
    ccx, ccy = _zone_center("Z_C")
    sliver = [
        [round(ccx - 0.0008, 8), round(ccy, 8)],
        [round(ccx + 0.0008, 8), round(ccy + 0.0000040, 8)],
        [round(ccx + 0.0008, 8), round(ccy - 0.0000040, 8)],
        [round(ccx - 0.0008, 8), round(ccy, 8)],
    ]
    items.append(
        {
            "id": "sub-sliver",
            "zone": "Z_C",
            "scenario": "sliver_polygon",
            "geometry": {"type": "Polygon", "coordinates": [sliver]},
            "expected_risk_tier": ZONES["Z_C"]["expected_risk_tier"],
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Near-zero-width digitizing sliver; degenerate area is unreliable for "
                "the 4-ha format test and zonal coverage -> human review."
            ),
            "origin": "digitizing artefact",
            "area_ha": round(_poly_area_ha(sliver + [sliver[0]]), 6),
        }
    )

    # 6) duplicate feature id (collides with sub-clean-poly's intent) -- a second
    #    feature reusing an id already present, the classic dedupe trap.
    e2cx, e2cy = _zone_center("Z_E")
    ring2 = _square_ring(e2cx, e2cy, 0.0004)
    items.append(
        {
            "id": "sub-clean-poly",  # DUPLICATE id on purpose
            "_manifest_id": "sub-dup-id",
            "zone": "Z_E",
            "scenario": "duplicate_feature_id",
            "geometry": {"type": "Polygon", "coordinates": [ring2]},
            "expected_risk_tier": ZONES["Z_E"]["expected_risk_tier"],
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Feature id 'sub-clean-poly' reused; identity collision must be flagged, "
                "not silently overwritten (idempotency/dedupe trap)."
            ),
            "origin": "duplicate id in customer list",
        }
    )

    # 7) small plot straddling the single Z_F Hansen loss pixel so summed loss
    #    coverage is BELOW the 0.10 threshold (tripwire C). Centred on the loss
    #    pixel's upper-left corner with half = 2 Hansen pixels, so the loss pixel
    #    is one quadrant (~6.25% coverage), not the bare ST_Intersects "yes".
    fcorner_lon, fcorner_lat = ZONES["Z_F"]["loss_pixel_corner"]
    half = HANSEN_DEG * 2.0
    tiny = _square_ring(fcorner_lon, fcorner_lat, half)
    items.append(
        {
            "id": "sub-subpixel-zf",
            "zone": "Z_F",
            "scenario": "subpixel_loss_signal_below_threshold",
            "geometry": {"type": "Polygon", "coordinates": [tiny]},
            "expected_risk_tier": ZONES["Z_F"]["expected_risk_tier"],
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Plot straddles the single post-cutoff loss pixel, which covers only "
                "~6% of the plot -- summed post-cutoff loss coverage stays below the "
                "0.10 threshold, so bare intersection must not flag HIGH (tripwire C)."
            ),
            "area_ha": round(_poly_area_ha(tiny), 6),
            "expected_loss_coverage_frac": 0.0625,
        }
    )

    # 8) >4 ha area ASSERTED on a single POINT submission (format violation:
    #    Art. 9(1)(d) requires a polygon for >= 4 ha). NEEDS_REVIEW.
    bgcx, bgcy = (AOI_MIN_LON + 0.030, AOI_MIN_LAT + 0.045)
    items.append(
        {
            "id": "sub-bigpoint",
            "zone": "AOI_background",
            "scenario": "over_4ha_point",
            "geometry": {"type": "Point", "coordinates": [round(bgcx, 6), round(bgcy, 6)]},
            "expected_risk_tier": "low",
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Asserted area 5.2 ha on a single point; EUDR Art. 9(1)(d) requires a "
                "perimeter polygon at >= 4 ha -> format violation, needs review."
            ),
            "asserted_area_ha": 5.2,
            "origin": "point submitted for a large plot",
        }
    )

    # 9) legitimate <= 4 ha point (valid point submission for a small plot).
    okcx, okcy = (AOI_MIN_LON + 0.020, AOI_MIN_LAT + 0.050)
    items.append(
        {
            "id": "sub-smallpoint",
            "zone": "AOI_background",
            "scenario": "under_4ha_point",
            "geometry": {"type": "Point", "coordinates": [round(okcx, 6), round(okcy, 6)]},
            "expected_risk_tier": "low",
            "expected_disposition": "AUTO_VALID",
            "rationale": (
                "Asserted area 1.8 ha on a point; < 4 ha so a single point is a valid "
                "EUDR submission format."
            ),
            "asserted_area_ha": 1.8,
        }
    )

    # Build the VALID-JSON GeoJSON FeatureCollection (skip the WKT-only bowtie).
    for it in items:
        if "geometry" not in it:
            continue
        props = {
            k: v
            for k, v in it.items()
            if k not in {"geometry", "wkt", "_manifest_id"}
        }
        props["synthetic"] = True
        features.append(
            {
                "type": "Feature",
                "id": it["id"],
                "geometry": it["geometry"],
                "properties": props,
            }
        )
    # The bowtie travels as a property-only feature so the file stays valid JSON.
    features.append(
        {
            "type": "Feature",
            "id": "sub-bowtie",
            "geometry": None,
            "properties": {
                "id": "sub-bowtie",
                "zone": "Z_B",
                "scenario": "self_intersecting_bowtie",
                "wkt": bowtie_wkt,
                "note": "self-intersecting; geometry carried as WKT (invalid GeoJSON ring)",
                "synthetic": True,
            },
        }
    )

    fc = {
        "type": "FeatureCollection",
        "name": "messy_submission",
        "_note": (
            "SYNTHETIC messy customer farm list for the `validate` showpiece. "
            "Self-intersecting geometry is carried as WKT in properties to keep the "
            "file valid JSON; see tests/fixtures/pathology/ and manifest.json."
        ),
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    _write_json(SUBMISSIONS / "messy_submission.geojson", fc)

    # ---- CSV variant: UTF-8 BOM, one comma-decimal row, >=6-decimal coords ----
    _write_submission_csv(items)

    # Manifest rows use a stable manifest id (resolving the duplicate-id collision).
    manifest_rows = []
    for it in items:
        mid = it.get("_manifest_id", it["id"])
        row = {
            "id": mid,
            "zone": it["zone"],
            "scenario": it["scenario"],
            "expected_risk_tier": it["expected_risk_tier"],
            "expected_disposition": it["expected_disposition"],
            "rationale": it["rationale"],
        }
        if "geometry" in it:
            row["geometry"] = it["geometry"]
        if "wkt" in it:
            row["wkt"] = it["wkt"]
        for extra in (
            "asserted_area_ha",
            "area_ha",
            "origin",
            "intended_lonlat",
            "expected_loss_coverage_frac",
        ):
            if extra in it:
                row[extra] = it[extra]
        manifest_rows.append(row)
    return manifest_rows


def _write_submission_csv(items: list[dict]) -> None:
    """Emit the CSV variant with a BOM, a comma-decimal row, and 6+ dp coords.

    The CSV is a flat point/asserted-area list (the common customer export shape).
    Polygons are represented by their centroid + a wkt column for completeness.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "lon", "lat", "asserted_area_ha", "geom_type", "scenario", "wkt"])
    for i, it in enumerate(items):
        mid = it.get("_manifest_id", it["id"])
        geom = it.get("geometry")
        wkt = it.get("wkt", "")
        if geom and geom["type"] == "Point":
            lon, lat = geom["coordinates"]
            gtype = "Point"
        elif geom and geom["type"] == "Polygon":
            ring = geom["coordinates"][0]
            lon = sum(p[0] for p in ring[:-1]) / (len(ring) - 1)
            lat = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
            gtype = "Polygon"
        else:
            # bowtie (wkt-only)
            lon, lat, gtype = "", "", "Polygon"
        asserted = it.get("asserted_area_ha", it.get("area_ha", ""))
        # >= 6-decimal coordinates throughout.
        lon_s = f"{lon:.6f}" if lon != "" else ""
        lat_s = f"{lat:.6f}" if lat != "" else ""
        asserted_s = f"{asserted}" if asserted != "" else ""
        # One row uses a comma decimal separator (locale export): the FIRST row.
        if i == 0 and lon_s and lat_s:
            lon_s = lon_s.replace(".", ",")
            lat_s = lat_s.replace(".", ",")
            if asserted_s:
                asserted_s = asserted_s.replace(".", ",")
        writer.writerow([mid, lon_s, lat_s, asserted_s, gtype, it["scenario"], wkt])

    text = out.getvalue()
    SUBMISSIONS.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM prepended on purpose (Excel export signature).
    with open(SUBMISSIONS / "messy_submission.csv", "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# Pathology fixture
# --------------------------------------------------------------------------- #


def _make_zero_area_spur_entry(cx: float, cy: float) -> dict:
    """Build a pathology entry for a valid-area polygon with a zero-area spur.

    The ring visits the midpoint M of its top edge twice: once going toward C, once
    returning from the spike tip P (which lies just outside the polygon boundary).
    This creates a self-touching ring -- GEOS reports it invalid.

    make_valid(method=structure) (the PostGIS default and Shapely >= 2 default) resolves
    the self-touch by splitting at M, producing a single Polygon (the main square; the
    zero-area spike arm is discarded).  Geodesic area is unchanged within epsilon.

    This is the safe-repair counterexample to the bowtie: the spur has zero signed area
    (the arm from M to P and back to M encloses nothing), so the area gate passes and
    AUTO_FIXED is the correct disposition.
    """
    scale = 0.001
    A = (cx, cy)
    B = (cx + scale, cy)
    C = (cx + scale, cy + scale)
    M = (cx + scale * 0.5, cy + scale)      # midpoint of top edge
    P = (cx + scale * 0.5, cy + scale * 1.001)  # spike tip (just outside the square)
    D = (cx, cy + scale)

    wkt = (
        f"POLYGON(("
        f"{A[0]:.7f} {A[1]:.7f}, "
        f"{B[0]:.7f} {B[1]:.7f}, "
        f"{C[0]:.7f} {C[1]:.7f}, "
        f"{M[0]:.7f} {M[1]:.7f}, "
        f"{P[0]:.7f} {P[1]:.7f}, "
        f"{M[0]:.7f} {M[1]:.7f}, "
        f"{D[0]:.7f} {D[1]:.7f}, "
        f"{A[0]:.7f} {A[1]:.7f}))"
    )

    # Verify with shapely that make_valid(method=structure) preserves area and
    # does NOT fragment into multiple parts.
    geom = _wkt_loads(wkt)
    fixed = _shapely_make_valid(geom, method="structure")
    assert fixed.geom_type == "Polygon", (
        f"path-zero-area-spur: expected Polygon after repair, got {fixed.geom_type}"
    )
    orig_area_m2 = abs(_GEOD.geometry_area_perimeter(geom)[0])
    fixed_area_m2 = abs(_GEOD.geometry_area_perimeter(fixed)[0])
    frac_diff = abs(orig_area_m2 - fixed_area_m2) / max(orig_area_m2, 1.0)
    assert frac_diff < 0.01, (
        f"path-zero-area-spur: geodesic area changed by {frac_diff:.4%} after repair"
    )

    return {
        "id": "path-zero-area-spur",
        "scenario": "zero_area_spur_self_touching_ring",
        "origin": "GPS digitizing glitch -- stylus briefly touched the ring boundary mid-edge",
        "wkt": wkt,
        "expected_disposition": "AUTO_FIXED",
        "rationale": (
            "ST_MakeValid removes a zero-area spur; geodesic area unchanged within epsilon "
            "-> AUTO_FIXED (the safe-repair counterexample to the bowtie)."
        ),
    }


def build_pathology() -> list[dict]:
    """8-12 documented real-world geometry failure modes, each cited.

    Emitted as WKT (with a couple of GeoJSON-shaped entries) into a single
    pathology JSON sidecar; the manifest carries the per-feature contract.
    """
    cx, cy = (AOI_MIN_LON + 0.030, AOI_MIN_LAT + 0.030)

    entries: list[dict] = [
        {
            "id": "path-bowtie",
            "scenario": "self_intersection_bowtie",
            "origin": "GPS drift on a hand-walked perimeter",
            "wkt": (
                f"POLYGON(({cx - 0.0006:.6f} {cy - 0.0006:.6f}, "
                f"{cx + 0.0006:.6f} {cy + 0.0006:.6f}, "
                f"{cx + 0.0006:.6f} {cy - 0.0006:.6f}, "
                f"{cx - 0.0006:.6f} {cy + 0.0006:.6f}, "
                f"{cx - 0.0006:.6f} {cy - 0.0006:.6f}))"
            ),
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": (
                "Self-intersecting figure-8 ring: signed area ~0 and "
                "ST_MakeValid(method=structure) fragments it into a 2-part MultiPolygon, "
                "so area is not meaningfully preserved; per the AUTO_FIXED-only-if-area-"
                "unchanged-within-epsilon gate (policy repair_area_epsilon_frac, tripwire F) "
                "this escalates to NEEDS_REVIEW rather than being silently auto-fixed."
            ),
        },
        {
            "id": "path-unclosed-ring",
            "scenario": "unclosed_ring",
            "origin": "digitizing -- last vertex not snapped to first",
            "wkt": (
                f"POLYGON(({cx:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy + 0.0008:.6f}, "
                f"{cx:.6f} {cy + 0.0008:.6f}))"
            ),
            "expected_disposition": "AUTO_FIXED",
            "rationale": "Ring not closed; closing the ring is a safe, area-preserving repair.",
            "note": "WKT shown with an open ring on purpose (not valid OGC WKT).",
        },
        {
            "id": "path-latlon-swap",
            "scenario": "lat_lon_swap",
            "origin": "spreadsheet export wrote [lat, lon]",
            # swapped: lon and lat exchanged -> lands outside the AOI / off-pattern.
            "wkt": f"POINT({cy:.6f} {cx:.6f})",
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": "Axis order ambiguous; not safely auto-correctable -> escalate.",
            "intended_wkt": f"POINT({cx:.6f} {cy:.6f})",
        },
        {
            "id": "path-mixed-crs",
            "scenario": "crs_confusion_web_mercator_meters",
            "origin": "CRS confusion -- coordinates in EPSG:3857 metres, declared 4326",
            "wkt": "POINT(12027500.0 1419000.0)",
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": "Values are Web Mercator metres, not degrees; CRS must be confirmed.",
        },
        {
            "id": "path-duplicate-vertices",
            "scenario": "consecutive_duplicate_vertices",
            "origin": "GPS logger emitted repeated fixes while stationary",
            "wkt": (
                f"POLYGON(({cx:.6f} {cy:.6f}, {cx:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy + 0.0008:.6f}, "
                f"{cx:.6f} {cy + 0.0008:.6f}, {cx:.6f} {cy:.6f}))"
            ),
            "expected_disposition": "AUTO_FIXED",
            "rationale": "Repeated vertices are dropped on cleaning; geometry unchanged.",
        },
        {
            "id": "path-spike",
            "scenario": "spike_antenna_vertex",
            "origin": "single bad GPS fix -- a far-flung spike vertex",
            "wkt": (
                f"POLYGON(({cx:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy + 0.0008:.6f}, "
                f"{cx + 0.5000:.6f} {cy + 0.5000:.6f}, "  # spike far outside AOI
                f"{cx:.6f} {cy + 0.0008:.6f}, {cx:.6f} {cy:.6f}))"
            ),
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": "A spike changes area materially; not a safe auto-repair -> review.",
        },
        {
            "id": "path-zero-area",
            "scenario": "collapsed_zero_area_polygon",
            "origin": "all vertices collinear (bad digitizing)",
            "wkt": (
                f"POLYGON(({cx:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy:.6f}, "
                f"{cx + 0.0016:.6f} {cy:.6f}, {cx:.6f} {cy:.6f}))"
            ),
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": "Degenerate zero-area ring; cannot compute area/coverage -> review.",
        },
        {
            "id": "path-tiny-sliver",
            "scenario": "micro_sliver",
            "origin": "near-collinear digitizing of a field edge",
            "wkt": (
                f"POLYGON(({cx:.6f} {cy:.6f}, "
                f"{cx + 0.0010:.6f} {cy + 0.0000030:.6f}, "
                f"{cx + 0.0010:.6f} {cy - 0.0000030:.6f}, {cx:.6f} {cy:.6f}))"
            ),
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": "Sub-pixel sliver; area unreliable for the 4-ha format test.",
        },
        {
            "id": "path-multipolygon-overlap",
            "scenario": "self_overlapping_multipolygon",
            "origin": "two digitized parcels merged with overlap",
            "wkt": (
                "MULTIPOLYGON((("
                f"{cx:.6f} {cy:.6f}, {cx + 0.0008:.6f} {cy:.6f}, "
                f"{cx + 0.0008:.6f} {cy + 0.0008:.6f}, {cx:.6f} {cy + 0.0008:.6f}, "
                f"{cx:.6f} {cy:.6f})), (("
                f"{cx + 0.0004:.6f} {cy + 0.0004:.6f}, {cx + 0.0012:.6f} {cy + 0.0004:.6f}, "
                f"{cx + 0.0012:.6f} {cy + 0.0012:.6f}, {cx + 0.0004:.6f} {cy + 0.0012:.6f}, "
                f"{cx + 0.0004:.6f} {cy + 0.0004:.6f})))"
            ),
            "expected_disposition": "AUTO_FIXED",
            "rationale": "Overlapping parts unioned by ST_MakeValid; area recomputed and recorded.",
        },
        {
            "id": "path-antimeridian-decoy",
            "scenario": "out_of_aoi_coordinate",
            "origin": "wrong-hemisphere keying (negative longitude)",
            "wkt": f"POINT({-cx:.6f} {cy:.6f})",
            "expected_disposition": "NEEDS_REVIEW",
            "rationale": "Longitude sign flips the point out of Vietnam; geocode must be confirmed.",
        },
        _make_zero_area_spur_entry(cx, cy),
    ]

    sidecar = {
        "name": "pathology",
        "_note": (
            "SYNTHETIC geometry pathologies, each from a documented real-world failure "
            "mode (origin cited per entry). WKT is used so open-ring / self-crossing "
            "cases (not valid GeoJSON) are representable; see manifest.json."
        ),
        "crs": "EPSG:4326",
        "entries": entries,
    }
    _write_json(PATHOLOGY / "pathology.json", sidecar)

    manifest_rows = []
    for e in entries:
        manifest_rows.append(
            {
                "id": e["id"],
                "zone": "pathology",
                "scenario": e["scenario"],
                "wkt": e["wkt"],
                "expected_risk_tier": None,
                "expected_disposition": e["expected_disposition"],
                "origin": e["origin"],
                "rationale": e["rationale"],
            }
        )
    return manifest_rows


# --------------------------------------------------------------------------- #
# Manifest + README
# --------------------------------------------------------------------------- #


def build_manifest(points: list[dict], submission: list[dict], pathology: list[dict]) -> None:
    manifest = {
        "schema_version": "1",
        "generator": "scripts/make_fixtures.py",
        "seed": SEED,
        "synthetic": True,
        "_note": (
            "Machine-readable expected-outcome contract for the synthetic AOI fixtures. "
            "All data is SYNTHETIC and illustrative; values are hand-painted to exercise "
            "specific EUDR correctness tripwires. expected_risk_tier is one of "
            "low/high/more-info-needed (null where N/A); expected_disposition is one of "
            "AUTO_VALID/AUTO_FIXED/NEEDS_REVIEW (where applicable)."
        ),
        "aoi": {
            "crs": "EPSG:4326",
            "min_lon": AOI_MIN_LON,
            "min_lat": AOI_MIN_LAT,
            "max_lon": AOI_MAX_LON,
            "max_lat": AOI_MAX_LAT,
            "region": "Vietnam Central Highlands (robusta coffee), ~12.67 N (mid-latitude tropics)",
        },
        "facts": {
            "deforestation_cutoff_date": _CUTOFF_DATE_STR,
            "regulation_application_date": _APPLICATION_DATE_STR,
            "hansen_release": _HANSEN_RELEASE,
            "hansen_lossyear_encoding": "band 1..25 == calendar 2001..2025; 0 == no loss",
            "country_risk": {"VN": _VN_RISK_STR},
            "zonal_engine": "exactextract",
        },
        "rasters": {
            "hansen_lossyear_aoi.tif": {
                "dataset": "Hansen GFC-2025-v1.13 lossyear (SYNTHETIC stand-in)",
                "dtype": "uint8",
                "nodata": NODATA_U8,
                "deg_per_pixel": HANSEN_DEG,
                "encoding": "0=no loss; 1..25 == 2001..2025",
            },
            "jrc_gfc2020_aoi.tif": {
                "dataset": "JRC GFC2020 V3 (SYNTHETIC stand-in)",
                "dtype": "uint8",
                "nodata": NODATA_U8,
                "deg_per_pixel": TENM_DEG,
                "encoding": "1=forest at 2020; 0=non-forest",
            },
            "worldcover_aoi.tif": {
                "dataset": "ESA WorldCover v200 (SYNTHETIC stand-in)",
                "dtype": "uint8",
                "nodata": NODATA_U8,
                "deg_per_pixel": TENM_DEG,
                "encoding": "ESA WorldCover classes (10=tree cover, 40=cropland)",
            },
        },
        "zones": {
            zid: {
                "scenario": z["scenario"],
                "bbox": list(z["bbox"]),
                "jrc": z["jrc"],
                "hansen": z["hansen"],
                "worldcover": z["wc"],
                "expected_risk_tier": z["expected_risk_tier"],
                "rationale": z["rationale"],
                **(
                    {
                        "loss_pixel_center": z["loss_pixel_center"],
                        "loss_pixel_corner": z["loss_pixel_corner"],
                        "loss_band": z["loss_band"],
                    }
                    if zid == "Z_F"
                    else {}
                ),
            }
            for zid, z in ZONES.items()
        },
        "features": {
            "points": points,
            "submission": submission,
            "pathology": pathology,
        },
    }
    _write_json(FIX / "manifest.json", manifest)


def write_readme() -> None:
    text = README_TEXT
    (FIX / "README.md").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Self-verification
# --------------------------------------------------------------------------- #


def _sample_point(ds_path: Path, lon: float, lat: float) -> int:
    with rasterio.open(ds_path) as ds:
        val = list(ds.sample([(lon, lat)]))[0][0]
    return int(val)


def verify() -> None:
    """Re-open rasters and assert every zone samples to its intended encoding.

    Samples at each zone centre (the painted interior), plus the Z_F single loss
    pixel centre. Raises AssertionError loudly on any mismatch.
    """
    hansen = RASTERS / "hansen_lossyear_aoi.tif"
    jrc = RASTERS / "jrc_gfc2020_aoi.tif"
    wc = RASTERS / "worldcover_aoi.tif"

    for zid, z in ZONES.items():
        cx, cy = _zone_center(zid)
        got_j = _sample_point(jrc, cx, cy)
        got_w = _sample_point(wc, cx, cy)
        assert got_j == z["jrc"], f"{zid}: JRC sampled {got_j}, expected {z['jrc']}"
        assert got_w == z["wc"], f"{zid}: WorldCover sampled {got_w}, expected {z['wc']}"
        if zid == "Z_F":
            # Background of the box is no-loss at the centre...
            got_h_bg = _sample_point(hansen, cx, cy)
            assert got_h_bg == 0, f"Z_F background Hansen sampled {got_h_bg}, expected 0"
            # ...and exactly the single loss pixel carries band 22.
            plon, plat = z["loss_pixel_center"]
            got_h_px = _sample_point(hansen, plon, plat)
            assert got_h_px == z["loss_band"], (
                f"Z_F loss pixel Hansen sampled {got_h_px}, expected {z['loss_band']}"
            )
        else:
            got_h = _sample_point(hansen, cx, cy)
            assert got_h == z["hansen"], f"{zid}: Hansen sampled {got_h}, expected {z['hansen']}"

    # CRS + range sanity on each raster.
    for path, lo, hi in (
        (hansen, 0, 25),
        (jrc, 0, 1),
        (wc, 0, 95),
    ):
        with rasterio.open(path) as ds:
            assert ds.crs is not None and ds.crs.to_epsg() == 4326, f"{path.name}: not EPSG:4326"
            assert ds.nodata == NODATA_U8, f"{path.name}: nodata != {NODATA_U8}"
            data = ds.read(1)
            valid = data[data != NODATA_U8]
            assert valid.min() >= lo and valid.max() <= hi, (
                f"{path.name}: values out of [{lo},{hi}] -> [{valid.min()},{valid.max()}]"
            )
            size_mb = path.stat().st_size / 1e6
            assert size_mb < 1.0, f"{path.name}: {size_mb:.3f} MB exceeds 1 MB budget"

    print("self-verification: OK -- all zones sample to intended Hansen/JRC/WorldCover encodings")


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, ensure_ascii=False)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# README content
# --------------------------------------------------------------------------- #

README_TEXT = """\
# tests/fixtures -- synthetic AOI fixture set

All files in this tree are **SYNTHETIC, illustrative stand-ins**, generated by
`scripts/make_fixtures.py` (seeded; idempotent -- running it twice yields
identical files). They exist solely so the test/CI path runs **offline and
deterministically**. They are **not** real Hansen / JRC / ESA WorldCover /
Sample Earth data. `scripts/fetch_data.sh` names the real production sources.

## AOI

EPSG:4326 window: lon 108.000..108.060, lat 12.640..12.700 -- Vietnam Central
Highlands (robusta coffee), ~12.67 N (mid-latitude tropics).

Facts pinned (see `policy/eudr_policy.yaml`): deforestation cutoff 31 Dec 2020
(distinct from the 30 Dec 2026 application date); Hansen GFC-2025-v1.13 lossyear
band 1..25 == calendar 2001..2025, 0 == no loss; Vietnam = LOW country risk
(Comm. Impl. Reg. (EU) 2025/1093) -> simplified_dd.

## Rasters (`rasters/`)

Small uint8 GeoTIFFs, EPSG:4326, nodata=255, < 1 MB each (tiled + deflate).

| File | Dataset stood in for | Grid | Encoding |
| --- | --- | --- | --- |
| `hansen_lossyear_aoi.tif` | Hansen GFC-2025-v1.13 lossyear (CC-BY 4.0) | ~1 arc-sec (1/3600 deg) | 0=no loss; 1..25 == 2001..2025 |
| `jrc_gfc2020_aoi.tif` | JRC GFC2020 V3 (free, attribution required) | ~10 m (1/11250 deg) | 1=forest @2020; 0=non-forest |
| `worldcover_aoi.tif` | ESA WorldCover v200 (CC-BY 4.0) | ~10 m | classes (10=tree, 40=cropland) |

### Painted zones (six non-overlapping sub-boxes)

| Zone | Scenario | JRC | Hansen | WorldCover | Expected risk |
| --- | --- | --- | --- | --- | --- |
| Z_A | old_clearing_coffee | 0 | 18 (2018, pre-cutoff) | 40 cropland | low |
| Z_B | post_cutoff_high | 1 | 22 (2022) over area | 10 tree | high |
| Z_C | band21_latency | 1 | 21 (2021) | 10 tree | more-info-needed (tripwire B) |
| Z_D | intact_forest | 1 | 0 | 10 tree | more-info-needed |
| Z_E | outside_forest_crop | 0 | 0 | 40 cropland | low |
| Z_F | subpixel_edge | 1 | one 2022 pixel | 10 tree | more-info-needed (tripwire C) |

## Points (`points/coffee_points.geojson`)

~50 representative coffee/cocoa points inside the AOI (several per zone). These
are **synthesized** coordinates -- **not** real Sample Earth observations.
Sample Earth (CC-BY-NC 4.0, DOI 10.7910/DVN/U7HWY1) is a NonCommercial reference
dataset and is **not embedded** here; `scripts/fetch_data.sh` fetches the real
points.

## Messy submission (`submissions/`)

`messy_submission.geojson` (valid JSON) and `messy_submission.csv` -- one messy
customer farm list for the `validate` showpiece. Itemised in `manifest.json`:
clean valid polygon; self-intersecting bowtie (GPS drift, carried as WKT in a
property because it is not a valid GeoJSON ring); lat/lon-swapped point
(spreadsheet export); polygon with an interior ring/hole; sliver polygon;
duplicate feature id; sub-pixel tiny plot in Z_F; a >4 ha area asserted on a
single point; a legitimate <=4 ha point.

The CSV additionally carries a UTF-8 BOM, one row using a comma decimal separator
(locale export), and >=6-decimal coordinates.

## Pathology (`pathology/pathology.json`)

10 documented real-world geometry failure modes (WKT), each with a cited origin
(GPS drift -> self-intersection / spike / duplicate vertices; digitizing ->
unclosed ring / sliver / collapsed zero-area; spreadsheet export -> lat/lon swap;
CRS confusion -> Web Mercator metres; wrong-hemisphere keying -> out-of-AOI).
WKT is used so open-ring / self-crossing cases (not valid GeoJSON) are
representable. The per-feature expected disposition is in `manifest.json`.

## Manifest (`manifest.json`)

Machine-readable expected-outcome contract consumed by the wave-2 test agents.
For every vector feature it carries id, zone, scenario, geometry (or wkt),
`expected_risk_tier` (low/high/more-info-needed or null), `expected_disposition`
(AUTO_VALID/AUTO_FIXED/NEEDS_REVIEW where applicable) and a one-line rationale.

## Licences of the real datasets stood in for

- Hansen GFC-2025-v1.13: CC-BY 4.0.
- JRC GFC2020 V3: free of charge, attribution required (EC reuse notice).
- ESA WorldCover v200: CC-BY 4.0.
- Sample Earth (CIAT/Alliance): CC-BY-NC 4.0 (NonCommercial), DOI 10.7910/DVN/U7HWY1.
- FDaP coffee commodity model_2025b: CC-BY 4.0 (2025b COGs).
"""


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    random.seed(SEED)
    np.random.seed(SEED)

    build_rasters()
    points = build_points()
    submission = build_submission()
    pathology = build_pathology()
    build_manifest(points, submission, pathology)
    write_readme()
    verify()
    print(f"fixtures written under {FIX}")


if __name__ == "__main__":
    main()
