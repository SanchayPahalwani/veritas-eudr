"""Self-grading against Whisp's ``Risk_PCrop`` tri-state.

Whisp (Openforis / Forest Data Partnership) publishes a documented decision tree
that emits a per-plot ``Risk_PCrop`` label in the tri-state ``Low`` / ``High`` /
``More info needed``. This system's :class:`~veritas_eudr.domain.RiskTier`
deliberately maps ONE-TO-ONE onto that tri-state (see
``RiskProfile.whisp_risk_pcrop``), so the only honest cross-check is a clean 3x3
confusion matrix: ours vs Whisp, label-for-label.

This module ships the REAL tooling for that comparison:

- :class:`WhispClient` -- a thin, lazy ``httpx`` wrapper that submits a GeoJSON
  geometry to the hosted Whisp REST API and reads back ``Risk_PCrop``. It is used
  ONLY on an explicit refresh; it never touches the network at import time or in
  the test suite.
- :func:`map_tier_to_whisp` / :func:`parse_whisp_pcrop` -- the identity tier<->label
  mapping and a tolerant parser that normalizes Whisp's label spelling.
- :func:`confusion_matrix` -- the diff math: a 3x3 matrix, ``n``, ``agreement_rate``
  and the explicit list of disagreements.
- :func:`load_cached_whisp` -- loads a cached ``point_id -> Risk_PCrop`` map.

CACHED-FIXTURE HONESTY MARKER
-----------------------------
The cached fixture under ``tests/fixtures/whisp/cached_pcrop.json`` is an
ILLUSTRATIVE PLACEHOLDER, NOT a real Whisp run. With the synthetic CI rasters
this system reads *painted* data, not real satellite observations, so comparing
those verdicts against a REAL Whisp run would not be a meaningful agreement
metric -- and is never presented as one anywhere. The fixture exists so the
confusion-matrix tooling is exercised reproducibly and the EVIDENCE write-up is
reproducible-not-fabricated.

To produce a genuine agreement number you must (1) fetch the real production
rasters/points via ``scripts/fetch_data.sh``, (2) re-run this system's engine
over them, and (3) refresh the Whisp side with :class:`WhispClient` (the
``--refresh`` path). Until that is done, no real-data agreement percentage is
asserted by this module or its fixtures.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from veritas_eudr.config import get_settings
from veritas_eudr.domain import RiskTier

# --------------------------------------------------------------------------- #
# Risk_PCrop tri-state
# --------------------------------------------------------------------------- #

# Whisp's three canonical ``Risk_PCrop`` labels, in their canonical spelling.
PCROP_LOW = "Low"
PCROP_HIGH = "High"
PCROP_MORE_INFO = "More info needed"

# The three labels, in a fixed order, so the confusion matrix is deterministic.
PCROP_LABELS: tuple[str, str, str] = (PCROP_LOW, PCROP_HIGH, PCROP_MORE_INFO)

# Identity mapping RiskTier -> Risk_PCrop. This is the SAME mapping surfaced by
# ``RiskProfile.whisp_risk_pcrop``; it is re-stated here as a standalone function
# so the diff tooling does not depend on having a RiskProfile in hand.
_TIER_TO_PCROP: dict[RiskTier, str] = {
    RiskTier.LOW: PCROP_LOW,
    RiskTier.HIGH: PCROP_HIGH,
    RiskTier.MORE_INFO_NEEDED: PCROP_MORE_INFO,
}

# Tolerant parse table: normalize whatever spelling/casing Whisp returns back to
# one of the three canonical labels. Keys are lowercased + whitespace-collapsed.
_PCROP_ALIASES: dict[str, str] = {
    "low": PCROP_LOW,
    "low risk": PCROP_LOW,
    "high": PCROP_HIGH,
    "high risk": PCROP_HIGH,
    "more info needed": PCROP_MORE_INFO,
    "more information needed": PCROP_MORE_INFO,
    "moreinfoneeded": PCROP_MORE_INFO,
    "more-info-needed": PCROP_MORE_INFO,
}


def map_tier_to_whisp(tier: RiskTier) -> str:
    """Map a :class:`RiskTier` to Whisp's ``Risk_PCrop`` label (identity mapping).

    This is the same value as ``RiskProfile.whisp_risk_pcrop``; both sides of the
    confusion matrix therefore share one definition of the tri-state.
    """
    return _TIER_TO_PCROP[tier]


def parse_whisp_pcrop(value: str) -> str:
    """Normalize a Whisp ``Risk_PCrop`` string to one of the three canonical labels.

    Tolerates casing and minor spelling variants (e.g. ``"low risk"``,
    ``"More information needed"``). Raises :class:`ValueError` on a value that
    cannot be mapped to the tri-state, rather than silently inventing a label.
    """
    key = " ".join(str(value).strip().lower().split())
    if key in _PCROP_ALIASES:
        return _PCROP_ALIASES[key]
    raise ValueError(f"unrecognized Risk_PCrop value: {value!r}")


# --------------------------------------------------------------------------- #
# Confusion matrix -- the real diff math
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Disagreement:
    """One row where this system and Whisp disagree.

    ``index`` is the position of the pair in the input list, so a caller can
    join it back to a point id if it kept one.
    """

    index: int
    ours: str
    whisp: str


@dataclass(frozen=True)
class ConfusionMatrix:
    """A 3x3 ours-vs-Whisp confusion over the ``Risk_PCrop`` tri-state.

    ``matrix[ours][whisp]`` is the count of pairs where this system said ``ours``
    and Whisp said ``whisp``. The diagonal is agreement.
    """

    matrix: dict[str, dict[str, int]]
    n: int
    agreement_rate: float
    disagreements: list[Disagreement] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """JSON-serializable view (e.g. for an EVIDENCE artifact)."""
        return {
            "labels": list(PCROP_LABELS),
            "matrix": {row: dict(cols) for row, cols in self.matrix.items()},
            "n": self.n,
            "agreement_rate": self.agreement_rate,
            "disagreements": [
                {"index": d.index, "ours": d.ours, "whisp": d.whisp} for d in self.disagreements
            ],
        }


def _empty_matrix() -> dict[str, dict[str, int]]:
    return {row: {col: 0 for col in PCROP_LABELS} for row in PCROP_LABELS}


def confusion_matrix(pairs: list[tuple[str, str]]) -> ConfusionMatrix:
    """Build the 3x3 ``Risk_PCrop`` confusion matrix from ``(ours, whisp)`` pairs.

    Each element of ``pairs`` is a ``(ours, whisp)`` tuple of ``Risk_PCrop``
    labels; both sides are run through :func:`parse_whisp_pcrop` so casing/spelling
    variants on either side are normalized before counting.

    Returns a :class:`ConfusionMatrix` with the 3x3 counts, the pair count ``n``,
    the ``agreement_rate`` (diagonal mass / ``n``; ``0.0`` for an empty input) and
    the explicit list of disagreements. The agreement rate is descriptive of THIS
    input only -- with illustrative cached data it is not a real-data metric.
    """
    matrix = _empty_matrix()
    disagreements: list[Disagreement] = []
    agree = 0

    for index, (ours_raw, whisp_raw) in enumerate(pairs):
        ours = parse_whisp_pcrop(ours_raw)
        whisp = parse_whisp_pcrop(whisp_raw)
        matrix[ours][whisp] += 1
        if ours == whisp:
            agree += 1
        else:
            disagreements.append(Disagreement(index=index, ours=ours, whisp=whisp))

    n = len(pairs)
    agreement_rate = (agree / n) if n else 0.0
    return ConfusionMatrix(
        matrix=matrix,
        n=n,
        agreement_rate=agreement_rate,
        disagreements=disagreements,
    )


def label_distribution(labels: list[str]) -> dict[str, int]:
    """Count of each canonical ``Risk_PCrop`` label in ``labels`` (parsed).

    Convenience for sanity-checking a side of the comparison; every canonical
    label is present in the result (zero-filled) so the shape is stable.
    """
    counts = Counter(parse_whisp_pcrop(value) for value in labels)
    return {label: counts.get(label, 0) for label in PCROP_LABELS}


# --------------------------------------------------------------------------- #
# Cached fixture loading
# --------------------------------------------------------------------------- #


def load_cached_whisp(path: str | Path) -> dict[str, str]:
    """Load a cached ``point_id -> Risk_PCrop`` map from ``path``.

    The on-disk file may carry metadata keys prefixed with an underscore (e.g.
    ``"_note"``, the illustrative-placeholder honesty marker); those are skipped.
    Every remaining value is normalized via :func:`parse_whisp_pcrop`, so a
    malformed cached label fails loudly here rather than corrupting the matrix.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"cached Whisp fixture must be a JSON object, got {type(raw).__name__}")
    return {
        str(point_id): parse_whisp_pcrop(label)
        for point_id, label in raw.items()
        if not str(point_id).startswith("_")
    }


def cached_fixture_note(path: str | Path) -> str | None:
    """Return the ``_note`` honesty marker from a cached fixture, if present."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    note = raw.get("_note") if isinstance(raw, dict) else None
    return str(note) if note is not None else None


# --------------------------------------------------------------------------- #
# WhispClient -- explicit-refresh only, never on the demo/test path
# --------------------------------------------------------------------------- #


class WhispClient:
    """A thin ``httpx`` client for the hosted Whisp ``Risk_PCrop`` endpoint.

    It is constructed lazily and performs NO network I/O until
    :meth:`submit_geometry` is called -- which only happens on an explicit
    refresh, never at import time, never in the test suite, and never on the demo
    path (``settings.whisp_live`` is ``False`` by default).
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or get_settings().whisp_api_url).rstrip("/")
        self._timeout = timeout
        # An injected client (e.g. one built on httpx.MockTransport) is used as-is
        # for testing; otherwise a real client is created lazily on first use.
        self._client = client
        self._owns_client = client is None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self._timeout)
        return self._client

    def submit_geometry(self, geojson_geometry: dict) -> str:
        """Submit one GeoJSON geometry to Whisp and return its ``Risk_PCrop`` label.

        Performs a network call. The response ``Risk_PCrop`` value is normalized
        through :func:`parse_whisp_pcrop` before being returned, so an unexpected
        spelling fails loudly rather than poisoning the confusion matrix.
        """
        client = self._ensure_client()
        response = client.post("/submit/geojson", json={"geometry": geojson_geometry})
        response.raise_for_status()
        payload = response.json()
        return parse_whisp_pcrop(_extract_pcrop(payload))

    def close(self) -> None:
        """Close the underlying client if this instance owns it."""
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> WhispClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _extract_pcrop(payload: object) -> str:
    """Pull the ``Risk_PCrop`` value out of a Whisp response payload.

    Whisp returns the per-plot result keyed by ``Risk_PCrop``; some shapes wrap it
    under ``data``. This tolerates both rather than assuming one rigid envelope.
    """
    if isinstance(payload, dict):
        if "Risk_PCrop" in payload:
            return str(payload["Risk_PCrop"])
        data = payload.get("data")
        if isinstance(data, dict) and "Risk_PCrop" in data:
            return str(data["Risk_PCrop"])
        if isinstance(data, list) and data and isinstance(data[0], dict):
            if "Risk_PCrop" in data[0]:
                return str(data[0]["Risk_PCrop"])
    raise ValueError("Whisp response did not contain a Risk_PCrop value")


__all__ = [
    "PCROP_LOW",
    "PCROP_HIGH",
    "PCROP_MORE_INFO",
    "PCROP_LABELS",
    "Disagreement",
    "ConfusionMatrix",
    "map_tier_to_whisp",
    "parse_whisp_pcrop",
    "confusion_matrix",
    "label_distribution",
    "load_cached_whisp",
    "cached_fixture_note",
    "WhispClient",
]
