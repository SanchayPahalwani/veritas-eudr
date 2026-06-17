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

from pathlib import Path

import pytest
from sqlalchemy.engine import URL, make_url

pytestmark = pytest.mark.postgis

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

# A dedicated, throwaway database the migration tests own outright. It is created
# fresh and dropped per session and NEVER overlaps the shared database every other
# postgis-marked test depends on, so this suite cannot poison that shared state.
MIGTEST_DB_NAME = "veritas_migtest"

EXPECTED_TABLES = {
    "ingestion_runs",
    "consignments",
    "plots",
    "evidence_ledger",
    "plot_results",
}


def _with_database(url: URL, database: str) -> URL:
    """Return ``url`` repointed at ``database`` on the same server."""
    return url.set(database=database)


def _drop_migtest_db(maintenance_url: URL) -> None:
    """Drop the throwaway database, terminating any lingering connections first."""
    from sqlalchemy import create_engine, text

    admin = create_engine(maintenance_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            # Terminate other backends still attached to the throwaway DB so the
            # DROP cannot be blocked by a stray connection.
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": MIGTEST_DB_NAME},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGTEST_DB_NAME}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def migrated_engine(database_url):
    """Run ``alembic upgrade head`` against a DEDICATED throwaway database.

    The shared database (``database_url``) is left exactly as found: we never touch
    its ``public`` schema. Instead we connect to the server's ``postgres``
    maintenance database with autocommit, (re)create ``veritas_migtest`` from
    scratch, run the migration against it, and yield an engine bound to it. On
    teardown we dispose the engine and drop ``veritas_migtest`` (terminating any
    remaining connections first), so repeated runs against the same persistent
    server stay deterministic.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    shared_url = make_url(database_url)
    maintenance_url = _with_database(shared_url, "postgres")
    migtest_url = _with_database(shared_url, MIGTEST_DB_NAME)

    # Fresh slate: drop any leftover throwaway DB, then create it anew. DROP/CREATE
    # DATABASE cannot run inside a transaction, so use an autocommit connection on
    # the maintenance database.
    _drop_migtest_db(maintenance_url)
    admin = create_engine(maintenance_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{MIGTEST_DB_NAME}"'))
    finally:
        admin.dispose()

    migtest_url_str = migtest_url.render_as_string(hide_password=False)

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # Pin the URL explicitly on the Config; env.py treats an explicit
    # sqlalchemy.url as highest precedence, so we never mutate process env or
    # touch the shared DB.
    cfg.set_main_option("sqlalchemy.url", migtest_url_str)

    command.upgrade(cfg, "head")

    engine = create_engine(migtest_url_str, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        _drop_migtest_db(maintenance_url)


def test_five_tables_exist(migrated_engine):
    from sqlalchemy import text

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
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
    assert any(
        "submission_hash" in n for n in ingestion_unique
    ), f"no unique index covering submission_hash; have {ingestion_unique}"
    assert any(
        "geom_hash" in n for n in plots_unique
    ), f"no unique index covering geom_hash; have {plots_unique}"


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
        row = (
            conn.execute(
                text("SELECT (fn_area_hectares(ST_GeomFromText(:wkt, 4326))).*"),
                {"wkt": wkt},
            )
            .mappings()
            .one()
        )

    geography_ha = float(row["geography_ha"])
    epsg6933_ha = float(row["epsg6933_ha"])

    # Geodesic vs equal-area cross-check must agree to within 0.1%.
    rel_delta = abs(geography_ha - epsg6933_ha) / geography_ha
    assert (
        rel_delta < 1e-3
    ), f"geography_ha={geography_ha} vs epsg6933_ha={epsg6933_ha} differ by {rel_delta:.2%}"

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
        row = (
            conn.execute(
                text("SELECT (fn_validate_plot(ST_GeomFromText(:wkt, 4326))).*"),
                {"wkt": bowtie},
            )
            .mappings()
            .one()
        )

    assert row["is_valid"] is False, row
    assert row["reason"] is not None
    assert row["repaired"] is not None, "ST_MakeValid should return a non-null geometry"

    # The repaired geometry must itself be valid.
    with migrated_engine.connect() as conn:
        repaired_valid = conn.execute(
            text("SELECT ST_IsValid((fn_validate_plot(ST_GeomFromText(:wkt, 4326))).repaired)"),
            {"wkt": bowtie},
        ).scalar_one()
    assert repaired_valid is True
