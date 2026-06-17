"""Configuration + policy loading.

Two layers:
- ``Settings`` -- runtime/environment knobs (DB URL, thresholds, AOI), via
  pydantic-settings. Env-overridable, prefixed ``VERITAS_``.
- ``load_policy()`` -- the versioned, CELEX-cited regulatory policy from
  ``policy/eudr_policy.yaml``. The policy is the definition of correctness, so
  it is data (reviewable, diffable), not code.

``EUDR_DEFORESTATION_CUTOFF`` is a module constant and is deliberately kept
DISTINCT from the regulation's application date (tripwire J).
"""

from __future__ import annotations

import functools
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The single deforestation cutoff for the whole system. NOT the application date.
EUDR_DEFORESTATION_CUTOFF: date = date(2020, 12, 31)

# Hansen lossyear encoding: 1..25 == 2001..2025. Band 21 == year 2021, the first
# post-cutoff annual band -- treated as boundary-uncertain, not a hard "high"
# (tripwire B). This is the offset to convert a lossyear band to a calendar year.
HANSEN_LOSSYEAR_BASE_YEAR: int = 2000

# Repo root (…/veritas-eudr), resolved from this file's location.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
POLICY_PATH: Path = PROJECT_ROOT / "policy" / "eudr_policy.yaml"


class Settings(BaseSettings):
    """Runtime knobs. Override via VERITAS_* env vars or a .env file."""

    model_config = SettingsConfigDict(env_prefix="VERITAS_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://veritas:veritas@db:5432/veritas",
        description="SQLAlchemy URL for the PostGIS system of record.",
    )

    # 4 ha submission-format boundary tolerance. A measured area within +/- this
    # band of 4 ha is BORDERLINE -> NEEDS_REVIEW, because the geography-vs-6933
    # disagreement could flip whether a point-only submission is a valid format.
    area_borderline_band_ha: float = Field(default=0.10)

    # tripwire F: AUTO_FIXED is gated on the repair changing area by <= this
    # fraction; otherwise escalate to NEEDS_REVIEW and record before/after.
    repair_area_epsilon_frac: float = Field(default=0.01)

    # tripwire C: flag a plot HIGH only when summed post-2020 loss coverage in
    # GROUND hectares is >= this fraction of plot area (never bare ST_Intersects).
    loss_coverage_threshold_frac: float = Field(default=0.10)

    # Local raster fixtures baked into the image (zero live dependency).
    fixtures_dir: Path = Field(default=PROJECT_ROOT / "tests" / "fixtures")

    # AOI: Vietnam Central Highlands robusta belt around Buon Ma Thuot (~12.67N) --
    # MID-LATITUDE tropics, not the equator. The committed SYNTHETIC fixtures cover a
    # narrower ~12.64N..12.70N window; the numeric defaults below are the AOI bbox.
    aoi_min_lon: float = 107.5
    aoi_min_lat: float = 12.4
    aoi_max_lon: float = 108.6
    aoi_max_lat: float = 14.0

    whisp_api_url: str = Field(default="https://whisp.openforis.org/api")
    whisp_live: bool = Field(default=False, description="Opt-in only; never on the demo path.")


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@functools.lru_cache(maxsize=1)
def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the versioned EUDR policy. Cached; pass a path to bypass (tests)."""
    p = Path(path) if path is not None else POLICY_PATH
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def policy_version(path: str | Path | None = None) -> str:
    return str(load_policy(path)["policy_version"])
