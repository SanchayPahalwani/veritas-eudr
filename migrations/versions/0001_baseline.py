"""baseline schema: ingestion_runs, consignments, plots, evidence_ledger, plot_results

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-17

Creates the five tables exactly as declared in ``veritas_eudr.db`` (the ORM is the
contract; this migration must not drift from it). PostGIS is enabled first; the
``plots.geom`` column is ``geometry(Geometry,4326)`` with an explicit GiST index
``ix_plots_geom_gist``; ``ingestion_runs.submission_hash`` and ``plots.geom_hash``
get unique indexes. ``downgrade()`` reverses everything in dependency order.
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostGIS must exist before any geometry column / spatial type is referenced.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

    # ingestion_runs -- one row per submitted file; idempotency by submission_hash.
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_hash", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("source_format", sa.String(length=32), nullable=False),
        sa.Column("n_features", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique index on submission_hash (mirrors unique=True, index=True in the ORM).
    op.create_index(
        "ix_ingestion_runs_submission_hash",
        "ingestion_runs",
        ["submission_hash"],
        unique=True,
    )

    # consignments -- a shipment grouping of plots for one DDS.
    op.create_table(
        "consignments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("operator_name", sa.String(length=256), nullable=False),
        sa.Column("commodity", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # plots -- canonical geometry; geom_hash gives plot-level idempotency; geom is
    # geometry(Geometry,4326) with a GiST index.
    op.create_table(
        "plots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=True),
        sa.Column("consignment_id", sa.String(length=64), nullable=True),
        sa.Column("geom_hash", sa.String(length=64), nullable=False),
        sa.Column("source_geometry_type", sa.String(length=32), nullable=False),
        sa.Column("asserted_area_ha", sa.Float(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="GEOMETRY",
                srid=4326,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                # We create the spatial index explicitly below (named index), so
                # suppress GeoAlchemy2's auto-created unnamed spatial index here.
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignments.id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique index on geom_hash (mirrors unique=True, index=True in the ORM).
    op.create_index("ix_plots_geom_hash", "plots", ["geom_hash"], unique=True)
    # Explicit, named GiST index on the geometry column.
    op.create_index(
        "ix_plots_geom_gist",
        "plots",
        ["geom"],
        unique=False,
        postgresql_using="gist",
    )

    # evidence_ledger -- APPEND-ONLY mutation/replay trail.
    op.create_table(
        "evidence_ledger",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("plot_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_name", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("pixel_value", sa.Float(), nullable=True),
        sa.Column("covered_fraction", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(length=64), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_ledger_run_id", "evidence_ledger", ["run_id"])
    op.create_index("ix_evidence_ledger_plot_id", "evidence_ledger", ["plot_id"])

    # plot_results -- per-run validation/area/risk JSON for fast API reads + replay.
    op.create_table(
        "plot_results",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("plot_id", sa.String(length=64), nullable=False),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("area", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plot_id"], ["plots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plot_results_run_id", "plot_results", ["run_id"])
    op.create_index("ix_plot_results_plot_id", "plot_results", ["plot_id"])


def downgrade() -> None:
    # Reverse order: drop dependents before their referenced tables. Indexes are
    # dropped with their owning table by DROP TABLE, but we drop the explicit ones
    # first for clarity and to be robust if a table drop is altered later.
    op.drop_index("ix_plot_results_plot_id", table_name="plot_results")
    op.drop_index("ix_plot_results_run_id", table_name="plot_results")
    op.drop_table("plot_results")

    op.drop_index("ix_evidence_ledger_plot_id", table_name="evidence_ledger")
    op.drop_index("ix_evidence_ledger_run_id", table_name="evidence_ledger")
    op.drop_table("evidence_ledger")

    op.drop_index("ix_plots_geom_gist", table_name="plots")
    op.drop_index("ix_plots_geom_hash", table_name="plots")
    op.drop_table("plots")

    op.drop_table("consignments")

    op.drop_index("ix_ingestion_runs_submission_hash", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")

    # Leave the postgis extension in place: dropping it would also drop
    # spatial_ref_sys and any other consumers in the database. Extension lifecycle
    # is an operator decision, not a per-migration downgrade concern.
