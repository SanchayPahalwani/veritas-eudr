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


def pytest_collection_modifyitems(config, items):
    """Auto-skip postgis-marked tests when no DB URL is configured."""
    if _database_url():
        return
    skip = pytest.mark.skip(reason="no DATABASE_URL; PostGIS integration test skipped")
    for item in items:
        if "postgis" in item.keywords:
            item.add_marker(skip)
