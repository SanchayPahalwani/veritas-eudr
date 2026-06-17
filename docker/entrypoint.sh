#!/usr/bin/env bash
# Container entrypoint: wait for PostGIS -> migrate -> seed the demo -> serve.
#
# The whole sequence is idempotent, so `docker compose up` is safe to re-run:
#   - `veritas-eudr migrate` is `alembic upgrade head`; already-applied revisions
#     are no-ops. It also asserts the PostGIS>=3.2 / GEOS>=3.10 floor (migration
#     0001) and creates the PL/pgSQL functions, GiST index, and extension.
#   - the seed `veritas-eudr run` is idempotent at the ingest layer (idempotency by
#     submission_hash / geom_hash): re-running it does not duplicate plots and
#     yields a stable run_id.
#   - `serve` is the long-running process (exec'd, so it becomes PID 1's child and
#     receives signals directly for clean shutdown).
set -euo pipefail

# The app reads the DB URL from VERITAS_DATABASE_URL (see config.Settings); the
# compose file sets it. Default to the in-compose `db` service so running the
# image bare (with a reachable `db` host) still works.
DB_URL="${VERITAS_DATABASE_URL:-postgresql+psycopg://veritas:veritas@db:5432/veritas}"

# Derive host/port for pg_isready from the SQLAlchemy URL. pg_isready does not
# speak the psycopg URL scheme, so pull the host:port out of `...@HOST:PORT/...`.
hostport="${DB_URL#*@}"      # strip everything through the first '@'
hostport="${hostport%%/*}"   # strip the '/dbname...' tail
DB_HOST="${hostport%%:*}"
DB_PORT="${hostport##*:}"
# If there was no explicit ':port', hostport==host -> fall back to 5432.
if [ "${DB_PORT}" = "${DB_HOST}" ]; then
    DB_PORT=5432
fi

echo "[entrypoint] waiting for PostGIS at ${DB_HOST}:${DB_PORT} ..."
# Bounded wait: ~60 tries x 2s = up to 2 minutes. The compose healthcheck already
# gates `app` on the db being healthy, but this loop makes the image robust when
# run outside compose (e.g. `docker run`).
for i in $(seq 1 60); do
    if pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -q; then
        echo "[entrypoint] PostGIS is accepting connections."
        break
    fi
    if [ "${i}" -eq 60 ]; then
        echo "[entrypoint] ERROR: PostGIS not reachable at ${DB_HOST}:${DB_PORT} after 120s." >&2
        exit 1
    fi
    sleep 2
done

echo "[entrypoint] applying migrations (alembic upgrade head; asserts GEOS>=3.10 floor) ..."
veritas-eudr migrate

# Seed the demo so the API has data immediately. Idempotent: re-up does not
# duplicate. Allowed to fail soft so a transient seed error never blocks the API
# from coming up (the data can be re-seeded by re-running `veritas-eudr run`).
echo "[entrypoint] seeding demo consignment DEMO from the committed messy fixture ..."
if veritas-eudr run \
        tests/fixtures/submissions/messy_submission.geojson \
        --operator "Acme Coffee Co" \
        --consignment DEMO; then
    echo "[entrypoint] demo seed complete."
else
    echo "[entrypoint] WARNING: demo seed failed; starting API anyway." >&2
fi

echo "[entrypoint] starting API on 0.0.0.0:8000 ..."
exec veritas-eudr serve --host 0.0.0.0 --port 8000
