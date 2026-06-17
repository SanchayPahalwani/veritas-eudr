"""Tests for veritas_eudr.obs: resolved build info + Prometheus exposition.

No database is needed -- both build_info() and render_metrics() are pure,
in-process functions over the loaded geospatial stack.
"""

from __future__ import annotations

from veritas_eudr.obs import build_info, render_metrics

_EXPECTED_KEYS = {
    "veritas_eudr",
    "rasterio",
    "gdal",
    "shapely",
    "geos",
    "pyproj",
    "proj",
    "exactextract",
}


def test_build_info_has_expected_keys():
    info = build_info()
    assert set(info) == _EXPECTED_KEYS


def test_build_info_values_are_non_empty_strings():
    info = build_info()
    for key in _EXPECTED_KEYS:
        value = info[key]
        assert isinstance(value, str), f"{key} is not a string: {value!r}"
        assert value.strip(), f"{key} is empty"


def test_build_info_geospatial_versions_look_like_versions():
    """rasterio/gdal/geos/proj/exactextract resolve to dotted version strings,
    not placeholders -- they are queried from the loaded libraries."""
    info = build_info()
    for key in ("rasterio", "gdal", "geos", "proj", "exactextract"):
        value = info[key]
        assert value[0].isdigit(), f"{key} does not start with a digit: {value!r}"
        assert "." in value, f"{key} is not a dotted version: {value!r}"


def test_build_info_is_stable_across_calls():
    assert build_info() == build_info()


def test_render_metrics_returns_bytes_and_content_type():
    body, content_type = render_metrics()
    assert isinstance(body, bytes)
    assert isinstance(content_type, str)
    assert "text/plain" in content_type


def test_render_metrics_contains_build_info_metric():
    body, _ = render_metrics()
    text = body.decode("utf-8")
    # The build_info gauge is present in the exposition ...
    assert "veritas_eudr_build_info" in text
    # ... and carries the resolved versions as labels.
    info = build_info()
    assert f'rasterio="{info["rasterio"]}"' in text
    assert f'gdal="{info["gdal"]}"' in text
    assert f'exactextract="{info["exactextract"]}"' in text


def test_render_metrics_contains_request_counter_definition():
    body, _ = render_metrics()
    text = body.decode("utf-8")
    assert "veritas_eudr_requests_total" in text
