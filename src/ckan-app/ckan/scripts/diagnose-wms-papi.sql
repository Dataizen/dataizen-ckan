-- Diagnostic WMS PAPI - Commandes SQL à exécuter dans le container CKAN
-- Table: res_bfe3499b_91e6_4e1e_a872_4276115429d5

-- 1. Vérifier le SRID réel des géométries
\echo '=== 1. SRID RÉEL DES GÉOMÉTRIES ==='
SELECT DISTINCT ST_SRID(the_geom) as srid_geom
FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5"
WHERE the_geom IS NOT NULL;

-- 2. Vérifier le SRID dans geometry_columns
\echo ''
\echo '=== 2. SRID DANS GEOMETRY_COLUMNS ==='
SELECT srid, f_geometry_column, type
FROM geometry_columns 
WHERE f_table_name = 'res_bfe3499b_91e6_4e1e_a872_4276115429d5';

-- 3. Compter les enregistrements et géométries
\echo ''
\echo '=== 3. NOMBRE D''ENREGISTREMENTS ==='
SELECT 
    COUNT(*) as total,
    COUNT(CASE WHEN the_geom IS NULL THEN 1 END) as null_geom,
    COUNT(CASE WHEN the_geom IS NOT NULL THEN 1 END) as with_geom
FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5";

-- 4. Vérifier la validité des géométries
\echo ''
\echo '=== 4. VALIDITÉ DES GÉOMÉTRIES ==='
SELECT 
    COUNT(*) as total,
    COUNT(CASE WHEN the_geom IS NOT NULL AND ST_IsValid(the_geom) THEN 1 END) as valid,
    COUNT(CASE WHEN the_geom IS NOT NULL AND NOT ST_IsValid(the_geom) THEN 1 END) as invalid
FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5"
WHERE the_geom IS NOT NULL;

-- 5. Calculer l'extent réel des données (en 4326)
\echo ''
\echo '=== 5. EXTENT RÉEL DES DONNÉES (EPSG:4326) ==='
SELECT 
    ST_Extent(ST_Transform(the_geom, 4326)) as extent_4326,
    ST_XMin(ST_Extent(ST_Transform(the_geom, 4326))) as minx,
    ST_YMin(ST_Extent(ST_Transform(the_geom, 4326))) as miny,
    ST_XMax(ST_Extent(ST_Transform(the_geom, 4326))) as maxx,
    ST_YMax(ST_Extent(ST_Transform(the_geom, 4326))) as maxy
FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5"
WHERE the_geom IS NOT NULL;

-- 6. Comparer avec l'extent du mapfile
\echo ''
\echo '=== 6. COMPARAISON AVEC EXTENT DU MAPFILE ==='
\echo 'Extent mapfile: 2.2409093196594845 45.7074812123406 5.71568897114573 48.66411372339638'
\echo 'BBOX requête: 45.70750000000000313,2.240909999999999958,48.66409999999999769,5.715690000000000381'

-- 7. Vérifier quelques enregistrements
\echo ''
\echo '=== 7. ÉCHANTILLON DES DONNÉES (5 premiers) ==='
SELECT 
    _id,
    ST_GeometryType(the_geom) as geom_type,
    ST_SRID(the_geom) as srid,
    ST_IsValid(the_geom) as is_valid,
    ST_AsText(ST_Transform(the_geom, 4326)) as geom_wkt_4326
FROM "res_bfe3499b_91e6_4e1e_a872_4276115429d5"
WHERE the_geom IS NOT NULL
LIMIT 5;
