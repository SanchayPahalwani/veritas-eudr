"""Tests for the command-line entry point (``veritas_eudr.cli``).

The parser is exercised directly; the heavy commands are monkeypatched so the
unit tests never bind a port or require a database:
- ``run``   -- the DB session factory and ``run_pipeline`` are stubbed.
- ``serve`` -- ``uvicorn.run`` is stubbed (no port is bound).
- ``migrate`` -- the alembic command is stubbed (no DB is touched).
- ``version`` -- run for real; with ``obs`` absent it falls back to __version__.
"""

from __future__ import annotations

import json

import pytest

from veritas_eudr import __version__, cli
from veritas_eudr.config import EUDR_DEFORESTATION_CUTOFF
from veritas_eudr.domain import (
    CountryRiskClass,
    Disposition,
    DueDiligencePath,
    DueDiligenceStatement,
    RiskTier,
    ValidationReport,
)
from veritas_eudr.pipeline import PlotOutcome

# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def test_parser_parses_run_subcommand():
    args = cli.build_parser().parse_args(
        ["run", "sub.geojson", "--operator", "Acme", "--consignment", "C1"]
    )
    assert args.command == "run"
    assert args.submission == "sub.geojson"
    assert args.operator == "Acme"
    assert args.consignment == "C1"


def test_parser_parses_serve_subcommand_with_defaults():
    args = cli.build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.host == "0.0.0.0"
    assert args.port == 8000


def test_parser_parses_serve_overrides():
    args = cli.build_parser().parse_args(["serve", "--host", "127.0.0.1", "--port", "9001"])
    assert args.host == "127.0.0.1"
    assert args.port == 9001


def test_parser_parses_migrate_subcommand():
    args = cli.build_parser().parse_args(["migrate"])
    assert args.command == "migrate"


def test_parser_parses_version_subcommand():
    args = cli.build_parser().parse_args(["version"])
    assert args.command == "version"


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_run_requires_operator_and_consignment():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["run", "sub.geojson"])


# --------------------------------------------------------------------------- #
# version (runs for real)
# --------------------------------------------------------------------------- #


def test_version_prints_something(capsys):
    rc = cli.main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip()


def test_version_falls_back_to_package_version_when_obs_missing(capsys, monkeypatch):
    # Force the obs import to fail so the fallback path is exercised deterministically.
    import builtins

    real_import = builtins.__import__

    def _fail_obs(name, *args, **kwargs):
        if name == "veritas_eudr.obs" or name.endswith(".obs"):
            raise ImportError("obs not built yet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_obs)
    rc = cli.main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert __version__ in out


# --------------------------------------------------------------------------- #
# run (heavy work mocked: no DB, no rasters)
# --------------------------------------------------------------------------- #


def _fake_dds() -> DueDiligenceStatement:
    return DueDiligenceStatement(
        consignment_id="C1",
        operator_name="Acme",
        plot_ids=["plot-1"],
        geojson={"type": "FeatureCollection", "features": []},
        deforestation_determination=RiskTier.LOW,
        due_diligence_path=DueDiligencePath.SIMPLIFIED_DD,
        country_risk_class=CountryRiskClass.LOW,
        due_diligence_regime="Art. 13 simplified",
        compliance_complete=False,
        policy_version="test",
        deforestation_cutoff_date=EUDR_DEFORESTATION_CUTOFF,
        regulation_application_date=EUDR_DEFORESTATION_CUTOFF,
    )


def test_run_invokes_pipeline_and_prints_json(monkeypatch, capsys):
    committed = {"value": False}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def commit(self):
            committed["value"] = True

    def _fake_run_pipeline(submission, operator_name, consignment_id, session):
        outcome = PlotOutcome(
            plot_id="plot-1",
            validation=ValidationReport(plot_id="plot-1", source_geometry_type="Polygon"),
            area=None,
            risk=None,
        )
        return {
            "run_id": "run-abc",
            "consignment_id": consignment_id,
            "n_plots": 1,
            "outcomes": [outcome],
            "dds": _fake_dds(),
        }

    # The handler imports these names lazily from their modules; patch at source.
    import veritas_eudr.db as db_mod
    import veritas_eudr.pipeline as pipeline_mod

    monkeypatch.setattr(db_mod, "get_sessionmaker", lambda: (lambda: _FakeSession()))
    monkeypatch.setattr(pipeline_mod, "run_pipeline", _fake_run_pipeline)

    rc = cli.main(["run", "sub.geojson", "--operator", "Acme", "--consignment", "C1"])
    out = capsys.readouterr().out

    assert rc == 0
    assert committed["value"] is True
    payload = json.loads(out)
    assert payload["run_id"] == "run-abc"
    assert payload["consignment_id"] == "C1"
    assert payload["n_plots"] == 1
    assert payload["plots"][0]["plot_id"] == "plot-1"
    assert payload["dds"]["compliance_complete"] is False


# --------------------------------------------------------------------------- #
# serve (uvicorn mocked: no port bound)
# --------------------------------------------------------------------------- #


def test_serve_calls_uvicorn_by_import_string(monkeypatch):
    calls = {}

    import sys
    import types

    fake_uvicorn = types.ModuleType("uvicorn")

    def _fake_run(app, host, port):
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    fake_uvicorn.run = _fake_run
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    rc = cli.main(["serve", "--host", "127.0.0.1", "--port", "9123"])
    assert rc == 0
    # Referenced by import string (the api module is NOT imported here).
    assert calls["app"] == "veritas_eudr.api:app"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9123


# --------------------------------------------------------------------------- #
# migrate (alembic mocked: no DB touched)
# --------------------------------------------------------------------------- #


def test_migrate_calls_alembic_upgrade_head(monkeypatch):
    import sys
    import types

    calls = {}

    fake_command = types.ModuleType("alembic.command")

    def _fake_upgrade(cfg, revision):
        calls["revision"] = revision
        calls["cfg"] = cfg

    fake_command.upgrade = _fake_upgrade

    fake_config_mod = types.ModuleType("alembic.config")

    class _FakeConfig:
        def __init__(self, path):
            self.path = path

    fake_config_mod.Config = _FakeConfig

    fake_alembic = types.ModuleType("alembic")
    fake_alembic.command = fake_command

    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "alembic.command", fake_command)
    monkeypatch.setitem(sys.modules, "alembic.config", fake_config_mod)

    rc = cli.main(["migrate"])
    assert rc == 0
    assert calls["revision"] == "head"
    assert str(calls["cfg"].path).endswith("alembic.ini")


def test_disposition_enum_importable():
    # Guards the test imports stay aligned with the domain contract.
    assert Disposition.AUTO_VALID
