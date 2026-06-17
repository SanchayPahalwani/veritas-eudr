"""Command-line entry point (``veritas-eudr``).

Four subcommands, stdlib ``argparse`` only:

- ``run``     -- the demo: ingest a submission, compute per-plot risk, build a
                 DDS, and print both as JSON. Opens a DB session.
- ``serve``   -- start the FastAPI app under uvicorn (referenced by import string
                 so the api module is not imported until uvicorn loads it).
- ``migrate`` -- bring the database schema up to head via the Alembic CLI.
- ``version`` -- print build info (lazy ``obs`` import; falls back to __version__).

The heavy modules (db, uvicorn, alembic, the api app) are imported INSIDE the
command handlers, so ``import veritas_eudr.cli`` -- and therefore argument
parsing and ``version`` -- stay cheap and dependency-light.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from veritas_eudr.domain import AreaMeasurement, RiskProfile, ValidationReport


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser (split out so it is unit-testable)."""
    parser = argparse.ArgumentParser(
        prog="veritas-eudr",
        description=(
            "EUDR backend spine: ingest a messy farm list, validate/repair, "
            "measure, intersect public deforestation rasters, and emit a "
            "Due Diligence Statement with a replayable evidence trail."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser(
        "run", help="Ingest a submission and print per-plot risk + the DDS as JSON."
    )
    p_run.add_argument("submission", help="Path to the submission file (GeoJSON / CSV / Excel).")
    p_run.add_argument("--operator", required=True, help="Operator name for the DDS.")
    p_run.add_argument("--consignment", required=True, help="Consignment id for the DDS.")
    p_run.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default output is already JSON; flag kept for explicitness).",
    )
    p_run.set_defaults(func=_cmd_run)

    p_serve = sub.add_parser("serve", help="Run the FastAPI app under uvicorn.")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0).")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port (default 8000).")
    p_serve.set_defaults(func=_cmd_serve)

    p_migrate = sub.add_parser("migrate", help="Apply database migrations (alembic upgrade head).")
    p_migrate.set_defaults(func=_cmd_migrate)

    p_version = sub.add_parser("version", help="Print build/version info.")
    p_version.set_defaults(func=_cmd_version)

    return parser


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #


def _outcome_to_dict(outcome: Any) -> dict[str, Any]:
    """Serialize one ``PlotOutcome`` to JSON-safe primitives."""

    def _dump(model: ValidationReport | AreaMeasurement | RiskProfile | None) -> Any:
        return model.model_dump(mode="json") if model is not None else None

    return {
        "plot_id": outcome.plot_id,
        "validation": _dump(outcome.validation),
        "area": _dump(outcome.area),
        "risk": _dump(outcome.risk),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    """Ingest + risk + DDS, print the result as JSON to stdout."""
    from veritas_eudr.db import get_sessionmaker
    from veritas_eudr.pipeline import run_pipeline

    session_factory = get_sessionmaker()
    with session_factory() as session:
        result = run_pipeline(
            args.submission,
            operator_name=args.operator,
            consignment_id=args.consignment,
            session=session,
        )
        session.commit()

    payload = {
        "run_id": result["run_id"],
        "consignment_id": result["consignment_id"],
        "n_plots": result["n_plots"],
        "plots": [_outcome_to_dict(o) for o in result["outcomes"]],
        "dds": result["dds"].model_dump(mode="json"),
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start uvicorn against the api app, referenced by import string."""
    import uvicorn

    uvicorn.run("veritas_eudr.api:app", host=args.host, port=args.port)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    """Run ``alembic upgrade head`` against the configured database."""
    from alembic import command
    from alembic.config import Config

    from veritas_eudr.config import PROJECT_ROOT

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    """Print build info from ``obs`` (built in parallel), falling back to the
    package ``__version__`` if ``obs`` is not importable yet."""
    try:
        from veritas_eudr.obs import build_info

        info = build_info()
        print(json.dumps(info, indent=2, default=str) if isinstance(info, dict) else str(info))
    except Exception:
        from veritas_eudr import __version__

        print(__version__)
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
