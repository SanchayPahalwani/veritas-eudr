"""Tests for the Whisp self-grading tooling.

Coverage:
- The confusion-matrix math is correct on hand-constructed ``(ours, whisp)`` pairs
  (the 3x3 counts, ``n`` and ``agreement_rate``), a fully-agreeing set scores
  ``1.0``, and a known disagreement is listed.
- The tier<->``Risk_PCrop`` mapping round-trips for all three tiers.
- :class:`WhispClient` never hits the live network in the test suite: the URL is
  asserted on construction, and a submission is exercised only against an injected
  ``httpx.MockTransport``.
- The cached fixture carries its illustrative ``_note`` honesty marker, so that
  marker cannot be silently dropped.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from veritas_eudr.domain import RiskTier
from veritas_eudr.whisp import (
    PCROP_HIGH,
    PCROP_LABELS,
    PCROP_LOW,
    PCROP_MORE_INFO,
    WhispClient,
    cached_fixture_note,
    confusion_matrix,
    label_distribution,
    load_cached_whisp,
    map_tier_to_whisp,
    parse_whisp_pcrop,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHED_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "whisp" / "cached_pcrop.json"


# --------------------------------------------------------------------------- #
# tier <-> Risk_PCrop mapping
# --------------------------------------------------------------------------- #


def test_map_tier_to_whisp_covers_all_three_tiers():
    assert map_tier_to_whisp(RiskTier.LOW) == PCROP_LOW
    assert map_tier_to_whisp(RiskTier.HIGH) == PCROP_HIGH
    assert map_tier_to_whisp(RiskTier.MORE_INFO_NEEDED) == PCROP_MORE_INFO


def test_map_tier_to_whisp_matches_riskprofile_property():
    """The standalone mapping equals RiskProfile.whisp_risk_pcrop's mapping."""
    from datetime import date

    from veritas_eudr.domain import RiskProfile

    for tier in RiskTier:
        profile = RiskProfile(
            plot_id="p",
            risk=tier,
            rationale="t",
            cutoff_date=date(2020, 12, 31),
        )
        assert map_tier_to_whisp(tier) == profile.whisp_risk_pcrop


def test_parse_round_trips_every_tier_label():
    for tier in RiskTier:
        label = map_tier_to_whisp(tier)
        assert parse_whisp_pcrop(label) == label


def test_parse_tolerates_casing_and_spelling_variants():
    assert parse_whisp_pcrop("low") == PCROP_LOW
    assert parse_whisp_pcrop("LOW RISK") == PCROP_LOW
    assert parse_whisp_pcrop("  High  ") == PCROP_HIGH
    assert parse_whisp_pcrop("More information needed") == PCROP_MORE_INFO
    assert parse_whisp_pcrop("more-info-needed") == PCROP_MORE_INFO


def test_parse_rejects_unknown_label():
    with pytest.raises(ValueError):
        parse_whisp_pcrop("medium")


# --------------------------------------------------------------------------- #
# confusion matrix math
# --------------------------------------------------------------------------- #


def test_confusion_matrix_counts_on_known_input():
    # Hand-constructed: 2 Low/Low, 1 High/High, 1 MoreInfo/MoreInfo on the
    # diagonal; one Low(ours)/High(whisp) and one High(ours)/MoreInfo(whisp)
    # off-diagonal. n=6, agree=4 -> 4/6.
    pairs = [
        (PCROP_LOW, PCROP_LOW),
        (PCROP_LOW, PCROP_LOW),
        (PCROP_HIGH, PCROP_HIGH),
        (PCROP_MORE_INFO, PCROP_MORE_INFO),
        (PCROP_LOW, PCROP_HIGH),
        (PCROP_HIGH, PCROP_MORE_INFO),
    ]
    cm = confusion_matrix(pairs)

    assert cm.n == 6
    assert cm.matrix[PCROP_LOW][PCROP_LOW] == 2
    assert cm.matrix[PCROP_HIGH][PCROP_HIGH] == 1
    assert cm.matrix[PCROP_MORE_INFO][PCROP_MORE_INFO] == 1
    assert cm.matrix[PCROP_LOW][PCROP_HIGH] == 1
    assert cm.matrix[PCROP_HIGH][PCROP_MORE_INFO] == 1
    # Every other cell is zero.
    total = sum(cm.matrix[r][c] for r in PCROP_LABELS for c in PCROP_LABELS)
    assert total == 6
    assert cm.agreement_rate == pytest.approx(4 / 6)


def test_confusion_matrix_full_agreement_is_one():
    pairs = [
        (PCROP_LOW, PCROP_LOW),
        (PCROP_HIGH, PCROP_HIGH),
        (PCROP_MORE_INFO, PCROP_MORE_INFO),
    ]
    cm = confusion_matrix(pairs)
    assert cm.agreement_rate == pytest.approx(1.0)
    assert cm.disagreements == []


def test_confusion_matrix_lists_disagreements_with_index():
    pairs = [
        (PCROP_LOW, PCROP_LOW),
        (PCROP_HIGH, PCROP_LOW),  # index 1: disagreement (we say High, Whisp Low)
    ]
    cm = confusion_matrix(pairs)
    assert cm.agreement_rate == pytest.approx(0.5)
    assert len(cm.disagreements) == 1
    d = cm.disagreements[0]
    assert d.index == 1
    assert d.ours == PCROP_HIGH
    assert d.whisp == PCROP_LOW


def test_confusion_matrix_empty_input_is_zero_not_division_error():
    cm = confusion_matrix([])
    assert cm.n == 0
    assert cm.agreement_rate == 0.0
    assert cm.disagreements == []


def test_confusion_matrix_normalizes_label_variants_before_counting():
    # Mixed spelling/casing on both sides still lands on the canonical diagonal.
    pairs = [("low", "Low"), ("HIGH", "high risk")]
    cm = confusion_matrix(pairs)
    assert cm.agreement_rate == pytest.approx(1.0)
    assert cm.matrix[PCROP_LOW][PCROP_LOW] == 1
    assert cm.matrix[PCROP_HIGH][PCROP_HIGH] == 1


def test_confusion_matrix_as_dict_is_json_shaped():
    cm = confusion_matrix([(PCROP_LOW, PCROP_HIGH)])
    d = cm.as_dict()
    assert d["labels"] == list(PCROP_LABELS)
    assert d["n"] == 1
    assert d["disagreements"] == [{"index": 0, "ours": PCROP_LOW, "whisp": PCROP_HIGH}]


def test_label_distribution_is_zero_filled():
    dist = label_distribution([PCROP_LOW, PCROP_LOW, PCROP_HIGH])
    assert dist == {PCROP_LOW: 2, PCROP_HIGH: 1, PCROP_MORE_INFO: 0}


# --------------------------------------------------------------------------- #
# cached fixture
# --------------------------------------------------------------------------- #


def test_cached_fixture_carries_illustrative_note():
    """The honesty marker must be present and say it is NOT a real Whisp run."""
    note = cached_fixture_note(CACHED_FIXTURE)
    assert note is not None
    lowered = note.lower()
    assert "not a real" in lowered
    assert "placeholder" in lowered or "illustrative" in lowered


def test_load_cached_whisp_skips_underscore_metadata():
    cached = load_cached_whisp(CACHED_FIXTURE)
    # Metadata keys (_note, _source, _aoi) are not point ids.
    assert all(not k.startswith("_") for k in cached)
    assert cached  # has at least one point
    # Every value is a canonical tri-state label.
    assert set(cached.values()) <= set(PCROP_LABELS)
    assert cached["pt-007"] == PCROP_HIGH


def test_load_cached_whisp_feeds_confusion_matrix():
    """End-to-end: a cached map joined against our verdicts builds a matrix."""
    cached = load_cached_whisp(CACHED_FIXTURE)
    # Pretend our engine agreed on every cached point (illustrative only).
    pairs = [(label, label) for label in cached.values()]
    cm = confusion_matrix(pairs)
    assert cm.n == len(cached)
    assert cm.agreement_rate == pytest.approx(1.0)


def test_load_cached_whisp_rejects_non_object(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_cached_whisp(bad)


# --------------------------------------------------------------------------- #
# WhispClient -- no live network in tests
# --------------------------------------------------------------------------- #


def test_whisp_client_records_base_url_without_network():
    client = WhispClient(base_url="https://whisp.example.org/api/")
    # Trailing slash stripped; no I/O performed by construction.
    assert client.base_url == "https://whisp.example.org/api"


def test_whisp_client_defaults_to_settings_url():
    from veritas_eudr.config import get_settings

    client = WhispClient()
    assert client.base_url == get_settings().whisp_api_url.rstrip("/")


def test_whisp_client_submit_against_mock_transport():
    """submit_geometry parses Risk_PCrop from a mocked response -- no live call."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(200, json={"Risk_PCrop": "high"})

    transport = httpx.MockTransport(handler)
    mock = httpx.Client(base_url="https://whisp.example.org/api", transport=transport)
    client = WhispClient(base_url="https://whisp.example.org/api", client=mock)

    geometry = {"type": "Point", "coordinates": [108.01, 12.64]}
    result = client.submit_geometry(geometry)

    assert result == PCROP_HIGH  # normalized from "high"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/submit/geojson")


def test_whisp_client_submit_handles_wrapped_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"Risk_PCrop": "More info needed"}]})

    transport = httpx.MockTransport(handler)
    mock = httpx.Client(base_url="https://whisp.example.org/api", transport=transport)
    client = WhispClient(client=mock, base_url="https://whisp.example.org/api")
    assert client.submit_geometry({"type": "Point", "coordinates": [0, 0]}) == PCROP_MORE_INFO


def test_whisp_client_submit_raises_on_missing_pcrop():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"something_else": 1})

    transport = httpx.MockTransport(handler)
    mock = httpx.Client(base_url="https://whisp.example.org/api", transport=transport)
    client = WhispClient(client=mock, base_url="https://whisp.example.org/api")
    with pytest.raises(ValueError):
        client.submit_geometry({"type": "Point", "coordinates": [0, 0]})
