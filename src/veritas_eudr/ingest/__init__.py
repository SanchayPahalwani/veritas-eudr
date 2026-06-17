"""Ingest: parse a messy customer farm list into canonical, idempotent plots.

This module is the front door of the pipeline (ingest -> validate -> area ->
deforestation -> risk -> api). Its single responsibility is to turn whatever a
customer hands us -- a GeoJSON FeatureCollection, a CSV, or an Excel export,
each with its own export pathologies -- into a deterministic, content-addressed
set of ``CanonicalFeature`` value objects, and to persist them idempotently.

What "canonical" means here (defined explicitly and deterministically, because
the geom_hash that gives us plot-level idempotency is only as trustworthy as the
canonical form it hashes):

1. CRS is asserted EPSG:4326 with axis order [lon, lat] (GeoJSON order). We do
   NOT reproject -- inputs are already declared 4326; we assert it and stamp the
   SRID into the hash so a 4326 point can never collide with the same numbers
   read under another CRS.
2. Coordinates are rounded to 6 decimal places -- the regulatory grid (~0.11 m
   at this latitude). Sub-grid GPS jitter must not mint a new plot.
3. Polygon ring orientation is normalised to a CCW exterior (the in-process
   equivalent of ``ST_ForcePolygonCCW`` via ``shapely...orient(p, sign=1.0)``),
   and rings are rotated to a deterministic start vertex. Winding and the choice
   of start vertex are presentation details, not differences in the plot, so
   they are normalised away before hashing.
4. The canonical WKT (SRID-tagged) is hashed with SHA-256 -> ``geom_hash``.

Crucially, we do NOT repair invalid geometry here. Some submissions carry a
self-intersecting ring (a bowtie) as a WKT string in a property with GeoJSON
``geometry: null`` -- not a valid GeoJSON ring. We surface those verbatim as a
``raw_wkt`` feature and leave validity to the ``validate`` module. Canonicalising
or "fixing" them at ingest would erase the very evidence validate needs.

Idempotency is content-addressed at two levels:
- per plot, ``geom_hash`` (unique) drives ``INSERT ... ON CONFLICT DO NOTHING``;
- per submission, a SHA-256 over the sorted member geom_hashes gives a stable
  ``ingestion_runs.submission_hash`` (unique), so re-ingesting an identical file
  is a no-op: same submission_hash, zero new plots, no duplicate run row.
"""

from __future__ import annotations

import csv as _csv
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely import wkt as shp_wkt
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

# EPSG:4326 -- the declared/asserted CRS for every submission. Stamped into the
# canonical representation so the hash is CRS-aware.
CANONICAL_SRID = 4326

# The regulatory coordinate grid: 6 decimal degrees (~0.11 m at this latitude).
COORD_DECIMALS = 6
_GRID = 10**-COORD_DECIMALS

# Property/column keys that may carry a geometry as a WKT string when the
# GeoJSON geometry is null or the tabular row has no usable lon/lat.
_WKT_KEYS = ("wkt", "WKT", "geom_wkt", "geometry_wkt")
# Property/column keys that may carry an asserted plot area in hectares.
_AREA_KEYS = ("asserted_area_ha", "area_ha", "asserted_area", "area")
# Property/column keys that may carry an external feature id.
_ID_KEYS = ("id", "external_id", "plot_id", "feature_id")


# --------------------------------------------------------------------------- #
# Canonicalisation primitives
# --------------------------------------------------------------------------- #


def _round_coords(geom: BaseGeometry) -> BaseGeometry:
    """Round every coordinate to the 6dp regulatory grid, deterministically.

    Uses ``shapely.transform`` (a pure coordinate map) rather than
    ``set_precision`` so no topological snapping/reordering is introduced -- we
    want a faithful, predictable rounding, not a geometry repair.
    """
    return shapely.transform(geom, lambda coords: np.round(coords, COORD_DECIMALS))


def _canonical_ring_coords(coords: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Rotate a closed ring to a deterministic start vertex.

    A ring digitised from a different start vertex is the same plot. We drop the
    duplicated closing vertex, rotate so the lexicographically smallest
    ``(lon, lat)`` vertex is first, then re-close. Orientation is assumed already
    normalised (CCW) by the caller via ``orient``.
    """
    pts = [tuple(c) for c in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if not pts:
        return []
    start = min(range(len(pts)), key=lambda i: pts[i])
    rotated = pts[start:] + pts[:start]
    rotated.append(rotated[0])  # re-close
    return rotated


def _canonicalize(geom: BaseGeometry) -> BaseGeometry:
    """Return the canonical geometry: 6dp-rounded, CCW exterior, ring-rotated.

    Defined for the geometry types the submissions actually carry (Point,
    Polygon, MultiPolygon). Other types are rounded only -- enough for a stable
    hash without asserting a ring-rotation semantics we have not defined.
    """
    rounded = _round_coords(geom)
    gtype = rounded.geom_type

    if gtype == "Polygon":
        oriented = orient(rounded, sign=1.0)
        exterior = _canonical_ring_coords(list(oriented.exterior.coords))
        interiors = [
            # Interior rings are oriented CW by ``orient(sign=1.0)``; keep that
            # winding (already applied) and just normalise the start vertex.
            _canonical_ring_coords(list(ring.coords))
            for ring in oriented.interiors
        ]
        # Sort interior rings deterministically by their canonical start vertex.
        interiors.sort(key=lambda r: r[0] if r else (0.0, 0.0))
        from shapely.geometry import Polygon

        return Polygon(exterior, interiors)

    if gtype == "MultiPolygon":
        from shapely.geometry import MultiPolygon

        parts = [_canonicalize(part) for part in rounded.geoms]
        # Order parts deterministically by their canonical WKT so part order in
        # the input does not change the hash.
        parts.sort(key=lambda p: p.wkt)
        return MultiPolygon(parts)

    return rounded


def canonical_wkt(geom: BaseGeometry) -> str:
    """SRID-tagged canonical WKT for ``geom`` -- the exact string we hash.

    Format: ``SRID=4326;<WKT>`` with coordinates rounded to 6dp. Including the
    SRID makes the hash CRS-aware (a 4326 point cannot collide with the same
    numbers under another CRS) and mirrors the EWKT PostGIS emits.
    """
    canon = _canonicalize(geom)
    body = shp_wkt.dumps(canon, rounding_precision=COORD_DECIMALS, trim=True)
    return f"SRID={CANONICAL_SRID};{body}"


def geom_hash(geom: BaseGeometry) -> str:
    """SHA-256 (hex) over the canonical, SRID-tagged WKT of ``geom``."""
    return hashlib.sha256(canonical_wkt(geom).encode("utf-8")).hexdigest()


def _raw_wkt_hash(raw_wkt: str, source_geometry_type: str) -> str:
    """Content hash for a WKT-only feature we deliberately do NOT canonicalise.

    The bowtie et al. are invalid/pathological; canonicalising would distort the
    evidence. We hash a normalised-but-faithful key -- whitespace-collapsed WKT
    plus the declared geometry type and SRID -- so the feature is still
    content-addressed and contributes a stable member hash to the submission.
    """
    normalised = " ".join(raw_wkt.split())
    key = f"SRID={CANONICAL_SRID};RAW;{source_geometry_type};{normalised}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def submission_hash(features: Iterable[CanonicalFeature]) -> str:
    """Deterministic SHA-256 over the SORTED member geom_hashes.

    Order-independent by construction, so two parses of the same file (in any
    feature order) produce the same submission hash. This is the value that
    makes the DB re-ingest a no-op via ``ingestion_runs.submission_hash``.
    """
    member_hashes = sorted(f.geom_hash for f in features)
    return hashlib.sha256("\n".join(member_hashes).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# CanonicalFeature value object (local to this module)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CanonicalFeature:
    """A single submitted feature, canonicalised and content-addressed.

    Exactly one of ``geometry`` (a canonical shapely geometry) or ``raw_wkt`` (a
    pathology carried verbatim) is populated. ``geom_hash`` is set in both cases
    so every feature is content-addressed and idempotent.
    """

    external_id: str
    source_geometry_type: str
    geom_hash: str
    geometry: BaseGeometry | None = None
    raw_wkt: str | None = None
    asserted_area_ha: float | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def is_raw_wkt(self) -> bool:
        """True for features carried verbatim as WKT (unrepaired pathologies)."""
        return self.raw_wkt is not None and self.geometry is None

    @property
    def canonical_wkt(self) -> str | None:
        """SRID-tagged canonical WKT, or None for raw-WKT pathologies."""
        return None if self.geometry is None else canonical_wkt(self.geometry)

    @classmethod
    def from_geometry(
        cls,
        external_id: str,
        geometry: BaseGeometry,
        *,
        source_geometry_type: str | None = None,
        asserted_area_ha: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> CanonicalFeature:
        canon = _canonicalize(geometry)
        return cls(
            external_id=str(external_id),
            source_geometry_type=source_geometry_type or geometry.geom_type,
            geom_hash=geom_hash(geometry),
            geometry=canon,
            raw_wkt=None,
            asserted_area_ha=asserted_area_ha,
            properties=properties or {},
        )

    @classmethod
    def from_raw_wkt(
        cls,
        external_id: str,
        raw_wkt: str,
        *,
        source_geometry_type: str | None = None,
        asserted_area_ha: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> CanonicalFeature:
        gtype = source_geometry_type or _wkt_geometry_type(raw_wkt)
        return cls(
            external_id=str(external_id),
            source_geometry_type=gtype,
            geom_hash=_raw_wkt_hash(raw_wkt, gtype),
            geometry=None,
            raw_wkt=raw_wkt,
            asserted_area_ha=asserted_area_ha,
            properties=properties or {},
        )


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def _wkt_geometry_type(raw_wkt: str) -> str:
    """Geometry type token of a WKT string (e.g. 'Polygon'), without parsing it.

    We must NOT ``shp_wkt.loads`` here -- the pathologies are invalid by design
    (open rings, self-intersections); we only read the leading type keyword.
    """
    head = raw_wkt.strip().split("(", 1)[0].strip().upper()
    mapping = {
        "POINT": "Point",
        "MULTIPOINT": "MultiPoint",
        "LINESTRING": "LineString",
        "MULTILINESTRING": "MultiLineString",
        "POLYGON": "Polygon",
        "MULTIPOLYGON": "MultiPolygon",
        "GEOMETRYCOLLECTION": "GeometryCollection",
    }
    # Take the first token (handles "POLYGON Z", etc.).
    token = head.split()[0] if head else ""
    return mapping.get(token, token.title() or "Unknown")


def _first_present(d: dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _to_float(value: Any) -> float | None:
    """Parse a numeric cell tolerant of locale comma-decimals.

    A cell like ``"12,646"`` (comma decimal separator from a locale export) ->
    ``12.646``. Plain ``"108.006"`` -> ``108.006``. Thousands separators are not
    expected in this AOI's coordinate/area magnitudes and are not assumed.

    ``float('nan')`` (emitted by pandas for empty Excel cells when ``dtype=str``
    is used) is treated as absent and returns ``None``.
    """
    import math

    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return None if math.isnan(v) else v
    s = str(value).strip().replace("﻿", "")
    if not s:
        return None
    # Comma-decimal locale: a single comma and no dot -> treat comma as decimal.
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    return float(s)


# --------------------------------------------------------------------------- #
# GeoJSON parsing
# --------------------------------------------------------------------------- #


def _parse_geojson(text: str) -> list[CanonicalFeature]:
    text = text.lstrip("﻿")  # tolerate a BOM
    data = json.loads(text)

    if data.get("type") == "FeatureCollection":
        raw_features = data.get("features", [])
    elif data.get("type") == "Feature":
        raw_features = [data]
    else:  # a bare geometry object
        raw_features = [{"type": "Feature", "geometry": data, "properties": {}}]

    out: list[CanonicalFeature] = []
    for idx, feat in enumerate(raw_features):
        props: dict[str, Any] = dict(feat.get("properties") or {})
        external_id = (
            feat.get("id") if feat.get("id") not in (None, "") else _first_present(props, _ID_KEYS)
        )
        if external_id in (None, ""):
            external_id = f"feature-{idx}"

        area_val = _first_present(props, _AREA_KEYS)
        asserted_area_ha = _to_float(area_val) if area_val is not None else None

        geom = feat.get("geometry")
        wkt_prop = _first_present(props, _WKT_KEYS)

        if geom is not None:
            shp = shapely_shape(geom)
            out.append(
                CanonicalFeature.from_geometry(
                    external_id,
                    shp,
                    source_geometry_type=geom.get("type"),
                    asserted_area_ha=asserted_area_ha,
                    properties=props,
                )
            )
        elif wkt_prop:
            # geometry == null but a WKT string is carried in a property: a
            # non-GeoJSON-valid pathology (e.g. the bowtie). Surface verbatim.
            out.append(
                CanonicalFeature.from_raw_wkt(
                    external_id,
                    str(wkt_prop),
                    asserted_area_ha=asserted_area_ha,
                    properties=props,
                )
            )
        # A feature with neither geometry nor WKT is silently skipped: there is
        # nothing to content-address. (None occur in the fixtures.)
    return out


# --------------------------------------------------------------------------- #
# Tabular (CSV / Excel) parsing
# --------------------------------------------------------------------------- #


def _row_to_feature(row: dict[str, Any], idx: int) -> CanonicalFeature | None:
    # Normalise keys: strip BOM/whitespace, lower-case for lookup.
    norm = {(str(k).replace("﻿", "").strip() if k is not None else ""): v for k, v in row.items()}
    lower = {k.lower(): v for k, v in norm.items()}

    external_id = _first_present(lower, _ID_KEYS)
    if external_id in (None, ""):
        external_id = f"row-{idx}"

    declared_type = lower.get("geom_type") or lower.get("geometry_type")
    declared_type = str(declared_type).strip() if declared_type else None

    area_val = _first_present(lower, _AREA_KEYS)
    asserted_area_ha = _to_float(area_val) if area_val is not None else None

    wkt_cell = _first_present(lower, _WKT_KEYS)
    if wkt_cell:
        return CanonicalFeature.from_raw_wkt(
            external_id,
            str(wkt_cell),
            source_geometry_type=declared_type,
            asserted_area_ha=asserted_area_ha,
            properties=dict(norm),
        )

    lon = _to_float(lower.get("lon") if "lon" in lower else lower.get("longitude"))
    lat = _to_float(lower.get("lat") if "lat" in lower else lower.get("latitude"))
    if lon is None or lat is None:
        return None  # no usable geometry in this row

    from shapely.geometry import Point

    point = Point(lon, lat)
    return CanonicalFeature.from_geometry(
        external_id,
        point,
        # Honour the declared type (a row may stand in for a polygon with only a
        # representative point), defaulting to Point.
        source_geometry_type=declared_type or "Point",
        asserted_area_ha=asserted_area_ha,
        properties=dict(norm),
    )


def _parse_csv(text: str) -> list[CanonicalFeature]:
    text = text.lstrip("﻿")  # strip a UTF-8 BOM on the header
    reader = _csv.DictReader(StringIO(text))
    out: list[CanonicalFeature] = []
    for idx, row in enumerate(reader):
        feat = _row_to_feature(row, idx)
        if feat is not None:
            out.append(feat)
    return out


def _parse_excel(path: Path) -> list[CanonicalFeature]:
    import pandas as pd

    df = pd.read_excel(path, dtype=str)  # keep cells as text -> comma-decimal safe
    out: list[CanonicalFeature] = []
    for idx, record in enumerate(df.to_dict(orient="records")):
        feat = _row_to_feature(record, idx)
        if feat is not None:
            out.append(feat)
    return out


# --------------------------------------------------------------------------- #
# Public parse entry point
# --------------------------------------------------------------------------- #


def parse_submission(path: str | Path) -> list[CanonicalFeature]:
    """Parse a submission file into canonical features (format auto-detected).

    Accepts GeoJSON (``.geojson`` / ``.json``), CSV (``.csv``), and Excel
    (``.xlsx`` / ``.xls``). Features are returned in source order; deduplication
    is by ``geom_hash`` and is the persistence layer's responsibility (so a
    caller can still see -- and report -- duplicate ids).
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in (".geojson", ".json"):
        return _parse_geojson(p.read_text(encoding="utf-8-sig"))
    if suffix == ".csv":
        return _parse_csv(p.read_text(encoding="utf-8-sig"))
    if suffix in (".xlsx", ".xls"):
        return _parse_excel(p)

    # Fall back to content sniffing for an unknown/empty suffix.
    raw = p.read_text(encoding="utf-8-sig")
    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _parse_geojson(raw)
    return _parse_csv(raw)


def _source_format(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".geojson": "geojson",
        ".json": "geojson",
        ".csv": "csv",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
    }.get(suffix, "unknown")


# --------------------------------------------------------------------------- #
# Persistence (the DB path -- exercised by the postgis-marked tests)
# --------------------------------------------------------------------------- #


def ingest_submission(path: str | Path, session: Any) -> Any:
    """Parse and persist a submission idempotently; return the IngestionRun.

    Idempotency:
    - The submission-level hash uniquely identifies the file's content. If a run
      with that ``submission_hash`` already exists, we return it without
      inserting anything (re-ingest is a no-op).
    - Plots are inserted with ``INSERT ... ON CONFLICT (geom_hash) DO NOTHING``
      (PostgreSQL upsert), so a geometry already present from another submission
      is shared, never duplicated.

    Imported lazily so the pure-python parse/canonicalisation layer never drags
    in SQLAlchemy/PostGIS.
    """
    from geoalchemy2.shape import from_shape
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from veritas_eudr.db import IngestionRun, Plot

    p = Path(path)
    features = parse_submission(p)
    sub_hash = submission_hash(features)
    source_format = _source_format(p)

    # Idempotency gate: an identical file is a no-op -> return the existing run.
    existing = session.execute(
        select(IngestionRun).where(IngestionRun.submission_hash == sub_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    # Surface duplicate external ids in the run notes (do not silently overwrite).
    seen_ids: dict[str, int] = {}
    for f in features:
        seen_ids[f.external_id] = seen_ids.get(f.external_id, 0) + 1
    duplicate_ids = sorted(eid for eid, n in seen_ids.items() if n > 1)

    distinct_hashes = sorted({f.geom_hash for f in features})

    run = IngestionRun(
        submission_hash=sub_hash,
        source_filename=p.name,
        source_format=source_format,
        n_features=len(features),
        status="ingested",
        notes={
            "duplicate_external_ids": duplicate_ids,
            "n_distinct_geom_hashes": len(distinct_hashes),
            "n_raw_wkt_pathologies": sum(1 for f in features if f.is_raw_wkt),
        },
    )
    session.add(run)
    session.flush()  # assign run.id for the FK below

    # Build plot rows for features with a canonical (non-raw) geometry. Raw-WKT
    # pathologies are NOT inserted as plots here -- they carry no valid geom and
    # are handed to the validate module; the run notes count them.
    rows: list[dict[str, Any]] = []
    inserted_hashes: set[str] = set()
    for f in features:
        if f.geometry is None:
            continue
        if f.geom_hash in inserted_hashes:
            continue  # collapse byte-identical geometries within this file
        inserted_hashes.add(f.geom_hash)
        rows.append(
            {
                "id": f.geom_hash[:16],  # deterministic, content-derived plot id
                "external_id": f.external_id,
                "ingestion_run_id": run.id,
                "geom_hash": f.geom_hash,
                "source_geometry_type": f.source_geometry_type,
                "asserted_area_ha": f.asserted_area_ha,
                "geom": from_shape(f.geometry, srid=CANONICAL_SRID),
            }
        )

    if rows:
        stmt = pg_insert(Plot).values(rows)
        # Plot-level idempotency: a geometry already present is left untouched.
        stmt = stmt.on_conflict_do_nothing(index_elements=["geom_hash"])
        session.execute(stmt)

    return run
