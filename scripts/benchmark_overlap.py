"""Inter-plot overlap self-join benchmark: GiST index vs forced sequential scan.

Produces the real ``EXPLAIN (ANALYZE, BUFFERS)`` evidence quoted in EVIDENCE.md.
The query is the genuine overlap-detection self-join a consignment-validation
step runs (find plot pairs that overlap):

    SELECT a.id, b.id FROM bench_plots a JOIN bench_plots b
    ON ST_Intersects(a.geom, b.geom) AND a.id < b.id

We disclose everything: N, the predicate, table page count, PostGIS/GEOS version,
and the host. The sequential-scan baseline is O(N^2) ST_Intersects evaluations,
so it is measured at a smaller N that completes within a statement timeout, while
the GiST path is also measured at a large N to show it scales. Apples-to-apples
speedup is reported at the shared small N.

Run (needs a live PostGIS):
    VERITAS_DATABASE_URL=postgresql+psycopg://veritas:veritas@127.0.0.1:55432/veritas \\
        .venv/bin/python scripts/benchmark_overlap.py
"""

from __future__ import annotations

import io
import os
import platform
import random
import sys

import psycopg

AOI = (107.60, 12.50, 108.50, 13.70)  # lon0, lat0, lon1, lat1 (Vietnam Central Highlands)
PLOT_SIDE_DEG = 0.0015  # ~160 m squares -> a realistic smallholder size, some overlap
SEED = 42

QUERY = (
    "SELECT a.id, b.id FROM bench_plots a JOIN bench_plots b "
    "ON ST_Intersects(a.geom, b.geom) AND a.id < b.id"
)


def _dsn() -> str:
    url = os.environ.get("VERITAS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set VERITAS_DATABASE_URL (or DATABASE_URL) to a live PostGIS")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _rows(n: int) -> io.StringIO:
    rng = random.Random(SEED)
    lon0, lat0, lon1, lat1 = AOI
    buf = io.StringIO()
    s = PLOT_SIDE_DEG
    for i in range(1, n + 1):
        x = rng.uniform(lon0, lon1 - s)
        y = rng.uniform(lat0, lat1 - s)
        wkt = (
            f"POLYGON(({x:.6f} {y:.6f},{x + s:.6f} {y:.6f},"
            f"{x + s:.6f} {y + s:.6f},{x:.6f} {y + s:.6f},{x:.6f} {y:.6f}))"
        )
        buf.write(f"{i}\tSRID=4326;{wkt}\n")
    buf.seek(0)
    return buf


def _load(cur, n: int) -> None:
    cur.execute("DROP TABLE IF EXISTS bench_plots")
    cur.execute("CREATE TABLE bench_plots (id bigint PRIMARY KEY, geom geometry(Polygon, 4326))")
    with cur.copy("COPY bench_plots (id, geom) FROM STDIN") as cp:
        cp.write(_rows(n).read())
    cur.execute("CREATE INDEX bench_plots_geom_gist ON bench_plots USING gist (geom)")
    cur.execute("ANALYZE bench_plots")


def _explain(cur, *, gist: bool, analyze: bool, timeout_ms: int = 0) -> str:
    cur.execute("SET enable_seqscan = on")
    cur.execute(f"SET enable_indexscan = {'on' if gist else 'off'}")
    cur.execute(f"SET enable_bitmapscan = {'on' if gist else 'off'}")
    cur.execute(f"SET statement_timeout = {timeout_ms}")
    mode = "(ANALYZE, BUFFERS, VERBOSE)" if analyze else "(VERBOSE, COSTS)"
    cur.execute(f"EXPLAIN {mode} {QUERY}")
    return "\n".join(r[0] for r in cur.fetchall())


def _pages(cur) -> int:
    cur.execute("SELECT relpages FROM pg_class WHERE relname = 'bench_plots'")
    return cur.fetchone()[0]


def main() -> None:
    n_large = int(os.environ.get("BENCH_N_LARGE", "150000"))
    # The sequential-scan baseline is O(N^2); pick the largest N whose seq-scan
    # completes within the timeout so the fixed-N comparison is real measured time.
    small_candidates = [
        int(x) for x in os.environ.get("BENCH_N_SMALL", "6000,4000,2500,1500").split(",")
    ]
    seq_timeout_ms = int(os.environ.get("BENCH_SEQ_TIMEOUT_MS", "300000"))
    out: list[str] = []
    w = out.append

    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT postgis_full_version()")
        pgis = cur.fetchone()[0]
        w("# Overlap self-join benchmark -- GiST vs forced sequential scan")
        w(f"# host: {platform.platform()} | python {platform.python_version()}")
        w(f"# postgis_full_version: {pgis}")
        w(f"# predicate: {QUERY}")
        w("")

        # --- apples-to-apples at the largest small N whose seq-scan completes ---
        n_small = None
        seq = None
        for cand in small_candidates:
            _load(cur, cand)
            try:
                seq = _explain(cur, gist=False, analyze=True, timeout_ms=seq_timeout_ms)
                n_small = cand
                break
            except psycopg.errors.QueryCanceled:
                w(f"# (seq-scan at N={cand} exceeded {seq_timeout_ms} ms; retrying smaller)")
                continue
        if n_small is None:
            sys.exit("sequential-scan baseline did not complete at any candidate N")
        pages_small = _pages(cur)
        w(f"## Shared N = {n_small} plots ({pages_small} heap pages). Fixed-N speedup.")
        w("")
        w(f"### GiST OFF (forced sequential nested loop, O(N^2)), N={n_small}")
        w(seq)
        w("")
        w(f"### GiST ON, N={n_small}")
        gist_small = _explain(cur, gist=True, analyze=True, timeout_ms=0)
        w(gist_small)
        w("")

        # --- GiST scaling at large N (seq scan is infeasible here; show planner cost only) ---
        _load(cur, n_large)
        pages_large = _pages(cur)
        w(f"## Large N = {n_large} plots ({pages_large} heap pages). GiST scaling.")
        w("")
        w(f"### GiST ON, N={n_large} (actual)")
        gist_large = _explain(cur, gist=True, analyze=True, timeout_ms=0)
        w(gist_large)
        w("")
        w(f"### GiST OFF, N={n_large} (planner COST ONLY -- O(N^2) is not executed)")
        seq_cost = _explain(cur, gist=False, analyze=False)
        w(seq_cost)
        w("")

        cur.execute("DROP TABLE IF EXISTS bench_plots")

    print("\n".join(out))


if __name__ == "__main__":
    main()
