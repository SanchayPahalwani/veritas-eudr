"""Compliance / DDS layer on top of the deforestation determination.

This module turns per-plot ``RiskProfile`` verdicts (the deforestation axis)
into a consignment-level Due Diligence Statement (DDS), shaped for TRACES. It is
deliberately conservative about what it claims:

- **Legality is never assessed here.** EUDR Art. 2 legality is eight documentary
  categories that are NOT derivable from public geospatial rasters; the only
  reachable state is ``NOT_ASSESSED``. So ``build_legality_assessment`` always
  returns the domain default.
- **No DDS is ever fully compliant.** Art. 3 conformity is *conjunctive*
  (deforestation-free AND legal). Because legality is ``NOT_ASSESSED``, a complete
  conformity finding is unreachable: ``DueDiligenceStatement.compliance_complete``
  is always ``False`` and we make that state loud.
- **The geometry conforms to the EUDR GeoJson File Description v1.5.** Conformance
  is enforced, not assumed: a non-conformant payload raises rather than being
  silently submitted.

The TRACES submission itself is stubbed (no network) -- see ``TracesStubClient``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import date, timedelta

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from veritas_eudr.config import (
    EUDR_DEFORESTATION_CUTOFF,
    Settings,
    get_settings,
    load_policy,
    policy_version,
)
from veritas_eudr.domain import (
    CountryRiskClass,
    DueDiligencePath,
    DueDiligenceStatement,
    LegalityAssessment,
    RiskProfile,
    RiskTier,
    utcnow,
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# EUDR GeoJson File Description v1.5 conformance constants.
GEOJSON_SPEC_VERSION = "1.5"
# TRACES auto-rounds submitted coordinates to 6 decimals; we mirror that exactly
# so a downstream hash of the payload matches what TRACES stores.
COORD_DECIMALS = 6
# A ring must be closed (first == last) and carry at least four coordinate pairs
# (three distinct vertices + the closing repeat).
MIN_RING_PAIRS = 4
# v1.5 ~10,000-plot cap; enforced via a serialized-size ceiling.
MAX_GEOJSON_BYTES = 25 * 1024 * 1024

# The AOI commodity is robusta coffee; this default keeps the DDS shape complete.
DEFAULT_COMMODITY = "coffee"

# Stable module salt for the reference/verification pairing scheme. This binds a
# verification number to a SPECIFIC reference number; it is NOT a real signing
# key (a production system would use a managed secret). It exists only to give
# the pairing internal consistency for the stub.
_PAIRING_SALT = b"veritas-eudr.risk.reference-verification.v1"

# Consignment risk ordering, documented: a consignment's determination is the
# worst (highest-risk) tier among its plots. HIGH dominates MORE_INFO_NEEDED,
# which dominates LOW. (A single high-risk plot taints the consignment; an
# unresolved plot keeps it from being clean.)
_RISK_ORDER = {
    RiskTier.LOW: 0,
    RiskTier.MORE_INFO_NEEDED: 1,
    RiskTier.HIGH: 2,
}


# --------------------------------------------------------------------------- #
# Legality (Art. 2) -- never assessed from rasters
# --------------------------------------------------------------------------- #


def build_legality_assessment() -> LegalityAssessment:
    """Return the Art. 2 legality assessment.

    Always ``NOT_ASSESSED`` with all eight documentary categories
    ``NOT_ASSESSED`` (the domain default): legality is not derivable from public
    geospatial rasters, so this system honestly reports the gap rather than
    fabricating a legality finding.
    """
    return LegalityAssessment()


# --------------------------------------------------------------------------- #
# Consignment-level risk
# --------------------------------------------------------------------------- #


def consignment_risk(profiles: list[RiskProfile]) -> RiskTier:
    """The consignment's deforestation determination: the highest-risk tier among
    its plots, with ordering ``HIGH > MORE_INFO_NEEDED > LOW``.

    Rationale for the ordering: HIGH means at least one plot has a corroborated
    post-cutoff deforestation concern, which taints the whole consignment;
    MORE_INFO_NEEDED means an unresolved plot that prevents a clean finding; LOW
    is reachable only when *every* plot is LOW. An empty consignment carries no
    deforestation concern and is therefore LOW.
    """
    if not profiles:
        return RiskTier.LOW
    return max((p.risk for p in profiles), key=lambda tier: _RISK_ORDER[tier])


# --------------------------------------------------------------------------- #
# EUDR GeoJson File Description v1.5
# --------------------------------------------------------------------------- #


def _round_position(position: list[float]) -> list[float]:
    """Round a [lon, lat] position to exactly ``COORD_DECIMALS`` decimals."""
    return [round(float(position[0]), COORD_DECIMALS), round(float(position[1]), COORD_DECIMALS)]


def _round_ring(ring: list[list[float]]) -> list[list[float]]:
    return [_round_position(p) for p in ring]


def _geometry_to_feature(geom: BaseGeometry, plot_id: str) -> dict:
    """One v1.5 Feature for a plot. Polygon/MultiPolygon rings are rounded and
    re-closed (first == last) at 6 decimals; points are rounded as-is.

    Closing is done AFTER rounding so the repeated closing vertex is bit-identical
    to the rounded first vertex (rounding the two independently could otherwise
    leave first != last)."""
    raw = mapping(geom)
    gtype = raw["type"]

    if gtype == "Polygon":
        rings = []
        for ring in raw["coordinates"]:
            rounded = _round_ring([list(c) for c in ring])
            if rounded and rounded[0] != rounded[-1]:
                rounded.append(list(rounded[0]))
            rings.append(rounded)
        coordinates: object = rings
    elif gtype == "MultiPolygon":
        polys = []
        for poly in raw["coordinates"]:
            rings = []
            for ring in poly:
                rounded = _round_ring([list(c) for c in ring])
                if rounded and rounded[0] != rounded[-1]:
                    rounded.append(list(rounded[0]))
                rings.append(rounded)
            polys.append(rings)
        coordinates = polys
    elif gtype in ("Point", "MultiPoint", "LineString"):
        if gtype == "Point":
            coordinates = _round_position([raw["coordinates"][0], raw["coordinates"][1]])
        elif gtype == "MultiPoint":
            coordinates = [_round_position([c[0], c[1]]) for c in raw["coordinates"]]
        else:  # LineString
            coordinates = [_round_position([c[0], c[1]]) for c in raw["coordinates"]]
    else:  # pragma: no cover - defensive: unexpected geometry type
        coordinates = raw["coordinates"]

    return {
        "type": "Feature",
        "properties": {"ProducerName": "", "ProductionPlace": plot_id, "Area": None},
        "geometry": {"type": gtype, "coordinates": coordinates},
    }


def build_eudr_geojson(plots: list[tuple[str, BaseGeometry]]) -> dict:
    """Build an EUDR GeoJson File Description v1.5 FeatureCollection.

    ``plots`` is a list of ``(plot_id, geometry)`` pairs. The output is WGS84 /
    EPSG:4326 with positions ordered ``[lon, lat]``, polygon rings closed
    (first == last) with at least four coordinate pairs, and every coordinate
    rounded to EXACTLY six decimals (mirroring TRACES auto-rounding so a
    downstream hash of the payload matches).
    """
    features = [_geometry_to_feature(geom, plot_id) for plot_id, geom in plots]
    return {"type": "FeatureCollection", "features": features}


def _ring_has_six_decimals(ring: list[list[float]]) -> bool:
    for position in ring:
        for coord in position:
            # round-trip equality at 6 decimals == no finer precision present.
            if round(float(coord), COORD_DECIMALS) != float(coord):
                return False
    return True


def _ring_is_self_crossing(ring: list[list[float]]) -> bool:
    """A ring self-crosses if its boundary is not simple. Built as a LineString so
    a touching-but-not-crossing closure (the legal first==last repeat) is not
    flagged, but a figure-eight / bowtie ring is."""
    from shapely.geometry import LineString

    if len(ring) < MIN_RING_PAIRS:
        return False
    line = LineString(ring)
    return not line.is_simple


def validate_eudr_geojson(fc: dict) -> list[str]:
    """Validate a FeatureCollection against the EUDR GeoJson File Description
    v1.5. Returns a list of issue strings; an empty list means conformant.

    Per v1.5, a polygon with an interior ring (a doughnut) and a self-crossing
    ring are REJECTED / not processed by the EU system -- the documented
    workaround is to split a doughnut into two separate polygons before
    submitting. This validator flags such payloads as rejected rather than
    silently keeping the outer ring. It also enforces 6-decimal coordinates,
    closed rings (first == last), at least four coordinate pairs per ring, and a
    total serialized size below 25 MB (~the v1.5 10,000-plot cap).
    """
    issues: list[str] = []

    if fc.get("type") != "FeatureCollection":
        issues.append("rejected: top-level type must be 'FeatureCollection'")
    features = fc.get("features")
    if not isinstance(features, list):
        issues.append("rejected: 'features' must be a list")
        return issues

    for idx, feature in enumerate(features):
        geometry = feature.get("geometry") or {}
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")

        if gtype == "Polygon":
            _validate_polygon_rings(idx, coords, issues)
        elif gtype == "MultiPolygon":
            for poly in coords or []:
                _validate_polygon_rings(idx, poly, issues)
        elif gtype in ("Point", "MultiPoint", "LineString"):
            positions = [coords] if gtype == "Point" else coords
            for position in positions or []:
                if not _ring_has_six_decimals([list(position)]):
                    issues.append(
                        f"feature[{idx}]: coordinates exceed {COORD_DECIMALS} decimal places"
                    )
                    break
        else:
            issues.append(f"feature[{idx}]: unsupported geometry type {gtype!r}")

    # Serialized-size ceiling (~10,000-plot cap).
    serialized = json.dumps(fc, separators=(",", ":")).encode("utf-8")
    if len(serialized) >= MAX_GEOJSON_BYTES:
        issues.append(
            f"rejected: serialized size {len(serialized)} bytes >= {MAX_GEOJSON_BYTES} "
            f"byte cap (~10,000-plot limit)"
        )

    return issues


def _validate_polygon_rings(idx: int, rings: list, issues: list[str]) -> None:
    """Validate the rings of a single polygon (rings[0] = exterior, rest interior)."""
    if not rings:
        issues.append(f"feature[{idx}]: polygon has no rings")
        return

    if len(rings) > 1:
        issues.append(
            f"feature[{idx}]: rejected/not processed -- polygon has {len(rings) - 1} interior "
            f"ring(s); v1.5 does not process doughnuts (split into two polygons instead)"
        )

    for ring_idx, ring in enumerate(rings):
        ring = [list(p) for p in ring]
        if len(ring) < MIN_RING_PAIRS:
            issues.append(
                f"feature[{idx}] ring[{ring_idx}]: has {len(ring)} pairs; "
                f">= {MIN_RING_PAIRS} required"
            )
            continue
        if ring[0] != ring[-1]:
            issues.append(f"feature[{idx}] ring[{ring_idx}]: not closed (first != last)")
        if not _ring_has_six_decimals(ring):
            issues.append(
                f"feature[{idx}] ring[{ring_idx}]: coordinates exceed "
                f"{COORD_DECIMALS} decimal places"
            )
        if _ring_is_self_crossing(ring):
            issues.append(
                f"feature[{idx}] ring[{ring_idx}]: rejected/not processed -- self-crossing ring"
            )


# --------------------------------------------------------------------------- #
# Reference / verification number chain
# --------------------------------------------------------------------------- #


def make_reference_number(consignment_id: str, submitted_on: date | None = None) -> str:
    """A DDS reference number -- the value passed downstream / to customs.

    Shaped to look like a TRACES reference; the random suffix keeps two
    submissions of the same consignment distinct. It carries no secret.
    """
    submitted_on = submitted_on or utcnow().date()
    nonce = secrets.token_hex(4).upper()
    return f"EUDR-{submitted_on:%Y}-{consignment_id}-{nonce}"


def make_verification_number(reference: str) -> str:
    """The verification number that authenticates a SPECIFIC reference number.

    It is an HMAC-SHA256 of the reference under a stable module salt. It is
    confidential (not passed downstream) and non-functional alone: it only
    verifies when paired with the exact reference it was derived from.
    """
    digest = hmac.new(_PAIRING_SALT, reference.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:32].upper()


def verify_pairing(reference: str, verification: str) -> bool:
    """True iff ``verification`` is the verification number for ``reference``.

    Validates the PAIRING, not mere presence: a verification number derived from
    a different reference does NOT verify. Uses a constant-time comparison.
    """
    if not reference or not verification:
        return False
    expected = make_verification_number(reference)
    return hmac.compare_digest(expected, verification)


# --------------------------------------------------------------------------- #
# DDS assembly
# --------------------------------------------------------------------------- #


def _regulation_application_date(settings_policy: dict) -> date:
    return date.fromisoformat(str(settings_policy["regulation_application_date"]))


def build_dds(
    consignment_id: str,
    operator_name: str,
    plots: list[tuple[str, BaseGeometry]],
    profiles: list[RiskProfile],
    settings: Settings | None = None,
) -> DueDiligenceStatement:
    """Assemble a TRACES-shaped Due Diligence Statement for a consignment.

    The statement is always WITHHELD as a complete conformity finding:
    ``compliance_complete`` is ``False`` and ``legality_status`` is
    ``NOT_ASSESSED`` for every input (Art. 3 is conjunctive and legality is not
    derivable here). The deforestation determination is the consignment-level
    roll-up of the per-plot ``RiskProfile`` tiers.

    Raises ``ValueError`` if the assembled GeoJson is not v1.5-conformant.
    """
    settings = settings or get_settings()
    policy = load_policy()

    geojson = build_eudr_geojson(plots)
    geojson_issues = validate_eudr_geojson(geojson)
    if geojson_issues:
        raise ValueError(
            "refusing to build DDS: GeoJson is not EUDR v1.5-conformant: "
            + "; ".join(geojson_issues)
        )

    determination = consignment_risk(profiles)
    legality = build_legality_assessment()

    # Country-risk path, read from policy (never hardcoded twice).
    vn = policy["country_risk"]["VN"]
    due_diligence_path = DueDiligencePath(vn["due_diligence_path"])
    country_risk_class = CountryRiskClass(vn["class"])
    # Distinct from a complete-compliance claim: this names the *regime*, not a verdict.
    due_diligence_regime = (
        "Art. 13 simplified due diligence (low-risk country): Art. 10 risk assessment and "
        "Art. 11 mitigation skipped, but Art. 9 geolocation, DDS submission and 5-year "
        "retention still apply -- low-risk is not no-diligence."
    )

    valid_for_days = int(policy["dds_validity"]["valid_for_days"])
    valid_from = utcnow().date()
    valid_until = valid_from + timedelta(days=valid_for_days)

    reference_number = make_reference_number(consignment_id, valid_from)
    verification_number = make_verification_number(reference_number)

    return DueDiligenceStatement(
        consignment_id=consignment_id,
        operator_name=operator_name,
        commodity=DEFAULT_COMMODITY,
        plot_ids=[plot_id for plot_id, _ in plots],
        geojson=geojson,
        deforestation_determination=determination,
        legality_status=legality.status,
        compliance_complete=False,
        due_diligence_path=due_diligence_path,
        country_risk_class=country_risk_class,
        due_diligence_regime=due_diligence_regime,
        reference_number=reference_number,
        verification_number=verification_number,
        valid_from=valid_from,
        valid_until=valid_until,
        annual_review_required=bool(policy["dds_validity"]["annual_review_required"]),
        geojson_spec_version=GEOJSON_SPEC_VERSION,
        policy_version=policy_version(),
        deforestation_cutoff_date=EUDR_DEFORESTATION_CUTOFF,
        regulation_application_date=_regulation_application_date(policy),
    )


# --------------------------------------------------------------------------- #
# TRACES stub
# --------------------------------------------------------------------------- #


class TracesStubClient:
    """A deliberately offline stand-in for the EU TRACES NT submission endpoint.

    The EU does run a TRACES NT acceptance environment (the
    ``EUDRSubmissionServiceV2`` SOAP service, operator-credentialed, with no legal
    value and still-draft specifications). We stub it on purpose -- NOT because it
    does not exist -- for two reasons: acceptance access requires an operator
    registration this project does not hold, and binding to a draft specification
    now would be premature. ``submit`` therefore makes NO network call; it returns
    a stubbed acceptance response carrying the DDS reference/verification pairing.
    """

    def submit(self, dds: DueDiligenceStatement) -> dict:
        """Return a stubbed submission response. No network I/O is performed."""
        reference = dds.reference_number
        verification = dds.verification_number
        paired = bool(reference and verification and verify_pairing(reference, verification))
        return {
            "status": "ACCEPTED_STUB",
            "transport": "none (stub; no network call)",
            "service": "EUDRSubmissionServiceV2 (acceptance environment; not invoked)",
            "consignment_id": dds.consignment_id,
            "reference_number": reference,
            "verification_number": verification,
            "pairing_valid": paired,
            "compliance_complete": dds.compliance_complete,
            "submitted_at": utcnow().isoformat(),
        }


__all__ = [
    "GEOJSON_SPEC_VERSION",
    "COORD_DECIMALS",
    "MIN_RING_PAIRS",
    "MAX_GEOJSON_BYTES",
    "build_legality_assessment",
    "consignment_risk",
    "build_eudr_geojson",
    "validate_eudr_geojson",
    "make_reference_number",
    "make_verification_number",
    "verify_pairing",
    "build_dds",
    "TracesStubClient",
]
