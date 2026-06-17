"""Database layer: SQLAlchemy 2.0 ORM over PostGIS (the system of record).

Schema (the contract the ingest/api/benchmark modules build on):
- ingestion_runs   -- one row per submitted file; idempotency by submission_hash
- consignments     -- a shipment grouping of plots for one DDS
- plots            -- canonical geometry; geom_hash gives plot-level idempotency;
                      geom is geometry(Geometry,4326) with a GiST index
- evidence_ledger  -- APPEND-ONLY; the replay/mutation trail
- plot_results     -- per-run validation/area/risk JSON, for fast API reads + replay

The actual DDL (including the GiST index and the PL/pgSQL functions) is owned by
the Alembic migration + sql/functions/, not by create_all -- so reviewers see a
real migration. ``Base.metadata`` here is the single source the migration
autogenerates against.
"""

from __future__ import annotations

import functools
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_filename: Mapped[str] = mapped_column(String(512))
    source_format: Mapped[str] = mapped_column(String(32))
    n_features: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ingested")
    notes: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    plots: Mapped[list[Plot]] = relationship(back_populates="ingestion_run")


class Consignment(Base):
    __tablename__ = "consignments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_name: Mapped[str] = mapped_column(String(256))
    commodity: Mapped[str] = mapped_column(String(64), default="coffee")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    plots: Mapped[list[Plot]] = relationship(back_populates="consignment")


class Plot(Base):
    __tablename__ = "plots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id"), nullable=True
    )
    consignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("consignments.id"), nullable=True
    )
    # Plot-level idempotency: SHA-256 over the canonicalized geometry.
    geom_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_geometry_type: Mapped[str] = mapped_column(String(32))
    asserted_area_ha: Mapped[float | None] = mapped_column(Float, nullable=True)
    geom: Mapped[object] = mapped_column(Geometry(geometry_type="GEOMETRY", srid=4326))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    ingestion_run: Mapped[IngestionRun | None] = relationship(back_populates="plots")
    consignment: Mapped[Consignment | None] = relationship(back_populates="plots")
    results: Mapped[list[PlotResult]] = relationship(back_populates="plot")


class EvidenceLedger(Base):
    """APPEND-ONLY. Never UPDATE or DELETE -- a re-run inserts new rows under a
    new run_id, which is what makes the mutation test meaningful."""

    __tablename__ = "evidence_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    plot_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_name: Mapped[str] = mapped_column(String(128))
    dataset_version: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(64))
    pixel_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    covered_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlotResult(Base):
    __tablename__ = "plot_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    plot_id: Mapped[str] = mapped_column(ForeignKey("plots.id"), index=True)
    validation_report: Mapped[dict] = mapped_column(JSONB)
    area: Mapped[dict] = mapped_column(JSONB)
    risk: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    plot: Mapped[Plot] = relationship(back_populates="results")


@functools.lru_cache(maxsize=1)
def get_engine(echo: bool = False):
    return create_engine(get_settings().database_url, echo=echo, future=True)


@functools.lru_cache(maxsize=1)
def get_sessionmaker():
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
