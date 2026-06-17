"""Observability: resolved build-time versions + a Prometheus exposition.

Two concerns live here, both deliberately small:

- ``build_info()`` reports the *resolved* versions of the geospatial stack that
  actually loaded in this process -- not the pinned ranges from ``pyproject``.
  Because rasterio bundles its own GDAL/PROJ/GEOS in the wheel, the only honest
  source of the GDAL/GEOS/PROJ version is the loaded library, queried here. A
  reviewer reading ``/health`` sees exactly which ABI produced a result.
- A Prometheus registry exposing those versions as a ``build_info`` gauge and a
  request counter, rendered via the standard exposition format. PostGIS is NOT
  resolvable at import time (it lives in the DB), so it is added by the API from
  a live connection when available -- never guessed here.
"""

from __future__ import annotations

import importlib.metadata

import pyproj
import rasterio
import shapely
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

from veritas_eudr import __version__ as veritas_version


def _exactextract_version() -> str:
    """Resolve the exactextract version (module attribute, metadata fallback)."""
    try:
        import exactextract

        version = getattr(exactextract, "__version__", None)
        if version:
            return str(version)
    except Exception:  # pragma: no cover - defensive: import/attr failure
        pass
    return importlib.metadata.version("exactextract")


def _geos_version_str() -> str:
    """shapely.geos_version is a (major, minor, patch) tuple; render as a string."""
    return ".".join(str(part) for part in shapely.geos_version)


def build_info() -> dict[str, str]:
    """Return the resolved build-time versions of the running stack.

    Every value is queried from the loaded library, so this reflects the actual
    ABI in this process, not the dependency pins. PostGIS is intentionally absent
    -- it is a property of the database, added by the API from a live connection.
    """
    return {
        "veritas_eudr": str(veritas_version),
        "rasterio": str(rasterio.__version__),
        "gdal": str(rasterio.__gdal_version__),
        "shapely": str(shapely.__version__),
        "geos": _geos_version_str(),
        "pyproj": str(pyproj.__version__),
        "proj": str(pyproj.proj_version_str),
        "exactextract": _exactextract_version(),
    }


# A dedicated registry keeps these metrics out of the global default registry so
# repeated imports (e.g. across tests) do not raise duplicate-timeseries errors.
REGISTRY = CollectorRegistry()

# The build_info gauge follows the Prometheus convention: a constant value of 1
# with the versions carried as labels, so a dashboard can join on them.
_BUILD_INFO_LABELS = (
    "veritas_eudr",
    "rasterio",
    "gdal",
    "shapely",
    "geos",
    "pyproj",
    "proj",
    "exactextract",
)
BUILD_INFO = Gauge(
    "veritas_eudr_build_info",
    "Resolved build-time versions of the running geospatial stack.",
    _BUILD_INFO_LABELS,
    registry=REGISTRY,
)
BUILD_INFO.labels(**build_info()).set(1)

REQUEST_COUNTER = Counter(
    "veritas_eudr_requests_total",
    "Total HTTP requests handled, by endpoint and method.",
    ("endpoint", "method"),
    registry=REGISTRY,
)


def record_request(endpoint: str, method: str) -> None:
    """Increment the request counter for one handled request."""
    REQUEST_COUNTER.labels(endpoint=endpoint, method=method).inc()


def render_metrics() -> tuple[bytes, str]:
    """Render the Prometheus exposition for the project registry.

    Returns ``(body, content_type)`` ready to hand to a ``PlainTextResponse`` so
    the ``/metrics`` endpoint stays a thin pass-through.
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


__all__ = [
    "build_info",
    "render_metrics",
    "record_request",
    "REGISTRY",
    "BUILD_INFO",
    "REQUEST_COUNTER",
]
