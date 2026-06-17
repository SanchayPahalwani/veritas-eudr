-- fn_area_hectares(g geometry)
--
-- Plot area in hectares, computed two independent ways so callers can report the
-- measured disagreement (relevant to the 4 ha submission-format boundary, where a
-- borderline area must escalate to NEEDS_REVIEW):
--
--   geography_ha  -- AUTHORITATIVE. ST_Area(g::geography)/1e4. Casting a 4326
--                    geometry to geography makes ST_Area geodesic (on the WGS84
--                    spheroid), in square metres; /1e4 -> hectares.
--   epsg6933_ha   -- CROSS-CHECK. ST_Area(ST_Transform(g,6933))/1e4. EPSG:6933
--                    is the NSIDC EASE-Grid 2.0 global equal-area projection;
--                    a planar area in an authalic projection, also in m^2 -> ha.
--   delta_frac    -- |geography_ha - epsg6933_ha| / geography_ha; the relative
--                    disagreement between the two methods (0 == perfect agreement).
--
-- For a small plot in the Vietnam Central Highlands (~12.67N, mid-latitude) the
-- two agree to well within 0.1%. A large delta signals a degenerate/huge geometry
-- and should itself be a review trigger.
--
-- Idempotent: CREATE OR REPLACE. IMMUTABLE-ish but marked STABLE because the SRID
-- transform path consults the spatial_ref_sys catalog.

CREATE OR REPLACE FUNCTION fn_area_hectares(g geometry)
RETURNS TABLE (
    geography_ha  double precision,
    epsg6933_ha   double precision,
    delta_frac    double precision
)
LANGUAGE sql
STABLE
AS $$
    WITH m AS (
        SELECT
            ST_Area(g::geography) / 1e4                  AS geography_ha,
            ST_Area(ST_Transform(g, 6933)) / 1e4         AS epsg6933_ha
    )
    SELECT
        m.geography_ha,
        m.epsg6933_ha,
        CASE
            WHEN m.geography_ha = 0 THEN 0::double precision
            ELSE abs(m.geography_ha - m.epsg6933_ha) / m.geography_ha
        END AS delta_frac
    FROM m;
$$;

COMMENT ON FUNCTION fn_area_hectares(geometry) IS
    'Plot area in hectares: authoritative geodesic (geography) + EPSG:6933 '
    'equal-area cross-check + their relative delta_frac, for 4 ha boundary review.';
