"""Orchestrator: a messy submission file -> per-plot risk + a consignment DDS.

This is the spine the demo (`veritas-eudr run`) and `docker compose up` exercise.
It owns no domain logic of its own; it sequences the frozen modules in order --
ingest -> validate -> area -> deforestation -> risk -> DDS -- and writes the
durable record (plot_results + the append-only evidence_ledger).

Two entry points:

- ``process_features`` is PURE (no DB). It turns a list of ``CanonicalFeature``
  value objects into a list of ``PlotOutcome`` (validation for every feature;
  area + risk for the ones that carry a usable geometry). Raw-WKT pathologies
  (e.g. an unrepaired bowtie carried as a WKT string) are still validated -- so
  their findings surface -- but are not area/risk-assessed, because there is no
  trustworthy geometry to measure or sample.
- ``run_pipeline`` is the DB path. It ingests (idempotently), groups the plots
  into a consignment, recomputes validation/area/risk per persisted plot, writes
  one ``PlotResult`` row and the per-layer ``EvidenceLedger`` rows per plot, and
  returns the assembled (always withheld) ``DueDiligenceStatement``.

The run_id is derived from the submission hash when not supplied, so a replay of
the same input is stable and the evidence ledger is comparable across runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry

from veritas_eudr import area as area_mod
from veritas_eudr import deforestation as deforestation_mod
from veritas_eudr import validate as validate_mod
from veritas_eudr.config import Settings, get_settings
from veritas_eudr.domain import (
    AreaMeasurement,
    RiskProfile,
    ValidationReport,
)
from veritas_eudr.ingest import CanonicalFeature, ingest_submission, submission_hash
from veritas_eudr.risk import build_dds, build_eudr_geojson, validate_eudr_geojson

# A run_id is a short, stable token. Derived from the submission hash so the same
# input always replays under the same id (and the evidence ledger lines up).
_RUN_ID_LEN = 16

# Validation rule ids that mean the geometry's LOCATION is untrustworthy: the
# coordinates are out of range, axis-swapped, null-island, outside the AOI, or in
# an unconfirmed CRS. Such a plot has no trustworthy footprint to sample against
# the AOI deforestation rasters (exactextract would read outside the tiles), so
# it is validated and recorded but NOT area/risk-assessed -- the honest outcome
# is "needs review", not a fabricated risk tier on a wrong location.
_UNSAMPLEABLE_LOCATION_RULES = frozenset(
    {
        "coordinate_out_of_range",
        "lat_lon_swap",
        "null_island",
        "out_of_aoi",
        "unknown_mixed_crs",
    }
)


def _is_sampleable(report: ValidationReport) -> bool:
    """True unless a finding flags the geometry's location as untrustworthy.

    A location-credibility failure (see ``_UNSAMPLEABLE_LOCATION_RULES``) means the
    plot cannot be sampled against the AOI rasters; we record the validation report
    but do not measure or risk-assess it.
    """
    return not any(f.rule_id in _UNSAMPLEABLE_LOCATION_RULES for f in report.findings)


@dataclass(frozen=True)
class PlotOutcome:
    """The result of processing one feature.

    ``validation`` is always present (every feature is validated, including
    raw-WKT pathologies). ``area`` and ``risk`` are ``None`` for features without
    a usable geometry -- they could not be measured or sampled.
    """

    plot_id: str
    validation: ValidationReport
    area: AreaMeasurement | None = None
    risk: RiskProfile | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run_id_from_submission(features: list[CanonicalFeature]) -> str:
    """A deterministic run_id derived from the submission hash, so replay of an
    identical file produces an identical run_id (and comparable evidence)."""
    return submission_hash(features)[:_RUN_ID_LEN]


def _validation_properties(feature: CanonicalFeature) -> dict[str, Any]:
    """The property dict handed to ``validate_plot`` for one feature.

    Validate's format rule reads ``asserted_area_ha`` and its id is taken from
    ``id`` -- both live on the ``CanonicalFeature`` rather than (necessarily) in
    the raw properties, so they are merged in here without mutating the original.
    """
    props = dict(feature.properties)
    if feature.asserted_area_ha is not None:
        props.setdefault("asserted_area_ha", feature.asserted_area_ha)
    props.setdefault("id", feature.external_id)
    return props


def process_features(
    features: list[CanonicalFeature],
    run_id: str,
    *,
    provider: deforestation_mod.DeforestationProvider | None = None,
    settings: Settings | None = None,
) -> list[PlotOutcome]:
    """Validate every feature; area- and risk-assess the ones with a geometry.

    PURE: no database access. For each feature ``validate_plot`` runs against the
    canonical geometry (or, for a raw-WKT pathology, the verbatim WKT string).
    Features that carry a usable shapely geometry are additionally measured
    (``area.measure``) and risk-tiered (``deforestation.assess_plot``); raw-WKT
    pathologies get a ``ValidationReport`` only.
    """
    settings = settings or get_settings()
    # One provider for the whole batch (it caches raster handles/pixel sizes).
    if provider is None:
        provider = deforestation_mod.RasterProvider(settings)

    outcomes: list[PlotOutcome] = []
    for feature in features:
        plot_id = feature.external_id
        props = _validation_properties(feature)

        if feature.geometry is None:
            # Raw-WKT pathology: validate the verbatim WKT so its findings (e.g.
            # the self-intersection) surface, but do not measure/sample it.
            report = validate_mod.validate_plot(
                feature.raw_wkt, properties=props, settings=settings
            )
            outcomes.append(PlotOutcome(plot_id=plot_id, validation=report))
            continue

        geom = feature.geometry
        report = validate_mod.validate_plot(geom, properties=props, settings=settings)

        if not _is_sampleable(report):
            # Location is untrustworthy (out of AOI / axis swap / unknown CRS):
            # record the report, but do not measure or sample a wrong location.
            outcomes.append(PlotOutcome(plot_id=plot_id, validation=report))
            continue

        measurement = area_mod.measure(geom, settings=settings)
        profile = deforestation_mod.assess_plot(geom, plot_id, run_id, provider, settings=settings)
        outcomes.append(
            PlotOutcome(
                plot_id=plot_id,
                validation=report,
                area=measurement,
                risk=profile,
            )
        )
    return outcomes


# --------------------------------------------------------------------------- #
# DB path
# --------------------------------------------------------------------------- #


def _get_or_create_consignment(session: Any, consignment_id: str, operator_name: str) -> Any:
    """Fetch the consignment or create it (idempotent on the id PK)."""
    from veritas_eudr.db import Consignment

    consignment = session.get(Consignment, consignment_id)
    if consignment is None:
        consignment = Consignment(id=consignment_id, operator_name=operator_name)
        session.add(consignment)
        session.flush()
    return consignment


def _persisted_plots(session: Any, ingestion_run_id: int) -> list[Any]:
    """Every plot belonging to this ingestion run, in a stable id order."""
    from sqlalchemy import select

    from veritas_eudr.db import Plot

    stmt = select(Plot).where(Plot.ingestion_run_id == ingestion_run_id).order_by(Plot.id)
    return list(session.execute(stmt).scalars().all())


def run_pipeline(
    path: str | Path,
    operator_name: str,
    consignment_id: str,
    session: Any,
    run_id: str | None = None,
    *,
    provider: deforestation_mod.DeforestationProvider | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Ingest a submission and produce per-plot risk + a consignment DDS.

    Steps:
    1. ``ingest_submission`` persists the plots and the ingestion run idempotently
       (re-running the same file inserts no duplicate plots/run).
    2. Get-or-create the ``Consignment`` and link this run's plots to it.
    3. For each persisted plot recompute validation + area + risk and write a
       ``PlotResult`` row (validation_report / area / risk JSON per the shared
       contract) plus one ``EvidenceLedger`` row per ``RiskProfile`` evidence item.
    4. Assemble the (always withheld) DDS via ``risk.build_dds``.

    Returns ``{run_id, consignment_id, n_plots, outcomes, dds}``. ``outcomes`` is
    the per-plot ``PlotOutcome`` list; ``dds`` is the ``DueDiligenceStatement``.
    """
    from geoalchemy2.shape import to_shape

    from veritas_eudr.db import EvidenceLedger, PlotResult
    from veritas_eudr.ingest import parse_submission

    settings = settings or get_settings()
    path = Path(path)

    # 1. Ingest (idempotent). Derive a stable run_id from the file content if the
    #    caller did not pin one, so a replay lines up with the evidence ledger.
    run = ingest_submission(path, session)
    session.flush()
    if run_id is None:
        run_id = _run_id_from_submission(parse_submission(path))

    # 2. Consignment + link this run's persisted plots to it.
    _get_or_create_consignment(session, consignment_id, operator_name)
    plots = _persisted_plots(session, run.id)
    for plot in plots:
        plot.consignment_id = consignment_id
    session.flush()

    if provider is None:
        provider = deforestation_mod.RasterProvider(settings)

    # 3. Recompute per plot, write the durable record.
    outcomes: list[PlotOutcome] = []
    dds_plots: list[tuple[str, BaseGeometry]] = []
    profiles: list[RiskProfile] = []

    for plot in plots:
        geom = to_shape(plot.geom)
        props: dict[str, Any] = {"id": plot.external_id or plot.id}
        if plot.asserted_area_ha is not None:
            props["asserted_area_ha"] = plot.asserted_area_ha

        report = validate_mod.validate_plot(geom, properties=props, settings=settings)

        measurement: AreaMeasurement | None = None
        profile: RiskProfile | None = None
        if _is_sampleable(report):
            measurement = area_mod.measure(geom, settings=settings)
            profile = deforestation_mod.assess_plot(
                geom, plot.id, run_id, provider, settings=settings
            )

        # PlotResult per the shared contract: validation/area/risk JSON. For an
        # unsampleable plot the area/risk columns hold an empty object (the plot
        # is recorded with its validation findings but carries no measurement).
        session.add(
            PlotResult(
                run_id=run_id,
                plot_id=plot.id,
                validation_report=report.model_dump(mode="json"),
                area=measurement.model_dump(mode="json") if measurement is not None else {},
                risk=profile.model_dump(mode="json") if profile is not None else {},
            )
        )
        if profile is not None:
            # One append-only evidence row per RiskProfile evidence item.
            for record in profile.evidence:
                session.add(
                    EvidenceLedger(
                        run_id=record.run_id,
                        plot_id=record.plot_id,
                        dataset_name=record.dataset_name,
                        dataset_version=record.dataset_version,
                        rule_id=record.rule_id,
                        pixel_value=record.pixel_value,
                        covered_fraction=record.covered_fraction,
                        verdict=record.verdict,
                    )
                )

        outcomes.append(
            PlotOutcome(
                plot_id=plot.id,
                validation=report,
                area=measurement,
                risk=profile,
            )
        )
        # A plot whose geometry is not EUDR v1.5-conformant (e.g. a doughnut /
        # self-crossing ring) is precisely one validate flagged for human review;
        # it must not silently enter the consignment GeoJson submitted to TRACES.
        # It still gets a PlotResult above -- recorded, not submitted. Only a
        # sampleable, conformant plot with a risk profile flows into the DDS.
        if profile is not None and not validate_eudr_geojson(build_eudr_geojson([(plot.id, geom)])):
            dds_plots.append((plot.id, geom))
            profiles.append(profile)

    session.flush()

    # 4. Assemble the consignment DDS (always withheld; legality NOT_ASSESSED).
    dds = build_dds(consignment_id, operator_name, dds_plots, profiles, settings=settings)

    return {
        "run_id": run_id,
        "consignment_id": consignment_id,
        "n_plots": len(plots),
        "outcomes": outcomes,
        "dds": dds,
    }


__all__ = ["PlotOutcome", "process_features", "run_pipeline"]
