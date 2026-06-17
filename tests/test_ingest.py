"""Tests for veritas_eudr.ingest -- parsing a messy customer farm list into
canonical, idempotent plots.

Layers:
- Parsing (GeoJSON + CSV + Excel): BOM, comma-decimal locale, >=6-decimal
  coords, WKT-in-property pathologies (geometry=null), duplicate ids.
- Canonicalization + geom_hash: winding-invariance, 6dp rounding, [lon,lat]
  order, deterministic SHA-256.
- Persistence (postgis-marked): one ingestion_run, ON CONFLICT plot insert.
  Skips cleanly without a DB.
"""

from __future__ import annotations

import hashlib

import pytest
from shapely import wkt as shp_wkt
from shapely.geometry import Point, Polygon

from veritas_eudr.ingest import (
    CanonicalFeature,
    canonical_wkt,
    geom_hash,
    ingest_submission,
    parse_submission,
)

SUBMISSIONS = "tests/fixtures/submissions"


# --------------------------------------------------------------------------- #
# Fixtures: resolved file paths
# --------------------------------------------------------------------------- #


@pytest.fixture()
def geojson_path(fixtures_dir):
    return fixtures_dir / "submissions" / "messy_submission.geojson"


@pytest.fixture()
def csv_path(fixtures_dir):
    return fixtures_dir / "submissions" / "messy_submission.csv"


# --------------------------------------------------------------------------- #
# Canonicalization: winding, rounding, axis order, determinism
# --------------------------------------------------------------------------- #


def test_winding_invariance_same_geom_hash():
    """A CW ring and the SAME ring wound CCW canonicalize identically -> same hash.

    This is the in-process equivalent of ST_ForcePolygonCCW: orientation is a
    presentation detail, not a difference in the plot.
    """
    cw = Polygon(
        [
            (108.0055, 12.6455),
            (108.0055, 12.6465),
            (108.0065, 12.6465),
            (108.0065, 12.6455),
            (108.0055, 12.6455),
        ]
    )
    ccw = Polygon(
        [
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
        ]
    )
    assert cw.exterior.is_ccw != ccw.exterior.is_ccw  # genuinely opposite winding
    assert geom_hash(cw) == geom_hash(ccw)
    assert canonical_wkt(cw) == canonical_wkt(ccw)


def test_ring_rotation_invariance_same_geom_hash():
    """The same polygon digitized from a different start vertex (ring rotation)
    canonicalizes identically. Defined to be invariant here."""
    a = Polygon(
        [
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
        ]
    )
    b = Polygon(
        [
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
        ]
    )
    assert geom_hash(a) == geom_hash(b)


def test_coordinates_rounded_to_six_decimals():
    """Coordinates beyond the 6dp regulatory grid are rounded in the canonical
    representation, so sub-micro jitter does not produce a new plot."""
    fine = Point(108.00530901234, 12.64463719999)
    cw = canonical_wkt(fine)
    # No coordinate token carries more than 6 decimals.
    for token in cw.replace("(", " ").replace(")", " ").replace(",", " ").split():
        if "." in token:
            assert len(token.split(".")[1]) <= 6, token
    assert "108.005309" in cw and "12.644637" in cw


def test_six_decimal_jitter_collapses_to_same_hash():
    p1 = Point(108.0053090, 12.6446370)
    p2 = Point(108.00530904, 12.64463703)  # below the 6dp grid -> same plot
    assert geom_hash(p1) == geom_hash(p2)


def test_seventh_decimal_difference_does_not_collapse_when_it_rounds_apart():
    p1 = Point(108.0053090, 12.6446370)
    p2 = Point(108.0053100, 12.6446370)  # differs at the 6th decimal -> distinct
    assert geom_hash(p1) != geom_hash(p2)


def test_canonical_wkt_is_lon_lat_order():
    """Canonical WKT keeps [lon, lat]; lon for this AOI is ~108, lat ~12.6."""
    cw = canonical_wkt(Point(108.005309, 12.644637))
    inner = cw[cw.index("(") + 1 : cw.index(")")].strip()
    lon_str, lat_str = inner.split()
    assert float(lon_str) == pytest.approx(108.005309)
    assert float(lat_str) == pytest.approx(12.644637)


def test_geom_hash_is_sha256_of_canonical_wkt():
    p = Point(108.005309, 12.644637)
    cw = canonical_wkt(p)
    assert geom_hash(p) == hashlib.sha256(cw.encode("utf-8")).hexdigest()
    assert len(geom_hash(p)) == 64


def test_geom_hash_includes_srid_distinguishes_geometry_type():
    """A point and a (tiny) polygon at the same nominal place must not collide."""
    pt = Point(108.005309, 12.644637)
    poly = Polygon(
        [
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
        ]
    )
    assert geom_hash(pt) != geom_hash(poly)


# --------------------------------------------------------------------------- #
# GeoJSON parsing
# --------------------------------------------------------------------------- #


def test_parse_geojson_returns_canonical_features(geojson_path):
    feats = parse_submission(geojson_path)
    assert feats, "expected features parsed from the messy GeoJSON"
    assert all(isinstance(f, CanonicalFeature) for f in feats)
    # Every feature carries a geom_hash and a source geometry type.
    for f in feats:
        assert f.geom_hash and len(f.geom_hash) == 64
        assert f.source_geometry_type
        assert f.external_id


def test_parse_geojson_feature_count(geojson_path):
    """The messy GeoJSON carries 9 features (8 with GeoJSON geometry + the
    geometry=null bowtie carried as WKT in a property)."""
    feats = parse_submission(geojson_path)
    assert len(feats) == 9


def test_geojson_null_geometry_wkt_property_surfaced_unrepaired(geojson_path):
    """The bowtie has geometry=null and its ring in a WKT property. It is
    surfaced as a feature with raw_wkt + the WKT geometry type, WITHOUT repair
    (validity is the validate module's job)."""
    feats = parse_submission(geojson_path)
    bowtie = [f for f in feats if f.external_id == "sub-bowtie"]
    assert len(bowtie) == 1
    bt = bowtie[0]
    assert bt.raw_wkt is not None
    assert bt.raw_wkt.upper().startswith("POLYGON")
    assert bt.source_geometry_type == "Polygon"
    # Not repaired: the self-intersection is preserved verbatim.
    parsed = shp_wkt.loads(bt.raw_wkt)
    assert not parsed.is_valid
    # A WKT-only pathology has no canonicalized shapely geometry.
    assert bt.geometry is None


def test_geojson_asserted_area_carried_when_present(geojson_path):
    feats = parse_submission(geojson_path)
    by_id = {f.external_id: f for f in feats}
    # sub-bigpoint asserts 5.2 ha; sub-smallpoint asserts 1.8 ha.
    bigpoints = [f for f in feats if f.external_id == "sub-bigpoint"]
    assert bigpoints[0].asserted_area_ha == pytest.approx(5.2)
    smallpoints = [f for f in feats if f.external_id == "sub-smallpoint"]
    assert smallpoints[0].asserted_area_ha == pytest.approx(1.8)
    # The clean polygon carries no asserted area on the point/path it lacks one.
    assert "sub-sliver" in by_id


def test_geojson_latlon_swap_preserved_not_corrected(geojson_path):
    """The lat/lon-swapped point is ingested verbatim ([12.646, 108.046]); the
    axis order is NOT auto-corrected here -- that escalation is validate's call."""
    feats = parse_submission(geojson_path)
    swap = [f for f in feats if f.external_id == "sub-latlon-swap"]
    assert len(swap) == 1
    geom = swap[0].geometry
    assert geom is not None
    # Stored lon == 12.646 (the swap), not the intended 108.046.
    assert geom.x == pytest.approx(12.646)
    assert geom.y == pytest.approx(108.046)


# --------------------------------------------------------------------------- #
# CSV parsing: BOM, comma-decimal, >=6 decimals, WKT-in-cell
# --------------------------------------------------------------------------- #


def test_parse_csv_returns_canonical_features(csv_path):
    feats = parse_submission(csv_path)
    assert feats
    assert all(isinstance(f, CanonicalFeature) for f in feats)


def test_csv_comma_decimal_row_parses_to_correct_floats(csv_path):
    """Row 1 (sub-clean-poly) uses a comma decimal separator: '108,006000' ->
    108.006, '12,646000' -> 12.646, '1,2018' -> 1.2018 ha."""
    feats = parse_submission(csv_path)
    clean = [f for f in feats if f.external_id == "sub-clean-poly"]
    assert clean, "sub-clean-poly missing from CSV parse"
    f = clean[0]
    assert f.asserted_area_ha == pytest.approx(1.2018)
    assert f.geometry is not None
    # The point lon/lat decoded from the comma-decimal cells.
    assert f.geometry.x == pytest.approx(108.006)
    assert f.geometry.y == pytest.approx(12.646)


def test_csv_bom_does_not_corrupt_first_column(csv_path):
    """A UTF-8 BOM on the header must not leak into the first feature id."""
    feats = parse_submission(csv_path)
    ids = {f.external_id for f in feats}
    assert "sub-clean-poly" in ids
    assert all(not fid.startswith("﻿") for fid in ids)


def test_csv_six_decimal_coords_preserved(csv_path):
    """sub-sliver's lon 108.026267 keeps >=6 decimals through parse +
    canonicalization."""
    feats = parse_submission(csv_path)
    sliver = [f for f in feats if f.external_id == "sub-sliver"]
    assert sliver
    assert sliver[0].geometry.x == pytest.approx(108.026267, abs=1e-6)


def test_csv_wkt_cell_surfaced_as_raw_wkt(csv_path):
    """The bowtie row carries its ring as a WKT cell -> raw_wkt, unrepaired."""
    feats = parse_submission(csv_path)
    bowtie = [f for f in feats if f.external_id == "sub-bowtie"]
    assert bowtie
    assert bowtie[0].raw_wkt is not None
    assert bowtie[0].raw_wkt.upper().startswith("POLYGON")
    assert bowtie[0].source_geometry_type == "Polygon"


# --------------------------------------------------------------------------- #
# Excel (.xlsx) parsing -- exercises the _parse_excel branch via openpyxl
# --------------------------------------------------------------------------- #


@pytest.fixture()
def xlsx_path(tmp_path):
    """Write a small .xlsx mirroring a subset of the CSV fixture schema.

    Rows chosen to cover: normal point with area, point without area, and a
    6-decimal-coordinate point -- the same shape that the CSV path produces for
    equivalent rows so both paths can be compared directly.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "lon", "lat", "geom_type", "asserted_area_ha"])
    # Row 0: clean point with asserted area (mirrors sub-bigpoint from CSV fixture).
    ws.append(["xls-bigpoint", 108.030000, 12.685000, "Point", 5.2])
    # Row 1: point without asserted area (mirrors sub-latlon-swap coords, new id).
    ws.append(["xls-noArea", 12.646000, 108.046000, "Point", None])
    # Row 2: 6-decimal lon (mirrors sub-sliver's precision requirement).
    ws.append(["xls-sliver", 108.026267, 12.646000, "Point", 0.007692])
    p = tmp_path / "submission.xlsx"
    wb.save(p)
    return p


def test_parse_xlsx_returns_canonical_features(xlsx_path):
    """parse_submission on a .xlsx returns CanonicalFeature objects with the
    expected count, correct types, and valid geom_hashes."""
    feats = parse_submission(xlsx_path)
    assert len(feats) == 3
    assert all(isinstance(f, CanonicalFeature) for f in feats)
    for f in feats:
        assert f.geom_hash and len(f.geom_hash) == 64
        assert f.source_geometry_type
        assert f.external_id


def test_parse_xlsx_ids_preserved(xlsx_path):
    """External ids written into the xlsx come through unchanged."""
    feats = parse_submission(xlsx_path)
    ids = {f.external_id for f in feats}
    assert ids == {"xls-bigpoint", "xls-noArea", "xls-sliver"}


def test_parse_xlsx_coords_are_floats(xlsx_path):
    """Numeric cells stored as native Excel numbers are parsed to float coords."""
    feats = parse_submission(xlsx_path)
    by_id = {f.external_id: f for f in feats}

    bigpoint = by_id["xls-bigpoint"]
    assert bigpoint.geometry is not None
    assert bigpoint.geometry.x == pytest.approx(108.030000)
    assert bigpoint.geometry.y == pytest.approx(12.685000)


def test_parse_xlsx_asserted_area_carried(xlsx_path):
    """Numeric area cells come through as float; absent cells come through as None."""
    feats = parse_submission(xlsx_path)
    by_id = {f.external_id: f for f in feats}

    assert by_id["xls-bigpoint"].asserted_area_ha == pytest.approx(5.2)
    assert by_id["xls-noArea"].asserted_area_ha is None
    assert by_id["xls-sliver"].asserted_area_ha == pytest.approx(0.007692)


def test_parse_xlsx_six_decimal_coords_preserved(xlsx_path):
    """A lon stored as 108.026267 (6 significant decimals) survives parse +
    canonicalization without truncation."""
    feats = parse_submission(xlsx_path)
    by_id = {f.external_id: f for f in feats}
    sliver = by_id["xls-sliver"]
    assert sliver.geometry is not None
    assert sliver.geometry.x == pytest.approx(108.026267, abs=1e-6)


def test_parse_xlsx_matches_csv_for_equivalent_rows(tmp_path):
    """The xlsx path and CSV path produce CanonicalFeatures with the same
    geom_hash for geometrically identical rows -- the two branches are
    interchangeable for clean numeric data."""
    import csv

    import openpyxl

    rows = [
        ("eq-plot-a", 108.006000, 12.646000, "Point", "1.2018"),
        ("eq-plot-b", 108.026267, 12.646000, "Point", "0.007692"),
    ]

    # Write CSV.
    csv_file = tmp_path / "eq.csv"
    with csv_file.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "lon", "lat", "geom_type", "asserted_area_ha"])
        for r in rows:
            writer.writerow(r)

    # Write xlsx with the same values as native numbers.
    xlsx_file = tmp_path / "eq.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "lon", "lat", "geom_type", "asserted_area_ha"])
    for r in rows:
        ws.append([r[0], float(r[1]), float(r[2]), r[3], float(r[4])])
    wb.save(xlsx_file)

    csv_feats = {f.external_id: f for f in parse_submission(csv_file)}
    xlsx_feats = {f.external_id: f for f in parse_submission(xlsx_file)}

    assert set(csv_feats) == set(xlsx_feats)
    for eid in csv_feats:
        assert csv_feats[eid].geom_hash == xlsx_feats[eid].geom_hash, (
            f"geom_hash mismatch for {eid!r}: "
            f"csv={csv_feats[eid].geom_hash!r} xlsx={xlsx_feats[eid].geom_hash!r}"
        )


# --------------------------------------------------------------------------- #
# Duplicate id behaviour
# --------------------------------------------------------------------------- #


def test_duplicate_id_distinct_geometry_both_retained(geojson_path):
    """The messy GeoJSON reuses id 'sub-clean-poly' for a DIFFERENT geometry
    (the dup-id trap). Distinct geometry -> distinct geom_hash -> both features
    are retained (the identity collision is surfaced, not silently dropped)."""
    feats = parse_submission(geojson_path)
    clean = [f for f in feats if f.external_id == "sub-clean-poly"]
    assert len(clean) == 2
    hashes = {f.geom_hash for f in clean}
    assert len(hashes) == 2, "two distinct geometries must yield two hashes"


def test_geom_hash_dedupe_collapses_byte_identical_features():
    """parse_submission preserves features; dedupe is BY geom_hash. Two features
    with identical canonical geometry collapse to one hash even if their ids or
    presentation winding differ."""
    a = Polygon(
        [
            (108.0055, 12.6455),
            (108.0055, 12.6465),
            (108.0065, 12.6465),
            (108.0065, 12.6455),
            (108.0055, 12.6455),
        ]
    )
    b = Polygon(
        [
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
        ]
    )
    f1 = CanonicalFeature.from_geometry("x", a, source_geometry_type="Polygon")
    f2 = CanonicalFeature.from_geometry("y", b, source_geometry_type="Polygon")
    assert f1.geom_hash == f2.geom_hash


# --------------------------------------------------------------------------- #
# Persistence (postgis-marked; skips cleanly without a DB)
# --------------------------------------------------------------------------- #


@pytest.mark.postgis
def test_ingest_persists_one_run_and_plots(db_session, geojson_path):
    from veritas_eudr.db import IngestionRun, Plot

    run = ingest_submission(geojson_path, db_session)
    db_session.flush()
    assert isinstance(run, IngestionRun)
    assert run.submission_hash and len(run.submission_hash) == 64
    n_plots = db_session.query(Plot).filter(Plot.ingestion_run_id == run.id).count()
    assert n_plots > 0
    # n_features recorded matches the parse.
    feats = parse_submission(geojson_path)
    assert run.n_features == len(feats)


@pytest.mark.postgis
def test_ingest_csv_persists(db_session, csv_path):
    from veritas_eudr.db import IngestionRun

    run = ingest_submission(csv_path, db_session)
    db_session.flush()
    assert isinstance(run, IngestionRun)
    assert run.source_format == "csv"
