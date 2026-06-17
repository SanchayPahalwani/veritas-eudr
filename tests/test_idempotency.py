"""Idempotency tests for veritas_eudr.ingest.

Two layers:
- Pure-python (no DB): identical input -> identical geom_hash; canonicalization
  is winding- and ring-rotation-invariant; the submission-level hash is a
  deterministic function of the sorted geom_hashes.
- Persistence (postgis-marked): re-ingesting the identical file is a no-op --
  same submission_hash, 0 new plots (ON CONFLICT DO NOTHING), no duplicate
  ingestion_run. Skips cleanly without a DB.
"""

from __future__ import annotations

import hashlib

import pytest
from shapely.geometry import Point, Polygon

from veritas_eudr.ingest import (
    CanonicalFeature,
    geom_hash,
    ingest_submission,
    parse_submission,
    submission_hash,
)


@pytest.fixture()
def geojson_path(fixtures_dir):
    return fixtures_dir / "submissions" / "messy_submission.geojson"


@pytest.fixture()
def csv_path(fixtures_dir):
    return fixtures_dir / "submissions" / "messy_submission.csv"


# --------------------------------------------------------------------------- #
# Pure-python determinism
# --------------------------------------------------------------------------- #


def test_identical_geometry_identical_hash():
    p = Polygon(
        [
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
        ]
    )
    q = Polygon(
        [
            (108.0055, 12.6455),
            (108.0065, 12.6455),
            (108.0065, 12.6465),
            (108.0055, 12.6465),
            (108.0055, 12.6455),
        ]
    )
    assert geom_hash(p) == geom_hash(q)


def test_hash_stable_across_repeated_calls():
    p = Point(108.005309, 12.644637)
    assert geom_hash(p) == geom_hash(p)


def test_winding_invariant_hash():
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
    assert geom_hash(cw) == geom_hash(ccw)


def test_ring_rotation_invariant_hash():
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


# --------------------------------------------------------------------------- #
# Submission-level hash
# --------------------------------------------------------------------------- #


def test_submission_hash_is_deterministic(geojson_path):
    feats = parse_submission(geojson_path)
    assert submission_hash(feats) == submission_hash(feats)


def test_submission_hash_order_independent():
    """Re-ordering the feature list must not change the submission hash (it is
    defined over the SORTED member geom_hashes)."""
    fa = CanonicalFeature.from_geometry("a", Point(108.01, 12.65), source_geometry_type="Point")
    fb = CanonicalFeature.from_geometry("b", Point(108.02, 12.66), source_geometry_type="Point")
    assert submission_hash([fa, fb]) == submission_hash([fb, fa])


def test_submission_hash_is_sha256_of_sorted_member_hashes():
    fa = CanonicalFeature.from_geometry("a", Point(108.01, 12.65), source_geometry_type="Point")
    fb = CanonicalFeature.from_geometry("b", Point(108.02, 12.66), source_geometry_type="Point")
    expected = hashlib.sha256(
        "\n".join(sorted([fa.geom_hash, fb.geom_hash])).encode("utf-8")
    ).hexdigest()
    assert submission_hash([fa, fb]) == expected
    assert len(submission_hash([fa, fb])) == 64


def test_submission_hash_changes_when_a_feature_changes():
    fa = CanonicalFeature.from_geometry("a", Point(108.01, 12.65), source_geometry_type="Point")
    fb = CanonicalFeature.from_geometry("b", Point(108.02, 12.66), source_geometry_type="Point")
    fc = CanonicalFeature.from_geometry("c", Point(108.03, 12.67), source_geometry_type="Point")
    assert submission_hash([fa, fb]) != submission_hash([fa, fc])


def test_submission_hash_includes_wkt_only_features(geojson_path):
    """A WKT-only pathology (geometry=null bowtie) still has a geom_hash and so
    contributes to the submission hash -- it is not silently dropped."""
    feats = parse_submission(geojson_path)
    with_bowtie = submission_hash(feats)
    without_bowtie = submission_hash([f for f in feats if f.external_id != "sub-bowtie"])
    assert with_bowtie != without_bowtie


def test_reparse_same_file_same_submission_hash(geojson_path):
    """Parsing the identical file twice yields the identical submission hash --
    the pure-python half of idempotency."""
    h1 = submission_hash(parse_submission(geojson_path))
    h2 = submission_hash(parse_submission(geojson_path))
    assert h1 == h2


# --------------------------------------------------------------------------- #
# WKT-only feature hashing
# --------------------------------------------------------------------------- #


def test_wkt_only_feature_hash_is_stable():
    raw = "POLYGON((108.015400 12.645400, 108.016600 12.646600, 108.016600 12.645400, 108.015400 12.646600, 108.015400 12.645400))"
    f1 = CanonicalFeature.from_raw_wkt("sub-bowtie", raw, source_geometry_type="Polygon")
    f2 = CanonicalFeature.from_raw_wkt("sub-bowtie", raw, source_geometry_type="Polygon")
    assert f1.geom_hash == f2.geom_hash
    assert f1.geometry is None and f1.raw_wkt == raw


# --------------------------------------------------------------------------- #
# Persistence idempotency (postgis-marked; skips without a DB)
# --------------------------------------------------------------------------- #


@pytest.mark.postgis
def test_reingest_same_file_is_noop(db_session, geojson_path):
    from veritas_eudr.db import IngestionRun, Plot

    run1 = ingest_submission(geojson_path, db_session)
    db_session.flush()
    plots_after_first = db_session.query(Plot).count()
    runs_after_first = db_session.query(IngestionRun).count()

    run2 = ingest_submission(geojson_path, db_session)
    db_session.flush()
    plots_after_second = db_session.query(Plot).count()
    runs_after_second = db_session.query(IngestionRun).count()

    # Same submission hash; no new plots; no duplicate ingestion_run row.
    assert run1.submission_hash == run2.submission_hash
    assert plots_after_second == plots_after_first
    assert runs_after_second == runs_after_first


@pytest.mark.postgis
def test_reingest_returns_existing_run(db_session, geojson_path):
    run1 = ingest_submission(geojson_path, db_session)
    db_session.flush()
    run2 = ingest_submission(geojson_path, db_session)
    db_session.flush()
    assert run1.id == run2.id
