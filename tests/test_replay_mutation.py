"""Replay / mutation test for the evidence ledger.

The append-only evidence ledger exists so that a changed *input* is attributable
to a changed *verdict*. This test synthesizes a mutated Hansen raster in tmp_path
(Z_C's band-21 latency loss rewritten to band 22, plus a bumped dataset version),
runs ``assess_plot`` once under the original provider (run_id "v1") and once under
the mutated provider (run_id "v2"), and asserts:

  1. the verdict flips (MORE_INFO_NEEDED -> HIGH), and
  2. the flip is attributable to the changed Hansen input through the evidence
     rows -- the Hansen ``dataset_version`` and ``pixel_value`` differ between the
     two runs, while the JRC / WorldCover rows are byte-for-byte unchanged.

In-process and deterministic: compares the two RiskProfiles' evidence lists.
"""

from __future__ import annotations

import shapely

from veritas_eudr.deforestation import BAND21_LATENCY, RasterProvider, assess_plot
from veritas_eudr.domain import RiskTier

# A point inside Z_C (band-21 latency, JRC forest, tree context).
Z_C_POINT = shapely.geometry.Point(108.026943, 12.648394)
MUTATED_VERSION = "GFC-9999-vMUT"


def _mutate_hansen(src_path: str, dst_path: str) -> None:
    """Copy the Hansen tile, rewriting band 21 (the latency band) to band 22.

    Band 22 == calendar 2022, a non-latency post-cutoff year, so the band-21
    latency rule no longer applies and a high-coverage inside-forest plot becomes
    a genuine HIGH.
    """
    import rasterio

    with rasterio.open(src_path) as ds:
        arr = ds.read(1)
        profile = ds.profile
    mutated = arr.copy()
    mutated[mutated == BAND21_LATENCY] = 22
    with rasterio.open(dst_path, "w", **profile) as out:
        out.write(mutated, 1)


def _hansen_row(profile):
    return next(e for e in profile.evidence if "Hansen" in e.dataset_name)


def _non_hansen_rows(profile):
    return {e.dataset_name: e for e in profile.evidence if "Hansen" not in e.dataset_name}


def test_replay_mutation_flips_verdict_and_is_attributable(tmp_path):
    mutated_path = str(tmp_path / "hansen_lossyear_mutated.tif")
    original = RasterProvider()
    _mutate_hansen(original.hansen_path, mutated_path)
    mutated = RasterProvider(hansen_path=mutated_path, hansen_version=MUTATED_VERSION)

    v1 = assess_plot(Z_C_POINT, "pt-014", "v1", original)
    v2 = assess_plot(Z_C_POINT, "pt-014", "v2", mutated)

    # 1. The verdict flips because of the mutated input.
    assert v1.risk == RiskTier.MORE_INFO_NEEDED
    assert v1.boundary_uncertain is True  # band-21 latency dominated v1
    assert v2.risk == RiskTier.HIGH
    assert v2.boundary_uncertain is False
    assert v1.risk != v2.risk

    # 2. The change is attributable to the Hansen layer through the ledger.
    h1, h2 = _hansen_row(v1), _hansen_row(v2)
    assert h1.run_id == "v1" and h2.run_id == "v2"
    # The bumped dataset version is recorded ...
    assert h1.dataset_version != h2.dataset_version
    assert h2.dataset_version == MUTATED_VERSION
    # ... and the changed pixel value (band 21 -> band 22) is recorded.
    assert h1.pixel_value == float(BAND21_LATENCY)
    assert h2.pixel_value == 22.0
    # The Hansen verdict string differs (the per-layer reading changed tier).
    assert h1.verdict != h2.verdict
    assert h1.verdict.startswith("more-info-needed")
    assert h2.verdict.startswith("high")


def test_unchanged_inputs_are_not_attributed_the_change(tmp_path):
    """Only the mutated layer's rows differ: the JRC and WorldCover evidence rows
    are identical across runs, so the flip is NOT misattributed to them."""
    mutated_path = str(tmp_path / "hansen_lossyear_mutated.tif")
    original = RasterProvider()
    _mutate_hansen(original.hansen_path, mutated_path)
    mutated = RasterProvider(hansen_path=mutated_path, hansen_version=MUTATED_VERSION)

    v1 = assess_plot(Z_C_POINT, "pt-014", "v1", original)
    v2 = assess_plot(Z_C_POINT, "pt-014", "v2", mutated)

    rows1, rows2 = _non_hansen_rows(v1), _non_hansen_rows(v2)
    assert set(rows1) == set(rows2)
    for name, r1 in rows1.items():
        r2 = rows2[name]
        # Same input -> same provenance and same reading (run_id aside).
        assert r1.dataset_version == r2.dataset_version
        assert r1.pixel_value == r2.pixel_value
        assert r1.covered_fraction == r2.covered_fraction


def test_ledger_is_append_only_under_two_run_ids(tmp_path):
    """A re-run produces NEW rows under a new run_id rather than mutating the
    originals -- the property the evidence ledger's append-only design rests on."""
    mutated_path = str(tmp_path / "hansen_lossyear_mutated.tif")
    original = RasterProvider()
    _mutate_hansen(original.hansen_path, mutated_path)
    mutated = RasterProvider(hansen_path=mutated_path, hansen_version=MUTATED_VERSION)

    v1 = assess_plot(Z_C_POINT, "pt-014", "v1", original)
    v2 = assess_plot(Z_C_POINT, "pt-014", "v2", mutated)

    # Distinct, non-overlapping run_ids; same plot; same number of layers.
    assert {e.run_id for e in v1.evidence} == {"v1"}
    assert {e.run_id for e in v2.evidence} == {"v2"}
    assert len(v1.evidence) == len(v2.evidence) == 3
    assert all(e.plot_id == "pt-014" for e in (*v1.evidence, *v2.evidence))


def test_mutation_is_deterministic(tmp_path):
    """Two independent mutated providers yield identical verdicts -- the test path
    is deterministic (no RNG, no live data)."""
    p1 = str(tmp_path / "m1.tif")
    p2 = str(tmp_path / "m2.tif")
    base = RasterProvider()
    _mutate_hansen(base.hansen_path, p1)
    _mutate_hansen(base.hansen_path, p2)
    a = assess_plot(
        Z_C_POINT, "pt-014", "v2", RasterProvider(hansen_path=p1, hansen_version=MUTATED_VERSION)
    )
    b = assess_plot(
        Z_C_POINT, "pt-014", "v2", RasterProvider(hansen_path=p2, hansen_version=MUTATED_VERSION)
    )
    assert a.risk == b.risk == RiskTier.HIGH
    assert _hansen_row(a).covered_fraction == _hansen_row(b).covered_fraction
