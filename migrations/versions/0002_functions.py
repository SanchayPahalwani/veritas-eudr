"""PL/pgSQL helper functions + version-floor assertion

Revision ID: 0002_functions
Revises: 0001_baseline
Create Date: 2026-06-17

Reads and executes the idempotent ``CREATE OR REPLACE`` SQL in ``sql/functions/``
in sorted filename order:
  00_assert_floors.sql  -- RAISE EXCEPTION unless PostGIS>=3.2 AND GEOS>=3.10
  fn_area_hectares.sql  -- geodesic + EPSG:6933 area in hectares (+ delta)
  fn_validate_plot.sql  -- ST_IsValid/Reason/Detail + ST_MakeValid(method=structure)

The .sql files are the single source of these definitions so they read as plain
SQL in review and can be re-applied (CREATE OR REPLACE) outside Alembic too.
``downgrade()`` drops the two functions; the floor assertion is a transient DO
block with nothing to drop.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_functions"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Repo root is two levels up from this file: .../migrations/versions/0002_functions.py
_SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "functions"

# Applied in this explicit order: assert version floors first (fail fast), then
# the two functions. Sorting the directory would also yield this order, but we
# pin it so a future *.sql file cannot silently jump the floor assertion.
_SQL_FILES: tuple[str, ...] = (
    "00_assert_floors.sql",
    "fn_area_hectares.sql",
    "fn_validate_plot.sql",
)


def upgrade() -> None:
    for name in _SQL_FILES:
        sql_path = _SQL_DIR / name
        sql = sql_path.read_text(encoding="utf-8")
        op.execute(sql)


def downgrade() -> None:
    # The DO block in 00_assert_floors.sql leaves no persistent object. Drop the
    # two functions by signature (matching their single geometry argument).
    op.execute("DROP FUNCTION IF EXISTS fn_validate_plot(geometry);")
    op.execute("DROP FUNCTION IF EXISTS fn_area_hectares(geometry);")
