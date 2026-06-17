#!/usr/bin/env bash
# =============================================================================
# fetch_data.sh -- fetch + AOI-clip the REAL production datasets.
#
# IMPORTANT
#   The rasters committed under tests/fixtures/rasters/ are SYNTHETIC, hand-painted
#   stand-ins, used so the test/CI path is offline and deterministic. They are NOT
#   real data. Running THIS script fetches the genuine Hansen / JRC / ESA WorldCover
#   / Sample Earth / FDaP datasets, clips them to the AOI, and writes Cloud-Optimised
#   GeoTIFFs. It does NOT overwrite the committed synthetic fixtures (it writes to a
#   separate data/real/ directory).
#
# SAFE TO READ WITHOUT RUNNING
#   `set -euo pipefail`; nothing downloads on source/import. You must invoke the
#   script explicitly. A per-tile <50 MB size guard aborts before any oversized file
#   is committed (a full 10x10-deg Hansen granule is ~120 MB and breaches GitHub's
#   100 MB limit, hence the AOI clip + guard).
#
# REQUIREMENTS: bash, curl, GDAL CLI (gdalwarp, gdal_translate, gdalinfo). For the
# FDaP requester-pays GCS object you additionally need gcloud/gsutil with billing.
#
# AOI (EPSG:4326): lon 108.000..108.060, lat 12.640..12.700
#   Vietnam Central Highlands (robusta coffee), ~12.67 N (mid-latitude tropics).
# =============================================================================

set -euo pipefail

# --- AOI bounds (xmin ymin xmax ymax for gdalwarp -te) -----------------------
AOI_XMIN=108.000
AOI_YMIN=12.640
AOI_XMAX=108.060
AOI_YMAX=12.700

# --- output dir (kept separate from the committed synthetic fixtures) --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${REPO_ROOT}/data/real"
RAW_DIR="${OUT_DIR}/raw"
mkdir -p "${OUT_DIR}" "${RAW_DIR}"

MAX_TILE_MB=50

# --- helpers -----------------------------------------------------------------
log() { printf '[fetch_data] %s\n' "$*" >&2; }

require() {
  command -v "$1" >/dev/null 2>&1 || { log "ERROR: '$1' not found on PATH"; exit 2; }
}

# HTTP-200 reachability check (HEAD; no body downloaded).
check_url_200() {
  local url="$1"
  local code
  code="$(curl -sSL -o /dev/null -w '%{http_code}' --head "${url}" || echo "000")"
  if [[ "${code}" != "200" ]]; then
    log "ERROR: ${url} returned HTTP ${code} (expected 200)"
    return 1
  fi
  log "OK 200: ${url}"
}

# Abort if a produced tile breaches the per-tile size budget.
guard_size() {
  local f="$1"
  local mb
  mb=$(( $(stat -f%z "${f}" 2>/dev/null || stat -c%s "${f}") / 1000000 ))
  if (( mb >= MAX_TILE_MB )); then
    log "ERROR: ${f} is ${mb} MB (>= ${MAX_TILE_MB} MB guard). Refusing -- breaches GitHub 100 MB limit if committed."
    exit 3
  fi
  log "OK size: ${f} = ${mb} MB (< ${MAX_TILE_MB} MB)"
}

# clip a source raster to the AOI and write a COG.
clip_to_cog() {
  local src="$1" dst="$2"
  log "clip -> COG: ${dst}"
  gdalwarp -overwrite -t_srs EPSG:4326 \
    -te "${AOI_XMIN}" "${AOI_YMIN}" "${AOI_XMAX}" "${AOI_YMAX}" \
    "${src}" "${dst}.tmp.tif"
  gdal_translate -of COG -co COMPRESS=DEFLATE -co PREDICTOR=2 \
    "${dst}.tmp.tif" "${dst}"
  rm -f "${dst}.tmp.tif"
  guard_size "${dst}"
}

require curl

# =============================================================================
# 1) Hansen Global Forest Change GFC-2025-v1.13 -- lossyear granule
#    Encoding: lossyear band 1..25 == calendar 2001..2025; 0 == no loss.
#    Licence: CC-BY 4.0. The AOI sits at 108 E / 13 N -> 10x10-deg tile 20N_100E.
#    Granule index: https://storage.googleapis.com/earthenginepartners-hansen/GFC-2025-v1.13/download.html
# =============================================================================
HANSEN_TILE_URL="https://storage.googleapis.com/earthenginepartners-hansen/GFC-2025-v1.13/Hansen_GFC-2025-v1.13_lossyear_20N_100E.tif"
fetch_hansen() {
  require gdalwarp; require gdal_translate
  check_url_200 "${HANSEN_TILE_URL}"
  # Stream-clip directly from the remote tile via /vsicurl/ (no 120 MB local copy).
  clip_to_cog "/vsicurl/${HANSEN_TILE_URL}" "${OUT_DIR}/hansen_lossyear_aoi.tif"
}

# =============================================================================
# 2) JRC Global Forest Cover 2020 (GFC2020) V3
#    1 = forest at 2020 (excludes plantations by definition). 10 m.
#    Licence: free of charge, attribution required (EC reuse notice).
#    Citation: Bourgoin et al. 2026, ESSD 18:1331-1365.
#    Product page / catalogue: https://forobs.jrc.ec.europa.eu/GFC2020
#    JRC Data Catalogue record: https://data.jrc.ec.europa.eu/dataset/10d1b337-b7d1-4938-a048-686c8185b290
#    AOI lies in the N00E100..N20E120 10-deg block; adjust tile name to the catalogue listing.
# =============================================================================
JRC_PRODUCT_PAGE="https://forobs.jrc.ec.europa.eu/GFC2020"
JRC_TILE_URL="https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GFC/GFC2020/V3/JRC_GFC2020_V3_N20E100.tif"
fetch_jrc() {
  require gdalwarp; require gdal_translate
  check_url_200 "${JRC_PRODUCT_PAGE}"
  check_url_200 "${JRC_TILE_URL}"
  clip_to_cog "/vsicurl/${JRC_TILE_URL}" "${OUT_DIR}/jrc_gfc2020_aoi.tif"
}

# =============================================================================
# 3) ESA WorldCover v200 (2021 epoch)
#    Land-cover CONTEXT classes (10=tree, 40=cropland, ...). 10 m. Licence: CC-BY 4.0.
#    3x3-deg tiles named by SW corner. AOI (108 E / 12.6 N) -> tile N12E108.
#    Product page: https://esa-worldcover.org/en
#    S3 bucket (eu-central-1): s3://esa-worldcover/v200/2021/map/
# =============================================================================
WORLDCOVER_TILE_URL="https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N12E108_Map.tif"
fetch_worldcover() {
  require gdalwarp; require gdal_translate
  check_url_200 "${WORLDCOVER_TILE_URL}"
  clip_to_cog "/vsicurl/${WORLDCOVER_TILE_URL}" "${OUT_DIR}/worldcover_aoi.tif"
}

# =============================================================================
# 4) Sample Earth (CIAT/Alliance) land-cover reference points
#    ~100k points; filter to coffee/cocoa classes for commodity coordinates.
#    Licence: CC-BY-NC 4.0 (NonCommercial) -- attribution + non-commercial use only.
#    Harvard Dataverse DOI: 10.7910/DVN/U7HWY1
#    Landing page: https://doi.org/10.7910/DVN/U7HWY1
# =============================================================================
SAMPLEEARTH_DOI="10.7910/DVN/U7HWY1"
SAMPLEEARTH_LANDING="https://doi.org/${SAMPLEEARTH_DOI}"
fetch_sample_earth() {
  check_url_200 "${SAMPLEEARTH_LANDING}"
  log "Sample Earth is CC-BY-NC 4.0 (NonCommercial). Download the points file from"
  log "  ${SAMPLEEARTH_LANDING}"
  log "then filter to coffee/cocoa land-cover classes and AOI bbox into"
  log "  ${OUT_DIR}/sample_earth_coffee_points.geojson"
  log "(NOT auto-downloaded: licence requires attribution + non-commercial use.)"
}

# =============================================================================
# 5) FDaP coffee commodity model 2025b (OPTIONAL 4th convergence axis)
#    Commodity-presence probability COGs. Licence: CC-BY 4.0 (2025b COGs; NOT the
#    2025a CC-BY-NC EE model). GCS path is REQUESTER-PAYS: you pay egress, so
#    download ONCE and cache. Requires gcloud auth + a billing project.
#    Bucket: gs://fdap_coffee/model_2025b/  (requester-pays)
# =============================================================================
FDAP_GCS_PATH="gs://fdap_coffee/model_2025b/"
fetch_fdap() {
  log "FDaP coffee model_2025b lives at ${FDAP_GCS_PATH} (REQUESTER-PAYS)."
  log "Download ONCE with billing enabled, e.g.:"
  log "  gsutil -u <YOUR_BILLING_PROJECT> cp '${FDAP_GCS_PATH}<aoi_tile>.tif' ${RAW_DIR}/"
  log "then clip with clip_to_cog. Skipped by default to avoid surprise egress charges."
}

# --- driver ------------------------------------------------------------------
main() {
  log "AOI: ${AOI_XMIN} ${AOI_YMIN} ${AOI_XMAX} ${AOI_YMAX} (EPSG:4326)"
  log "Output: ${OUT_DIR} (committed synthetic fixtures are NOT touched)"
  fetch_hansen
  fetch_jrc
  fetch_worldcover
  fetch_sample_earth
  fetch_fdap
  log "done."
}

# Only run when executed directly -- safe to `source` for the function defs.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
