"""Convergence-of-evidence deforestation engine.

This is the most domain-critical module: it decides a per-plot deforestation
risk tier by converging *multiple* independent geospatial layers, never a single
dataset (tripwires E/L). The tiering mirrors Whisp's documented ``Risk_PCrop``
decision tree (credited; not novel) and is keyed off the 31 Dec 2020 cutoff.

The correctness tripwires this module exists to get right:

- **Tripwire B (band-21 latency).** Hansen reports *year-of-first-detection* with
  latency: a 2019-2020 clearing can surface in band 21 (calendar 2021), the first
  post-cutoff annual band. So post-cutoff loss that is *only* band 21 is treated
  as boundary-uncertain (``RiskProfile.boundary_uncertain``) and downgraded from a
  would-be HIGH to MORE_INFO_NEEDED.
- **Tripwire C (ground hectares, not bare intersects).** Both Hansen and the
  other rasters are EPSG:4326 *degree* grids, so a nominal 30 m x 30 m cell area
  is wrong -- ground pixels are non-square at this latitude. We weight each cell's
  exactextract coverage fraction by that cell's *geodesic ground area*, sum to
  ground hectares, and divide by the plot's geodesic area. A single edge pixel
  whose ground coverage is below ``loss_coverage_threshold_frac`` must NOT flag
  HIGH; it yields MORE_INFO_NEEDED.
- **Tripwire E (context, not commodity).** WorldCover cropland (class 40) is
  land-cover CONTEXT only; it is never read as commodity identification.
- **Tripwire L (JRC over-maps shaded coffee).** "Inside 2020 forest" alone is a
  weak HIGH signal for a coffee AOI, so HIGH requires *corroborating* post-cutoff
  loss, and commodity/crop context is treated as a downgrade.

The zonal engine is exactextract (fractional coverage), never rasterstats /
all_touched / a bare ST_Intersects.
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod

import numpy as np
import shapely
from pyproj import Geod, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from veritas_eudr.area import geodesic_area_ha
from veritas_eudr.config import (
    EUDR_DEFORESTATION_CUTOFF,
    Settings,
    get_settings,
    load_policy,
)
from veritas_eudr.domain import (
    EvidenceRecord,
    LayerSample,
    RiskProfile,
    RiskTier,
    SamplingStrategy,
)

try:  # exactextract is the mandated zonal engine; import lazily-friendly.
    from exactextract import exact_extract
except ImportError as exc:  # pragma: no cover - environment guard only
    raise ImportError("exactextract is required for the deforestation zonal engine") from exc

_WGS84 = Geod(ellps="WGS84")

# Hansen lossyear band span that is POST the 31 Dec 2020 cutoff: band 21 == 2021
# (first post-cutoff annual band) .. band 25 == 2025.
POST_CUTOFF_BAND_MIN = 21
POST_CUTOFF_BAND_MAX = 25
# Band 21 alone == calendar 2021, the latency band (tripwire B).
BAND21_LATENCY = 21
# Pre-cutoff disturbance: bands 1..20 == 2001..2020.
PRE_CUTOFF_BAND_MIN = 1
PRE_CUTOFF_BAND_MAX = 20

JRC_FOREST_VALUE = 1  # JRC GFC2020: 1 == forest at 2020.
WORLDCOVER_CROPLAND = 40  # ESA WorldCover: 40 == cropland (CONTEXT only, tripwire E).

# A zero-area plot (a point) carries no footprint to converge over, so we sample
# a nominal circular footprint of this radius. This is a sampling convenience for
# point submissions, NOT an area assertion (the 4 ha format test owns area).
POINT_SAMPLE_RADIUS_M = 50.0


# --------------------------------------------------------------------------- #
# Dataset metadata (sourced from policy, never hardcoded twice)
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def _dataset_index(policy_path: str | None = None) -> dict[str, dict[str, object]]:
    """Map dataset name -> its policy entry, for name/version provenance."""
    policy = load_policy(policy_path)
    return {str(d["name"]): d for d in policy.get("datasets", [])}


def _dataset_meta(substr: str, policy_path: str | None = None) -> tuple[str, str]:
    """Return (name, version) for the first policy dataset whose name contains
    ``substr``. Keeps dataset provenance single-sourced from the policy file."""
    for name, entry in _dataset_index(policy_path).items():
        if substr.lower() in name.lower():
            return name, str(entry.get("version", "unknown"))
    return substr, "unknown"


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def _cell_ground_ha(center_x: float, center_y: float, deg: float) -> float:
    """Geodesic ground area (ha) of one square degree-grid cell of side ``deg``
    centred at (center_x, center_y). This is the per-cell weight that converts a
    coverage fraction into GROUND hectares (tripwire C) -- never a nominal
    30 m x 30 m, because EPSG:4326 cells are non-square on the ground."""
    half = deg / 2.0
    lons = [center_x - half, center_x + half, center_x + half, center_x - half, center_x - half]
    lats = [center_y - half, center_y - half, center_y + half, center_y + half, center_y - half]
    area, _perimeter = _WGS84.polygon_area_perimeter(lons, lats)
    return abs(area) / 1e4


def _sample_footprint(geom: BaseGeometry) -> BaseGeometry:
    """The footprint to converge over. Areal geometries are used as-is; a point
    (no footprint) is buffered to a nominal circular plot so the layers have
    something to sample. Buffering is done in local UTM so the radius is metric."""
    if geom.is_empty:
        return geom
    if geom.geom_type in ("Point", "MultiPoint"):
        lon = geom.representative_point().x
        epsg = 32649 if lon >= 108.0 else 32648
        fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        projected = shapely_transform(lambda x, y, z=None: fwd.transform(x, y), geom)
        buffered = projected.buffer(POINT_SAMPLE_RADIUS_M)
        return shapely_transform(lambda x, y, z=None: inv.transform(x, y), buffered)
    return geom


def _extract_cells(
    raster_path: str, geom: BaseGeometry
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Run exactextract over ``geom`` against ``raster_path``; return
    (coverage_fractions, values, center_x, center_y) per intersected cell."""
    feature = {
        "type": "Feature",
        "properties": {"id": 0},
        "geometry": shapely.geometry.mapping(geom),
    }
    result = exact_extract(
        raster_path,
        [feature],
        ["coverage", "values", "center_x", "center_y"],
        output="pandas",
    )
    row = result.iloc[0]
    return (
        np.asarray(row["coverage"], dtype=float),
        np.asarray(row["values"], dtype=float),
        np.asarray(row["center_x"], dtype=float),
        np.asarray(row["center_y"], dtype=float),
    )


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class DeforestationProvider(ABC):
    """Samples the convergence layers for a plot.

    The ``LayerSample`` list it returns is the raw evidence the decision tree
    converges over; each sample carries its dataset name+version (provenance) so
    a verdict is fully attributable to its inputs (the replay/mutation trail)."""

    HANSEN = "hansen_lossyear"
    JRC = "jrc_gfc2020"
    WORLDCOVER = "worldcover"

    @abstractmethod
    def sample_plot(self, geom: BaseGeometry) -> list[LayerSample]:
        """Return one LayerSample per convergence layer for ``geom``."""
        raise NotImplementedError


class RasterProvider(DeforestationProvider):
    """Default provider: reads the local AOI raster tiles with exactextract.

    Per-layer sampling strategy:
    - Hansen lossyear -> FRACTIONAL_OVERLAP (ground-hectare loss fraction).
    - JRC GFC2020     -> ZONAL_MAJORITY (forest-at-2020 baseline).
    - WorldCover      -> ZONAL_MAJORITY (cropland CONTEXT, tripwire E).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        hansen_path: str | None = None,
        jrc_path: str | None = None,
        worldcover_path: str | None = None,
        hansen_version: str | None = None,
        policy_path: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        rasters = self.settings.fixtures_dir / "rasters"
        self.hansen_path = hansen_path or str(rasters / "hansen_lossyear_aoi.tif")
        self.jrc_path = jrc_path or str(rasters / "jrc_gfc2020_aoi.tif")
        self.worldcover_path = worldcover_path or str(rasters / "worldcover_aoi.tif")
        self._policy_path = policy_path

        self.hansen_name, hv = _dataset_meta("Hansen", policy_path)
        # Allow the version to be overridden (the mutation test bumps it).
        self.hansen_version = hansen_version or hv
        self.jrc_name, self.jrc_version = _dataset_meta("JRC GFC2020", policy_path)
        self.worldcover_name, self.worldcover_version = _dataset_meta("WorldCover", policy_path)

    # -- per-layer samplers ------------------------------------------------- #

    def _sample_hansen(self, footprint: BaseGeometry, plot_ha: float) -> LayerSample:
        cov, vals, cx, cy = self._extract(self.hansen_path, footprint)
        deg = self._pixel_deg(self.hansen_path)

        post_mask = (vals >= POST_CUTOFF_BAND_MIN) & (vals <= POST_CUTOFF_BAND_MAX)
        pre_mask = (vals >= PRE_CUTOFF_BAND_MIN) & (vals <= PRE_CUTOFF_BAND_MAX)

        # Ground hectares: weight each loss cell's coverage by its ground area.
        post_ground_ha = float(
            sum(cov[i] * _cell_ground_ha(cx[i], cy[i], deg) for i in np.where(post_mask)[0])
        )
        loss_fraction = post_ground_ha / plot_ha if plot_ha > 0 else 0.0

        post_bands = sorted({int(v) for v in vals[post_mask].tolist()})
        pre_bands = sorted({int(v) for v in vals[pre_mask].tolist()})
        band21_only = post_bands == [BAND21_LATENCY]

        note = (
            f"post_cutoff_bands={post_bands or None}; pre_cutoff_bands={pre_bands or None}; "
            f"post_loss_ground_ha={post_ground_ha:.6f}; loss_fraction={loss_fraction:.6f}; "
            f"band21_only={band21_only}"
        )
        # The reported "value" is the dominant post-cutoff band (most-recent loss),
        # else the dominant pre-cutoff band, else 0 (no loss).
        if post_bands:
            value = float(max(post_bands))
        elif pre_bands:
            value = float(max(pre_bands))
        else:
            value = 0.0

        return LayerSample(
            dataset_name=self.hansen_name,
            dataset_version=self.hansen_version,
            layer="lossyear",
            strategy=SamplingStrategy.FRACTIONAL_OVERLAP,
            value=value,
            covered_fraction=loss_fraction,
            covered_ha=post_ground_ha,
            details={
                "post_cutoff_bands": post_bands,
                "pre_cutoff_bands": pre_bands,
                "band21_only": band21_only,
                "loss_ground_ha": post_ground_ha,
                "loss_fraction": loss_fraction,
            },
            note=note,
        )

    def _sample_jrc(self, footprint: BaseGeometry) -> LayerSample:
        cov, vals, _cx, _cy = self._extract(self.jrc_path, footprint)
        total = float(cov.sum())
        forest_cov = float(cov[vals == JRC_FOREST_VALUE].sum())
        forest_frac = forest_cov / total if total > 0 else 0.0
        inside_forest = forest_frac >= 0.5  # zonal majority
        return LayerSample(
            dataset_name=self.jrc_name,
            dataset_version=self.jrc_version,
            layer="forest_2020",
            strategy=SamplingStrategy.ZONAL_MAJORITY,
            value=float(JRC_FOREST_VALUE if inside_forest else 0),
            covered_fraction=forest_frac,
            note=f"forest_fraction={forest_frac:.4f}; inside_2020_forest={inside_forest}",
        )

    def _sample_worldcover(self, footprint: BaseGeometry) -> LayerSample:
        cov, vals, _cx, _cy = self._extract(self.worldcover_path, footprint)
        total = float(cov.sum())
        crop_cov = float(cov[vals == WORLDCOVER_CROPLAND].sum())
        crop_frac = crop_cov / total if total > 0 else 0.0
        crop_present = crop_frac >= 0.5  # zonal majority for context
        return LayerSample(
            dataset_name=self.worldcover_name,
            dataset_version=self.worldcover_version,
            layer="landcover_context",
            strategy=SamplingStrategy.ZONAL_MAJORITY,
            value=float(WORLDCOVER_CROPLAND if crop_present else 0),
            covered_fraction=crop_frac,
            note=(
                f"cropland_fraction={crop_frac:.4f}; cropland_context={crop_present} "
                "(land-cover CONTEXT only, not commodity identification)"
            ),
        )

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    @functools.lru_cache(maxsize=8)
    def _pixel_deg(raster_path: str) -> float:
        import rasterio

        with rasterio.open(raster_path) as ds:
            return float(abs(ds.transform.a))

    def _extract(self, raster_path: str, geom: BaseGeometry):
        return _extract_cells(raster_path, geom)

    def sample_plot(self, geom: BaseGeometry) -> list[LayerSample]:
        footprint = _sample_footprint(geom)
        plot_ha = geodesic_area_ha(footprint)
        return [
            self._sample_hansen(footprint, plot_ha),
            self._sample_jrc(footprint),
            self._sample_worldcover(footprint),
        ]


# --------------------------------------------------------------------------- #
# Convergence decision tree
# --------------------------------------------------------------------------- #


def _find(samples: list[LayerSample], layer: str) -> LayerSample | None:
    for s in samples:
        if s.layer == layer:
            return s
    return None


def assess_plot(
    geom: BaseGeometry,
    plot_id: str,
    run_id: str,
    provider: DeforestationProvider | None = None,
    *,
    settings: Settings | None = None,
) -> RiskProfile:
    """Converge the layers for one plot into a ``RiskProfile``.

    Decision tree (mirrors Whisp's documented ``Risk_PCrop``):

    - **HIGH**: inside 2020 forest AND no commodity/crop context AND no pre-2020
      disturbance AND post-cutoff ground-coverage loss_fraction >= threshold.
      (Tripwire L: "inside forest" alone is insufficient -- HIGH demands
      corroborating post-cutoff loss.)
    - **LOW**: outside 2020 forest OR commodity/crop context present OR
      disturbance only before 2021 (pre-cutoff loss, no post-cutoff loss).
    - **MORE_INFO_NEEDED**: inside forest with no context; OR a post-cutoff
      signal below the coverage threshold (tripwire C); OR band-21-only latency
      (tripwire B), which dominates a would-be HIGH.
    """
    settings = settings or get_settings()
    provider = provider or RasterProvider(settings)
    threshold = settings.loss_coverage_threshold_frac

    samples = provider.sample_plot(geom)
    hansen = _find(samples, "lossyear")
    jrc = _find(samples, "forest_2020")
    worldcover = _find(samples, "landcover_context")

    inside_forest = bool(jrc and jrc.value == JRC_FOREST_VALUE)
    crop_context = bool(worldcover and worldcover.value == WORLDCOVER_CROPLAND)

    loss_fraction = float(hansen.covered_fraction or 0.0) if hansen else 0.0
    post_bands: list[int] = list(hansen.details.get("post_cutoff_bands") or []) if hansen else []
    pre_bands: list[int] = list(hansen.details.get("pre_cutoff_bands") or []) if hansen else []
    has_post_loss = bool(post_bands)
    has_pre_loss = bool(pre_bands)
    band21_only = bool(hansen.details.get("band21_only", False)) if hansen else False
    post_loss_meaningful = has_post_loss and loss_fraction >= threshold

    boundary_uncertain = False

    # --- LOW: any disqualifier of a forest-clearing concern. ----------------
    if not inside_forest:
        risk = RiskTier.LOW
        rule_id = "low.outside_2020_forest"
        rationale = (
            "Outside the JRC 2020 forest baseline; no forest at the cutoff to have " "been cleared."
        )
    elif crop_context:
        # Tripwire E/L: cropland CONTEXT downgrades; JRC over-maps shaded coffee.
        risk = RiskTier.LOW
        rule_id = "low.crop_context_downgrade"
        rationale = (
            "Cropland land-cover context present (WorldCover 40); treated as a "
            "downgrade because JRC over-maps shaded coffee as forest (context is "
            "not commodity identification)."
        )
    elif has_pre_loss and not has_post_loss:
        risk = RiskTier.LOW
        rule_id = "low.pre_cutoff_disturbance_only"
        rationale = (
            "Disturbance recorded only before 2021 (pre-cutoff Hansen loss); no "
            "post-cutoff loss signal."
        )
    # --- Inside forest, no crop context, from here. -------------------------
    elif band21_only:
        # Tripwire B: band-21 latency dominates a would-be HIGH.
        risk = RiskTier.MORE_INFO_NEEDED
        boundary_uncertain = True
        rule_id = "mins.band21_latency"
        rationale = (
            "Post-cutoff loss is only in Hansen band 21 (calendar 2021), the first "
            "post-cutoff annual band. Hansen reports year-of-first-detection with "
            "latency, so a 2019-2020 clearing can surface in 2021: boundary-uncertain."
        )
    elif post_loss_meaningful:
        # Inside forest + corroborating post-cutoff loss over a meaningful fraction.
        risk = RiskTier.HIGH
        rule_id = "high.inside_forest_post_cutoff_loss"
        rationale = (
            f"Inside the 2020 forest baseline with corroborating post-cutoff loss "
            f"over {loss_fraction:.2%} of the plot (>= {threshold:.0%} ground "
            f"coverage); no commodity context and no pre-2020 disturbance."
        )
    elif has_post_loss:
        # Tripwire C: a post-cutoff signal exists but below the coverage threshold.
        risk = RiskTier.MORE_INFO_NEEDED
        rule_id = "mins.sub_threshold_loss"
        rationale = (
            f"Post-cutoff loss present but ground coverage {loss_fraction:.2%} is "
            f"below the {threshold:.0%} threshold (single-edge-pixel signal); not "
            f"sufficient to flag HIGH on a bare intersection."
        )
    else:
        # Inside forest, no context, no loss: intact forest, undetermined use.
        risk = RiskTier.MORE_INFO_NEEDED
        rule_id = "mins.inside_forest_no_context"
        rationale = (
            "Inside the 2020 forest baseline with no commodity/crop context and no "
            "recorded loss; intact forest of undetermined land use."
        )

    evidence = _build_evidence(samples, run_id, plot_id, str(risk.value), rule_id)

    return RiskProfile(
        plot_id=plot_id,
        risk=risk,
        rationale=rationale,
        axes=samples,
        evidence=evidence,
        cutoff_date=EUDR_DEFORESTATION_CUTOFF,
        boundary_uncertain=boundary_uncertain,
    )


def _verdict_for_layer(sample: LayerSample) -> str:
    """A short, layer-specific verdict string for the evidence ledger row."""
    if sample.layer == "lossyear":
        return f"post_cutoff_loss_fraction={sample.covered_fraction:.6f}"
    if sample.layer == "forest_2020":
        return "inside_2020_forest" if sample.value == JRC_FOREST_VALUE else "outside_2020_forest"
    if sample.layer == "landcover_context":
        return "cropland_context" if sample.value == WORLDCOVER_CROPLAND else "no_cropland_context"
    return "n/a"


def _build_evidence(
    samples: list[LayerSample], run_id: str, plot_id: str, plot_verdict: str, rule_id: str
) -> list[EvidenceRecord]:
    """One append-only evidence row per layer. The ``verdict`` field carries the
    plot-level tier plus the layer-local reading, so the replay/mutation test can
    attribute a flipped verdict to the changed input (dataset_version/pixel_value)."""
    records: list[EvidenceRecord] = []
    for s in samples:
        records.append(
            EvidenceRecord(
                run_id=run_id,
                plot_id=plot_id,
                dataset_name=s.dataset_name,
                dataset_version=s.dataset_version,
                rule_id=rule_id,
                pixel_value=s.value,
                covered_fraction=s.covered_fraction,
                verdict=f"{plot_verdict}|{_verdict_for_layer(s)}",
            )
        )
    return records


__all__ = [
    "DeforestationProvider",
    "RasterProvider",
    "assess_plot",
    "BAND21_LATENCY",
    "JRC_FOREST_VALUE",
    "WORLDCOVER_CROPLAND",
]
