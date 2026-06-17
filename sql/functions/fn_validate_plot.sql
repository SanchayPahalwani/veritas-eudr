-- fn_validate_plot(g geometry)
--
-- Single authoritative validity check + repair for an incoming plot geometry.
-- Returns a composite row so callers (and the JSONB plot_results.validation_report)
-- can record the full picture in one pass:
--   is_valid  -- ST_IsValid(g): was the input OGC-valid as submitted?
--   reason    -- ST_IsValidReason(g): human-readable reason (e.g. 'Self-intersection')
--   location  -- ST_IsValidDetail(g).location: the failing coordinate (geometry POINT)
--   repaired  -- ST_MakeValid(g, 'method=structure'): the repaired geometry
--
-- method=structure (PostGIS >= 3.2, GEOS >= 3.10) is the structure-preserving
-- repair: it removes self-intersections by rebuilding the polygonal structure
-- rather than node-and-dissolve, which is the right behaviour for smallholder
-- boundary digitization errors. The version floor is enforced by 00_assert_floors.sql.
--
-- Idempotent: CREATE OR REPLACE. STABLE (depends only on the argument), no I/O.

CREATE OR REPLACE FUNCTION fn_validate_plot(g geometry)
RETURNS TABLE (
    is_valid  boolean,
    reason    text,
    location  geometry,
    repaired  geometry
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        ST_IsValid(g)                              AS is_valid,
        ST_IsValidReason(g)                        AS reason,
        (ST_IsValidDetail(g)).location             AS location,
        ST_MakeValid(g, 'method=structure')        AS repaired;
$$;

COMMENT ON FUNCTION fn_validate_plot(geometry) IS
    'OGC validity check + structure-preserving repair (PostGIS>=3.2/GEOS>=3.10). '
    'Returns is_valid, reason, failing location, and the ST_MakeValid repaired geometry.';
