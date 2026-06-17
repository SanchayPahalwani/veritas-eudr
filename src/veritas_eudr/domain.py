"""Shared domain contracts -- the single source of truth across module
boundaries (ingest -> validate -> area -> deforestation -> risk -> api).

Design rules:
- Everything that crosses a module boundary or reaches the API is defined here.
- Enum *values* are the wire format. They are stable and deliberately chosen to
  align with external contracts (e.g. RiskTier values mirror Whisp's
  ``Risk_PCrop`` tri-state; RequiredGeometryFormat mirrors the EUDR submission
  format boundary).
- Docstrings cite the regulation / correctness tripwire each type encodes, so a
  reviewer can see the domain judgment, not just the shape.

Regulatory pins (see policy/eudr_policy.yaml for CELEX + access dates):
- Regulation (EU) 2023/1115 (EUDR), as amended by Regulation (EU) 2025/2650.
- Commission Implementing Regulation (EU) 2025/1093 -> Vietnam = LOW risk.
- Deforestation cutoff: 31 December 2020 (kept DISTINCT from the application date).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #

# GeoJSON position: [lon, lat] (EPSG:4326). Order matters -- a [lat, lon] is the
# classic spreadsheet-export bug (see Disposition.NEEDS_REVIEW / RULE lat_lon_swap).
Position = list[float]


def utcnow() -> datetime:
    """Timezone-aware UTC now (never naive)."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Enums (wire format)
# --------------------------------------------------------------------------- #


class Disposition(StrEnum):
    """What the system did with a plot/finding.

    The judgment this enum encodes is what NOT to auto-fix: a coordinate the
    system cannot safely repair (a lat/lon swap, an unknown CRS) is escalated,
    never silently "corrected".
    """

    AUTO_VALID = "AUTO_VALID"  # passed as-is, no change
    AUTO_FIXED = "AUTO_FIXED"  # safely repaired (e.g. ST_MakeValid, area unchanged)
    NEEDS_REVIEW = "NEEDS_REVIEW"  # a human must decide; do not auto-repair


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RiskTier(StrEnum):
    """Convergence-of-evidence verdict. Values map ONE-TO-ONE to Whisp's
    ``Risk_PCrop`` tri-state (Low / High / More info needed) so the self-grading
    diff is a clean 3x3 confusion matrix. This tiering mirrors Whisp's documented
    decision tree and credits Whisp/FDaP -- it is not presented as novel.
    """

    LOW = "low"
    HIGH = "high"
    MORE_INFO_NEEDED = "more-info-needed"


class RequiredGeometryFormat(StrEnum):
    """EUDR Art. 9(1)(d) geolocation SUBMISSION-FORMAT boundary (not a
    compliance pass/fail): plots < 4 ha may submit a single point; plots >= 4 ha
    must submit a polygon of the perimeter (cattle excepted)."""

    POINT = "point"
    POLYGON = "polygon"


class LegalityStatus(StrEnum):
    """Art. 2 legality (eight documentary categories) is NOT derivable from
    public geospatial rasters. The only reachable state is NOT_ASSESSED -- we
    model the gap honestly rather than fake a legality finding."""

    NOT_ASSESSED = "NOT_ASSESSED"


class DueDiligencePath(StrEnum):
    """Three distinct EUDR paths. Do not conflate the latter two.

    - FULL_DD: standard/high-risk AOIs (Art. 8-11).
    - SIMPLIFIED_DD: Art. 13 low-risk -- skips Art. 10 risk assessment and Art. 11
      mitigation, but STILL produces a per-shipment DDS (low-risk != no diligence).
    - MICRO_SMALL_PRIMARY_ONE_TIME: 2025/2650 -- micro/small *primary* operators in
      low-risk countries file a one-time declaration + reusable identifier.
    """

    FULL_DD = "full_dd"
    SIMPLIFIED_DD = "simplified_dd"
    MICRO_SMALL_PRIMARY_ONE_TIME = "micro_small_primary_one_time"


class CountryRiskClass(StrEnum):
    """Commission Implementing Regulation (EU) 2025/1093 benchmarking."""

    LOW = "low"
    STANDARD = "standard"
    HIGH = "high"


class SamplingStrategy(StrEnum):
    """How a raster layer was sampled for a plot -- recorded per layer so a
    reviewer can audit the choice (point vs zonal vs fractional)."""

    POINT = "point"
    ZONAL_MAJORITY = "zonal_majority"
    FRACTIONAL_OVERLAP = "fractional_overlap"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class Finding(BaseModel):
    """One rule outcome on one plot."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    disposition: Disposition
    human_reason: str = Field(..., description="Plain-language reason, reviewer-facing.")
    failing_coordinate: Position | None = Field(
        default=None, description="[lon,lat] of the offending vertex, when known."
    )
    details: dict[str, object] = Field(default_factory=dict)


# Worst-case ordering for rolling Findings up into a plot disposition.
_DISPOSITION_RANK = {
    Disposition.AUTO_VALID: 0,
    Disposition.AUTO_FIXED: 1,
    Disposition.NEEDS_REVIEW: 2,
}


class ValidationReport(BaseModel):
    """Typed report for a single plot. ``disposition`` is the worst of its
    findings: any NEEDS_REVIEW dominates; otherwise any AUTO_FIXED; else
    AUTO_VALID."""

    plot_id: str
    source_geometry_type: str
    findings: list[Finding] = Field(default_factory=list)
    repaired_geometry_wkt: str | None = None
    notes: list[str] = Field(default_factory=list)

    @computed_field  # serialized into the wire format -- the rolled-up disposition is the key output
    @property
    def disposition(self) -> Disposition:
        if not self.findings:
            return Disposition.AUTO_VALID
        worst = max(self.findings, key=lambda f: _DISPOSITION_RANK[f.disposition])
        return worst.disposition

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_review(self) -> bool:
        return self.disposition == Disposition.NEEDS_REVIEW


# --------------------------------------------------------------------------- #
# Area + submission format
# --------------------------------------------------------------------------- #


class AreaMeasurement(BaseModel):
    """Area of a plot under multiple bases.

    ``ST_Area(geom::geography)`` (geodesic on the WGS84 spheroid) is AUTHORITATIVE
    -- it matches Whisp's GEE ``.area()`` basis. EPSG:6933 (EASE-Grid 2.0, equal
    area) is the cross-check; we report the *measured* delta. Web Mercator and
    planar 4326 figures exist only to demonstrate why they are WRONG (negative
    tests), never as the authority. See correctness tripwire A / negative-area test.
    """

    measured_area_ha: float = Field(..., description="ST_Area(geography)/1e4 -- authoritative.")
    area_ha_ease6933: float = Field(..., description="Equal-area cross-check.")
    area_ha_local_utm: float | None = Field(
        default=None,
        description="Per-plot local UTM (32648/32649); shape-faithful, not area authority.",
    )
    area_ha_webmercator: float | None = Field(
        default=None, description="EPSG:3857 -- WRONG (sec^2(lat) inflation); demonstration only."
    )
    delta_6933_pct: float = Field(..., description="100*(6933-geography)/geography.")
    delta_webmercator_pct: float | None = None
    required_geometry_format: RequiredGeometryFormat
    borderline: bool = Field(
        ..., description="Measured area within tolerance band of the 4 ha format boundary."
    )
    area_authority: str = "ST_Area(geom::geography)"


# --------------------------------------------------------------------------- #
# Deforestation / evidence
# --------------------------------------------------------------------------- #


class LayerSample(BaseModel):
    """One dataset layer sampled against one plot. The unit of convergence."""

    dataset_name: str
    dataset_version: str
    layer: str
    strategy: SamplingStrategy
    value: float | None = Field(default=None, description="Point value or majority class.")
    covered_fraction: float | None = Field(
        default=None, description="Sum of exactextract coverage fractions (0-1) of matching pixels."
    )
    covered_ha: float | None = Field(
        default=None,
        description="covered_fraction converted to GROUND hectares (per-pixel area weighted).",
    )
    details: dict[str, object] = Field(
        default_factory=dict,
        description="Structured layer-specific data the convergence step consumes "
        "(e.g. Hansen post-/pre-cutoff loss bands) -- never parsed back out of `note`.",
    )
    note: str | None = None


class EvidenceRecord(BaseModel):
    """Append-only evidence-ledger row. The replay/mutation test asserts that a
    changed input (e.g. a dataset version bump) is attributable to a changed
    verdict through exactly these rows."""

    run_id: str
    plot_id: str
    dataset_name: str
    dataset_version: str
    rule_id: str
    pixel_value: float | None = None
    covered_fraction: float | None = None
    verdict: str
    ts: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Risk
# --------------------------------------------------------------------------- #


class RiskProfile(BaseModel):
    """Per-plot convergence-of-evidence result. ``risk`` is never decided by a
    single dataset (tripwire E/L). ``whisp_risk_pcrop`` is the same value -- the
    mapping is identity, made explicit for the diff."""

    plot_id: str
    risk: RiskTier
    rationale: str
    axes: list[LayerSample] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    cutoff_date: date
    boundary_uncertain: bool = Field(
        default=False, description="e.g. Hansen lossyear band 21 latency (tripwire B)."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def whisp_risk_pcrop(self) -> str:
        # Identity map to Whisp's Risk_PCrop tri-state, surfaced for the confusion matrix.
        return {
            RiskTier.LOW: "Low",
            RiskTier.HIGH: "High",
            RiskTier.MORE_INFO_NEEDED: "More info needed",
        }[self.risk]


# --------------------------------------------------------------------------- #
# Legality + DDS
# --------------------------------------------------------------------------- #


class LegalityAssessment(BaseModel):
    """Art. 2 legality. Conjunctive with the deforestation-free test (Art. 3),
    so a NOT_ASSESSED legality means the system can NEVER emit a fully-compliant
    DDS -- only a deforestation-axis determination with a loud flag."""

    status: LegalityStatus = LegalityStatus.NOT_ASSESSED
    categories: dict[str, str] = Field(
        default_factory=lambda: {
            cat: "NOT_ASSESSED"
            for cat in (
                "land_use_rights",
                "environmental_protection",
                "forest_related",
                "third_parties_rights",
                "labour_rights",
                "human_rights",
                "fpic",
                "tax_anticorruption_trade_customs",
            )
        }
    )
    note: str = (
        "Legality (EUDR Art. 2) is not derivable from public geospatial rasters; "
        "it requires documentary due diligence outside this system's scope."
    )


class DueDiligenceStatement(BaseModel):
    """TRACES-shaped DDS. Conforms to the EUDR GeoJson File Description v1.5
    (2025-05-05) for its geometry; stamped with the policy/spec versions so a
    reviewer can see exactly which rule set produced it.

    Because legality is NOT_ASSESSED and Art. 3 is conjunctive, this is NEVER a
    complete EUDR conformity finding -- ``legality_status`` says so loudly.
    """

    model_config = ConfigDict(use_enum_values=True)

    consignment_id: str
    operator_name: str
    commodity: str = "coffee"
    plot_ids: list[str]
    geojson: dict[str, object] = Field(
        ..., description="EUDR GeoJson File Description v1.5 FeatureCollection."
    )

    deforestation_determination: RiskTier
    legality_status: LegalityStatus = LegalityStatus.NOT_ASSESSED
    compliance_complete: bool = Field(
        default=False,
        description="Always False: deforestation-axis only; legality NOT_ASSESSED (Art. 3 is conjunctive).",
    )

    due_diligence_path: DueDiligencePath
    country_risk_class: CountryRiskClass
    due_diligence_regime: str

    # Reference/verification number chain -- internal-consistency stub only.
    reference_number: str | None = None
    verification_number: str | None = None

    # Validity window: Art. 12 sets a one-year CEILING (valid for up to 1 year from
    # submission); we use a fixed 365-day window as a defensible reading of it.
    valid_from: date | None = None
    valid_until: date | None = None
    annual_review_required: bool = True

    # Provenance stamps.
    geojson_spec_version: str = "1.5"
    policy_version: str
    deforestation_cutoff_date: date
    regulation_application_date: date

    generated_at: datetime = Field(default_factory=utcnow)
