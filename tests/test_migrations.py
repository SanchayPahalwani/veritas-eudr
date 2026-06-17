"""Integration tests for the Alembic migrations + PL/pgSQL functions.

These are marked ``@pytest.mark.postgis`` and are SKIPPED unless a Postgres URL
is provided via ``VERITAS_DATABASE_URL`` (or ``DATABASE_URL``). They drive a real
``alembic upgrade head`` against a fresh database and then assert that:

- the five ORM tables exist (ingestion_runs, consignments, plots,
  evidence_ledger, plot_results);
- the explicit GiST index ``ix_plots_geom_gist`` on ``plots.geom`` exists;
- the unique indexes on ``ingestion_runs.submission_hash`` and ``plots.geom_hash``
  exist;
- ``fn_area_hectares`` on a ~1 milli-degree square at 12.67N (Vietnam Central
  Highlands, mid-latitude) returns geodesic geography area within 0.1% of the
  EPSG:6933 cross-check, and that value is ~1.20 ha (a 0.001-degree square at
  this latitude is ~111 m x ~108 m -> ~1.2e4 m^2);
- ``fn_validate_plot`` on a self-intersecting bowtie returns ``is_valid=false``
  and a non-null repaired geometry (``ST_MakeValid(..., 'method=structure')``).

Skipping without a DB URL is expected and handled by ``pytest_collection_modifyitems``
in ``conftest.py``; the body still uses the ``database_url`` fixture which calls
``pytest.skip`` defensively.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgis

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

EXPECTED_TABLES = {
    "ingestion_runs",
    "consignments",
    "plots",
    "evidence_ledger",
    "plot_results",
}


@pytest.fixture()
def migrated_engine(database_url):
    """Apply ``alembic upgrade head`` to a fresh schema, yield a connected engine.

    We drop the public schema (and the alembic version table) up-front so the run
    starts from nothing, then run the migration via the Alembic Python API with the
    DB URL injected so it matches the conftest URL exactly.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, future=True)

    # Fresh slate: drop and recreate the public schema. spatial_ref_sys etc. live
    # in public and are recreated by "CREATE EXTENSION postgis" in the migration.
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # Ensure env.py reads exactly this URL (it falls back to get_settings()).
    os.environ["VERITAS_DATABASE_URL"] = database_url
    cfg.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(cfg, "head")

    try:
        yield engine
    finally:
        engine.dispose()


def test_five_tables_exist(migrated_engine):
    from sqlalchemy import text

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        ).fetchall()
    present = {r[0] for r in rows}
    missing = EXPECTED_TABLES - present
    assert not missing, f"missing tables: {missing}; present: {sorted(present)}"


def test_gist_index_on_plots_geom(migrated_engine):
    from sqlalchemy import text

    with migrated_engine.connect() as conn:
        # pg_indexes is the readable view; assert the named GiST index exists and
        # that pg_class/pg_am confirm its access method is gist.
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'plots'"
            )
        ).fetchall()
        names = {r[0] for r in rows}
        assert "ix_plots_geom_gist" in names, f"indexes on plots: {sorted(names)}"

        am = conn.execute(
            text(
                "SELECT am.amname FROM pg_class c "
                "JOIN pg_index i ON i.indexrelid = c.oid "
                "JOIN pg_am am ON am.oid = c.relam "
                "WHERE c.relname = 'ix_plots_geom_gist'"
            )
        ).scalar_one()
        assert am == "gist", f"expected gist access method, got {am!r}"


def test_unique_indexes_exist(migrated_engine):
    from sqlalchemy import text

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.relname AS table_name, i.relname AS index_name, ix.indisunique "
                "FROM pg_class t "
                "JOIN pg_index ix ON ix.indrelid = t.oid "
                "JOIN pg_class i ON i.oid = ix.indexrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'public' AND ix.indisunique IS TRUE"
            )
        ).fetchall()
    unique_by_table: dict[str, set[str]] = {}
    for table_name, index_name, _ in rows:
        unique_by_table.setdefault(table_name, set()).add(index_name)

    # ingestion_runs.submission_hash and plots.geom_hash must each be unique.
    ingestion_unique = unique_by_table.get("ingestion_runs", set())
    plots_unique = unique_by_table.get("plots", set())
    assert any("submission_hash" in n for n in ingestion_unique), (
        f"no unique index covering submission_hash; have {ingestion_unique}"
    )
    assert any("geom_hash" in n for n in plots_unique), (
        f"no unique index covering geom_hash; have {plots_unique}"
    )


def test_fn_area_hectares_milli_degree_square(migrated_engine):
    from sqlalchemy import text

    # ~1 milli-degree square at 12.67N (Vietnam Central Highlands; mid-latitude).
    # A 0.001-degree square is roughly 111 m x ~108 m -> ~1.2e4 m^2 -> ~1.20 ha.
    wkt = (
        "POLYGON(("
        "108.000000 12.670000, "
        "108.001000 12.670000, "
        "108.001000 12.671000, "
        "108.000000 12.671000, "
        "108.000000 12.670000))"
    )
    with migrated_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT (fn_area_hectares(ST_GeomFromText(:wkt, 4326))).*"
            ),
            {"wkt": wkt},
        ).mappings().one()

    geography_ha = float(row["geography_ha"])
    epsg6933_ha = float(row["epsg6933_ha"])

    # Geodesic vs equal-area cross-check must agree to within 0.1%.
    rel_delta = abs(geography_ha - epsg6933_ha) / geography_ha
    assert rel_delta < 1e-3, (
        f"geography_ha={geography_ha} vs epsg6933_ha={epsg6933_ha} differ by {rel_delta:.2%}"
    )

    # And the magnitude is ~1.20 ha for a 1-milli-degree square at this latitude
    # (geodesic area cross-checked with pyproj: 12017 m^2 == 1.2017 ha).
    assert geography_ha == pytest.approx(1.2017, rel=2e-3), geography_ha


def test_fn_validate_plot_bowtie_is_repaired(migrated_engine):
    from sqlalchemy import text

    # Classic self-intersecting bowtie -> invalid; ST_MakeValid must repair it.
    bowtie = (
        "POLYGON(("
        "108.000000 12.670000, "
        "108.001000 12.671000, "
        "108.001000 12.670000, "
        "108.000000 12.671000, "
        "108.000000 12.670000))"
    )
    with migrated_engine.connect() as conn:
        row = conn.execute(
            text("SELECT (fn_validate_plot(ST_GeomFromText(:wkt, 4326))).*"),
            {"wkt": bowtie},
        ).mappings().one()

    assert row["is_valid"] is False, row
    assert row["reason"] is not None
    assert row["repaired"] is not None, "ST_MakeValid should return a non-null geometry"

    # The repaired geometry must itself be valid.
    with migrated_engine.connect() as conn:
        repaired_valid = conn.execute(
            text(
                "SELECT ST_IsValid((fn_validate_plot(ST_GeomFromText(:wkt, 4326))).repaired)"
            ),
            {"wkt": bowtie},
        ).scalar_one()
    assert repaired_valid is True
