"""Shared pytest fixtures.

Test layers:
- Unit tests (the majority) use shapely/pyproj/rasterio in-process and need NO
  database -- they run anywhere, deterministically.
- Integration tests are marked ``@pytest.mark.postgis`` and are SKIPPED unless a
  Postgres URL is provided via ``VERITAS_DATABASE_URL`` (or ``DATABASE_URL``).
  CI provides a postgis service container; locally, ``docker compose`` does.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


def _database_url() -> str | None:
    return os.environ.get("VERITAS_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("no VERITAS_DATABASE_URL/DATABASE_URL -> skipping PostGIS integration test")
    return url


@pytest.fixture()
def db_session(database_url):
    """A transactional session rolled back after each test.

    Imported lazily so the unit-test layer never imports SQLAlchemy engine code.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(database_url, future=True)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
        engine.dispose()


_POSTGIS_PROBE: tuple[bool, str] | None = None


def _postgis_skip_reason() -> str | None:
    """Return a skip reason for postgis-marked tests, or None to run them.

    Probes once and caches: no DB URL -> skip; DB URL set but the PostGIS
    functions / migrations are absent -> skip with a clear, actionable reason
    instead of letting the tests fail with a raw "function st_geomfromtext does
    not exist".
    """
    global _POSTGIS_PROBE
    if _POSTGIS_PROBE is not None:
        return _POSTGIS_PROBE[1] or None

    url = _database_url()
    if not url:
        _POSTGIS_PROBE = (True, "no DATABASE_URL; PostGIS integration test skipped")
        return _POSTGIS_PROBE[1]

    from sqlalchemy import create_engine, text

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT PostGIS_Lib_Version()"))
    except Exception:
        _POSTGIS_PROBE = (
            True,
            "DB reachable but PostGIS/migrations absent; run `alembic upgrade head` first",
        )
        return _POSTGIS_PROBE[1]
    finally:
        engine.dispose()

    _POSTGIS_PROBE = (False, "")
    return None


def pytest_collection_modifyitems(config, items):
    """Auto-skip postgis-marked tests when the DB is unavailable or not migrated."""
    reason = _postgis_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "postgis" in item.keywords:
            item.add_marker(skip)
