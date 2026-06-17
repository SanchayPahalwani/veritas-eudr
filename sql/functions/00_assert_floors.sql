-- Assert the PostGIS / GEOS version floors required by this system.
--
-- ST_MakeValid(geom, 'method=structure') -- which fn_validate_plot relies on to
-- repair invalid smallholder polygons -- requires PostGIS >= 3.2 AND GEOS >= 3.10.
-- Running the rest of the migration against an older stack would silently produce
-- a different (or failing) repair, so we fail loudly here instead.
--
-- Version strings are compared as text. PostGIS/GEOS report dotted versions like
-- '3.4.2'; string_to_array(..., '.')::int[] gives a numeric, component-wise
-- comparison that is correct across the 3.2 -> 3.10 -> 3.4 ordering (where naive
-- text comparison would wrongly rank '3.10' < '3.2').
DO $$
DECLARE
    lib_ver   text := PostGIS_Lib_Version();
    geos_ver  text := PostGIS_GEOS_Version();
    -- PostGIS_GEOS_Version() can append build metadata, e.g. '3.11.0-CAPI-1.17.0';
    -- take the leading dotted numeric portion only.
    geos_num  text := split_part(geos_ver, '-', 1);
    lib_arr   int[] := (string_to_array(lib_ver, '.'))[1:2]::int[];
    geos_arr  int[] := (string_to_array(geos_num, '.'))[1:2]::int[];
BEGIN
    IF lib_arr < ARRAY[3, 2]::int[] THEN
        RAISE EXCEPTION
            'PostGIS >= 3.2 required for ST_MakeValid(method=structure); found %',
            lib_ver;
    END IF;

    IF geos_arr < ARRAY[3, 10]::int[] THEN
        RAISE EXCEPTION
            'GEOS >= 3.10 required for ST_MakeValid(method=structure); found %',
            geos_ver;
    END IF;

    RAISE NOTICE 'version floors OK: PostGIS % / GEOS %', lib_ver, geos_ver;
END
$$;
