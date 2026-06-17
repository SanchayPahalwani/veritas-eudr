# veritas-eudr application image.
#
# Reproducibility contract (see pyproject.toml): the geospatial stack is pinned
# via wheels, NOT a system GDAL. rasterio/shapely/pyproj/geopandas/exactextract
# ship manylinux x86_64 wheels that BUNDLE their own GDAL/PROJ/GEOS, so a slim
# Python base + pip is sufficient -- there is deliberately no `apt-get install
# gdal/proj/geos` here, and no system `gdalinfo` CLI in the resulting image.
#
# Why `--platform=linux/amd64` is pinned (a real build finding, not a default):
# exactextract 0.3.0 publishes only `manylinux_2_27/2_28 x86_64` wheels and NO
# linux-aarch64 wheel. On an arm64 host, an unpinned build resolves the arm64 base
# image, finds no exactextract wheel, and falls back to compiling it from source --
# which needs a full C++/CMake/GEOS toolchain that this slim image deliberately
# lacks, and the build fails. Pinning to linux/amd64 makes pip use the published
# x86_64 wheel; it is also the production/CI target (Fargate, amd64 runners) and
# matches the postgis image, which itself has no arm64 manifest. (glibc note: the
# wheels need >= 2.28; python:3.12-slim is Debian bookworm, glibc 2.36 -- fine.)
#
# The build-time verification step below prints the resolved native versions
# (rasterio.__gdal_version__, shapely.geos_version, ...) into the build log so the
# actually-linked stack is auditable. The PostGIS/GEOS floor is asserted later, at
# migrate time, by migration 0001 (sql/functions/00_assert_floors.sql).
FROM --platform=linux/amd64 python:3.12-slim

# - Fail fast, no stale bytecode, unbuffered logs (so the entrypoint's progress
#   and uvicorn output stream straight through `docker logs`).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime tooling only:
# - postgresql-client gives `pg_isready`, used by the entrypoint to wait for the
#   DB before migrating. It is NOT a build dependency of the wheels.
# - No GDAL/PROJ/GEOS packages: the wheels carry their own (see header).
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the build inputs needed to `pip install .`:
# - pyproject.toml: project metadata + pinned dependency ranges.
# - src/: the package (hatchling builds the wheel from src/veritas_eudr).
# Everything else (policy, migrations, sql, scripts, fixtures) is copied below so
# `migrate` (alembic.ini + migrations + sql) and the seeded demo run (the
# committed messy_submission fixture) work inside the container with zero network.
COPY pyproject.toml README.md ./
COPY src ./src

# Install the project itself (resolves and installs the pinned dependency tree,
# pulling the bundled-GDAL wheels). `.` installs from pyproject in the WORKDIR.
RUN pip install .

# Runtime data the package reads at run/migrate time. config.PROJECT_ROOT resolves
# to /app (parents[2] of /app/src/veritas_eudr/config.py), so these paths line up
# with what alembic.ini, config.POLICY_PATH and Settings.fixtures_dir expect.
COPY alembic.ini ./
COPY migrations ./migrations
COPY sql ./sql
COPY policy ./policy
COPY scripts ./scripts
# The demo is seeded from a COMMITTED fixture -- ship it so the API has data on
# first boot with no external download.
COPY tests/fixtures ./tests/fixtures

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Build-time version verification: the resolved native stack lands in the build
# log. If a pinned wheel ever fails to provide GDAL/GEOS/exactextract, this RUN
# fails the build loudly rather than shipping a broken image.
RUN python -c "import rasterio,shapely,exactextract; print('rasterio',rasterio.__version__,'gdal',rasterio.__gdal_version__,'geos',shapely.geos_version,'exactextract',exactextract.__version__)"

# Non-root runtime user. Created after the COPY/install steps (which need root for
# /usr/local) and given ownership of /app so the entrypoint can write nothing it
# shouldn't; PROJ's writable cache, if used, lands under this user's $HOME.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
