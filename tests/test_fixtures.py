"""Tests for the synthetic-AOI fixture set + regenerator.

These cover the fixture *contract* the wave-2 test agents depend on:

(a) idempotent regeneration -- running make_fixtures into a temp copy of the repo
    reproduces byte-identical rasters and JSON (seeded, deterministic); the
    committed files also exist and load.
(b) manifest.json schema -- required keys, value domains, AOI containment, and
    per-feature expected_risk_tier / expected_disposition vocabularies.
(c) each raster opens with rasterio at EPSG:4326 with the expected value ranges,
    nodata, and the six painted zones sampling to their intended encodings.
(d) coffee_points has ~50 features all inside the AOI bbox.

All fixtures are SYNTHETIC stand-ins (see tests/fixtures/README.md); these tests
assert the fabricated encodings are internally consistent, not that any real
dataset was observed.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import rasterio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIX = PROJECT_ROOT / "tests" / "fixtures"
RASTERS = FIX / "rasters"
SCRIPT = PROJECT_ROOT / "scripts" / "make_fixtures.py"

AOI = (108.000, 12.640, 108.060, 12.700)  # min_lon, min_lat, max_lon, max_lat

RISK_TIERS = {"low", "high", "more-info-needed", None}
DISPOSITIONS = {"AUTO_VALID", "AUTO_FIXED", "NEEDS_REVIEW", None}

RASTER_RANGES = {
    "hansen_lossyear_aoi.tif": (0, 25),
    "jrc_gfc2020_aoi.tif": (0, 1),
    "worldcover_aoi.tif": (0, 95),
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def manifest() -> dict:
    with open(FIX / "manifest.json", encoding="utf-8") as fh:
        return json.load(fh)


def _in_aoi(lon: float, lat: float) -> bool:
    return AOI[0] <= lon <= AOI[2] and AOI[1] <= lat <= AOI[3]


# --------------------------------------------------------------------------- #
# (a) committed files exist + idempotent regeneration
# --------------------------------------------------------------------------- #


def test_committed_fixtures_exist_and_load():
    """The committed fixture files exist and parse."""
    for name in RASTER_RANGES:
        p = RASTERS / name
        assert p.exists(), f"missing raster {p}"
        with rasterio.open(p) as ds:
            assert ds.count == 1

    for rel in (
        "points/coffee_points.geojson",
        "submissions/messy_submission.geojson",
        "submissions/messy_submission.csv",
        "pathology/pathology.json",
        "manifest.json",
        "README.md",
    ):
        assert (FIX / rel).exists(), f"missing fixture {rel}"

    # JSON fixtures parse.
    for rel in (
        "points/coffee_points.geojson",
        "submissions/messy_submission.geojson",
        "pathology/pathology.json",
        "manifest.json",
    ):
        with open(FIX / rel, encoding="utf-8") as fh:
            json.load(fh)


def _load_make_fixtures():
    spec = importlib.util.spec_from_file_location("make_fixtures", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_regeneration_is_idempotent(tmp_path: Path):
    """Regenerating into a temp copy reproduces byte-identical files.

    Runs make_fixtures.py as a subprocess against a temp copy of the repo (so the
    committed tree is never touched), then byte-compares the regenerated fixtures
    against the committed ones.  veritas_eudr.config is already importable via the
    installed venv; load_policy() resolves policy/ absolutely from the package
    location, so no local copy is needed.
    """
    repo_copy = tmp_path / "repo"
    (repo_copy / "scripts").mkdir(parents=True)
    (repo_copy / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo_copy / "scripts" / "make_fixtures.py")

    res = subprocess.run(
        [sys.executable, str(repo_copy / "scripts" / "make_fixtures.py")],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"regeneration failed:\n{res.stdout}\n{res.stderr}"
    assert "self-verification: OK" in (res.stdout + res.stderr)

    regen_fix = repo_copy / "tests" / "fixtures"
    compare = [
        "rasters/hansen_lossyear_aoi.tif",
        "rasters/jrc_gfc2020_aoi.tif",
        "rasters/worldcover_aoi.tif",
        "points/coffee_points.geojson",
        "submissions/messy_submission.geojson",
        "submissions/messy_submission.csv",
        "pathology/pathology.json",
        "manifest.json",
    ]
    for rel in compare:
        committed = (FIX / rel).read_bytes()
        regenerated = (regen_fix / rel).read_bytes()
        assert committed == regenerated, f"non-idempotent regeneration for {rel}"


# --------------------------------------------------------------------------- #
# (b) manifest schema
# --------------------------------------------------------------------------- #


def test_manifest_top_level_schema(manifest: dict):
    for key in ("schema_version", "generator", "seed", "synthetic", "aoi", "facts", "zones", "features"):
        assert key in manifest, f"manifest missing top-level key {key}"
    assert manifest["synthetic"] is True
    aoi = manifest["aoi"]
    assert aoi["crs"] == "EPSG:4326"
    assert (aoi["min_lon"], aoi["min_lat"], aoi["max_lon"], aoi["max_lat"]) == AOI
    # Facts are pinned and must be present + correct.
    facts = manifest["facts"]
    assert facts["deforestation_cutoff_date"] == "2020-12-31"
    assert facts["regulation_application_date"] == "2026-12-30"
    assert facts["hansen_release"] == "GFC-2025-v1.13"
    assert facts["zonal_engine"] == "exactextract"


def test_manifest_zones(manifest: dict):
    zones = manifest["zones"]
    assert set(zones) == {"Z_A", "Z_B", "Z_C", "Z_D", "Z_E", "Z_F"}
    expected_tiers = {
        "Z_A": "low",
        "Z_B": "high",
        "Z_C": "more-info-needed",
        "Z_D": "more-info-needed",
        "Z_E": "low",
        "Z_F": "more-info-needed",
    }
    for zid, z in zones.items():
        assert z["expected_risk_tier"] == expected_tiers[zid]
        assert z["jrc"] in (0, 1)
        assert 0 <= z["hansen"] <= 25
        assert z["worldcover"] in (10, 40)
        # zone bbox inside the AOI
        mnlon, mnlat, mxlon, mxlat = z["bbox"]
        assert _in_aoi(mnlon, mnlat) and _in_aoi(mxlon, mxlat)
    assert "loss_pixel_center" in zones["Z_F"]
    assert zones["Z_F"]["loss_band"] == 22


def test_manifest_features_schema(manifest: dict):
    groups = manifest["features"]
    assert set(groups) == {"points", "submission", "pathology"}
    for group_name, rows in groups.items():
        assert isinstance(rows, list) and rows
        for row in rows:
            assert "id" in row and "zone" in row and "scenario" in row and "rationale" in row
            assert row["expected_risk_tier"] in RISK_TIERS
            # geometry or wkt must be present for every vector feature
            assert ("geometry" in row) or ("wkt" in row), f"{row['id']} has no geometry/wkt"
            if group_name in ("submission", "pathology"):
                assert "expected_disposition" in row
                assert row["expected_disposition"] in DISPOSITIONS


def test_manifest_submission_covers_required_scenarios(manifest: dict):
    scenarios = {r["scenario"] for r in manifest["features"]["submission"]}
    required = {
        "clean_valid_polygon",
        "self_intersecting_bowtie",
        "lat_lon_swapped_point",
        "polygon_with_hole",
        "sliver_polygon",
        "duplicate_feature_id",
        "subpixel_loss_signal_below_threshold",
        "over_4ha_point",
        "under_4ha_point",
    }
    assert required <= scenarios, f"missing submission scenarios: {required - scenarios}"


def test_manifest_pathology_count_and_origins(manifest: dict):
    rows = manifest["features"]["pathology"]
    assert 8 <= len(rows) <= 12, f"pathology count {len(rows)} outside 8-12"
    for r in rows:
        assert r.get("origin"), f"pathology {r['id']} missing documented origin"
        assert "wkt" in r


def test_manifest_ids_unique_within_groups(manifest: dict):
    for group, rows in manifest["features"].items():
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids)), f"duplicate manifest ids in {group}: {ids}"


# --------------------------------------------------------------------------- #
# (c) rasters open at EPSG:4326 with expected ranges + zone encodings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(RASTER_RANGES))
def test_raster_crs_nodata_and_range(name: str):
    lo, hi = RASTER_RANGES[name]
    with rasterio.open(RASTERS / name) as ds:
        assert ds.crs is not None and ds.crs.to_epsg() == 4326
        assert ds.dtypes[0] == "uint8"
        assert ds.nodata == 255
        data = ds.read(1)
        valid = data[data != 255]
        assert valid.size > 0
        assert int(valid.min()) >= lo
        assert int(valid.max()) <= hi
        # AOI extent
        b = ds.bounds
        assert abs(b.left - AOI[0]) < 1e-6 and abs(b.bottom - AOI[1]) < 1e-6
        assert abs(b.right - AOI[2]) < 1e-6 and abs(b.top - AOI[3]) < 1e-6
        # size budget
        assert (RASTERS / name).stat().st_size < 1_000_000


def test_zone_centres_sample_intended_encoding(manifest: dict):
    """Sampling each zone centre matches the manifest's intended encoding."""
    zones = manifest["zones"]
    hansen = RASTERS / "hansen_lossyear_aoi.tif"
    jrc = RASTERS / "jrc_gfc2020_aoi.tif"
    wc = RASTERS / "worldcover_aoi.tif"

    def sample(path: Path, lon: float, lat: float) -> int:
        with rasterio.open(path) as ds:
            return int(list(ds.sample([(lon, lat)]))[0][0])

    for zid, z in zones.items():
        mnlon, mnlat, mxlon, mxlat = z["bbox"]
        cx, cy = (mnlon + mxlon) / 2, (mnlat + mxlat) / 2
        assert sample(jrc, cx, cy) == z["jrc"], f"{zid} JRC mismatch"
        assert sample(wc, cx, cy) == z["worldcover"], f"{zid} WorldCover mismatch"
        if zid == "Z_F":
            assert sample(hansen, cx, cy) == 0  # background no-loss at centre
            plon, plat = z["loss_pixel_center"]
            assert sample(hansen, plon, plat) == z["loss_band"]
        else:
            assert sample(hansen, cx, cy) == z["hansen"], f"{zid} Hansen mismatch"


def test_subpixel_loss_coverage_below_threshold(manifest: dict):
    """Tripwire C: the Z_F straddle plot's post-cutoff loss coverage is < 0.10."""
    exactextract = pytest.importorskip("exactextract")
    hansen = str(RASTERS / "hansen_lossyear_aoi.tif")
    zf = next(r for r in manifest["features"]["submission"] if r["id"] == "sub-subpixel-zf")
    feat = {"type": "Feature", "properties": {}, "geometry": zf["geometry"]}
    res = exactextract.exact_extract(hansen, feat, ["unique", "frac"], output="pandas").iloc[0]
    cov = {int(u): float(f) for u, f in zip(res["unique"], res["frac"], strict=True)}
    loss_frac = cov.get(22, 0.0)
    assert 0 < loss_frac < 0.10, f"Z_F loss coverage {loss_frac} not in (0, 0.10)"


# --------------------------------------------------------------------------- #
# (d) coffee points ~50 inside the AOI
# --------------------------------------------------------------------------- #


def test_coffee_points_count_and_containment():
    with open(FIX / "points" / "coffee_points.geojson", encoding="utf-8") as fh:
        fc = json.load(fh)
    feats = fc["features"]
    assert 45 <= len(feats) <= 55, f"expected ~50 points, got {len(feats)}"
    for f in feats:
        lon, lat = f["geometry"]["coordinates"]
        assert _in_aoi(lon, lat), f"point {f['id']} outside AOI: {lon},{lat}"
        assert f["properties"]["synthetic"] is True


def test_points_note_is_honest_about_sample_earth():
    """The points file must not claim Sample Earth observations are embedded."""
    with open(FIX / "points" / "coffee_points.geojson", encoding="utf-8") as fh:
        fc = json.load(fh)
    note = fc["_note"].lower()
    assert "synthetic" in note
    assert "not real sample earth" in note


# --------------------------------------------------------------------------- #
# CSV variant idiosyncrasies
# --------------------------------------------------------------------------- #


def test_csv_has_bom_and_comma_decimal_row():
    raw = (FIX / "submissions" / "messy_submission.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV missing UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # at least one data row uses a comma decimal separator inside a quoted field
    assert any('"' in ln and "," in ln for ln in lines[1:]), "no comma-decimal locale row found"
    # >= 6-decimal coordinates somewhere
    import re

    assert re.search(r"\d+\.\d{6}", text), "no >=6-decimal coordinate found in CSV"
